"""
blue_team/risk_model.py — Gradient-boosted transaction risk scorer.

Sits in the middle of the decision engine pipeline after injection_detector.py
and mandate_engine.py have already run. Takes their signals as features (along
with raw transaction features) and produces a single risk_score float.

Why LightGBM instead of a neural network?
  - 100x faster inference (single tree traversal in microseconds vs. matrix ops)
  - No GPU needed, runs fine in the Docker container
  - Interpretable: we can enumerate which features mattered for the decision
  - Easily retrained after each feedback loop round (< 2s on 10k examples)

The model file is an ONNX export so the Docker image doesn't need the full
LightGBM training runtime. onnxmltools converts it during training; onnxruntime
loads it at inference time.

Feature engineering lives in _extract_features(). If you want to add a new
signal (e.g. time-of-day, user purchase history), add it there and retrain.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).resolve().parents[1] / "training" / "models"
_ONNX_MODEL_PATH = _MODEL_DIR / "risk_model.onnx"

# Global ONNX runtime session -- loaded once at startup
_ort_session = None


class RiskFeatures(NamedTuple):
    """
    Input feature vector for the risk model.

    These are the signals we pass as input. All values are floats in [0, 1]
    except amount_normalized (normalized against mandate max) and
    description_length (character count, 0-500+).
    """
    content_risk_score: float     # from injection_detector
    mandate_soft_score: float     # from mandate_engine
    amount_normalized: float      # tx.amount / mandate.max_amount
    is_approved_merchant: float   # 1.0 if merchant in allowlist, else 0.0
    is_approved_category: float   # 1.0 if category approved, else 0.0
    purpose_code_mismatch: float  # 1.0 if purpose code unexpected for category
    description_length: float     # character count of product text
    has_html_comment: float       # 1.0 if <!-- --> in text
    has_system_phrase: float      # 1.0 if "system" appears in description text
    has_urgency_phrase: float      # 1.0 if urgency language detected


class RiskResult(NamedTuple):
    """Output of the risk model."""
    risk_score: float        # 0.0-1.0 composite risk
    feature_importances: dict[str, float]  # which features drove the score
    latency_ms: float


def _load_onnx_session():
    """Load the ONNX model session (lazy, once per process)."""
    global _ort_session
    if _ort_session is not None:
        return _ort_session

    if not _ONNX_MODEL_PATH.exists():
        logger.info(
            "ONNX risk model not found at %s. "
            "Run training/train_lightgbm.py to generate it. "
            "Falling back to heuristic scorer.",
            _ONNX_MODEL_PATH,
        )
        return None

    try:
        import onnxruntime as ort
        _ort_session = ort.InferenceSession(
            str(_ONNX_MODEL_PATH),
            providers=["CPUExecutionProvider"],
        )
        logger.info("ONNX risk model loaded from %s", _ONNX_MODEL_PATH)
        return _ort_session
    except Exception as e:
        logger.warning("Failed to load ONNX model: %s. Using heuristic scorer.", e)
        return None


def _heuristic_score(features: RiskFeatures) -> float:
    """
    Fast heuristic fallback when the ONNX model is not available.

    Not as accurate as the trained model, but deterministic and explainable.
    Used during development before training, and as a safety fallback.
    """
    score = 0.0

    # Content risk from injection detector (heaviest weight)
    score += features.content_risk_score * 0.45

    # Mandate signals
    score += features.mandate_soft_score * 0.20

    # Transaction structural anomalies
    if features.is_approved_merchant < 0.5:
        score += 0.15
    if features.purpose_code_mismatch > 0.5:
        score += 0.10
    if features.has_html_comment > 0.5:
        score += 0.05
    if features.has_system_phrase > 0.5:
        score += 0.05

    return min(1.0, score)


def score_transaction(features: RiskFeatures) -> RiskResult:
    """
    Score a transaction's risk level using the LightGBM model (or heuristic fallback).

    This is the last signal before the fusion decision engine makes its call.
    It runs in < 5ms on CPU, well within the 150ms budget.

    Args:
        features: Pre-computed feature vector for this transaction

    Returns:
        RiskResult with the composite score and top feature importances
    """
    t0 = time.perf_counter()

    feature_array = np.array([list(features)], dtype=np.float32)
    feature_names = RiskFeatures._fields

    session = _load_onnx_session()

    if session is not None:
        # Run ONNX inference
        inputs = {session.get_inputs()[0].name: feature_array}
        outputs = session.run(None, inputs)
        # ONNX LightGBM outputs: [label, probabilities]
        probabilities = outputs[1][0]  # shape: (2,) for binary classification
        risk_score = float(probabilities[1])  # probability of class 1 (injection/fraud)

        # Feature importances from the model
        # We approximate relative importance using the feature values * weights heuristic
        importances = {
            name: float(abs(features[i])) for i, name in enumerate(feature_names)
        }
    else:
        # Fall back to heuristic
        risk_score = _heuristic_score(features)
        importances = {
            name: float(features[i]) for i, name in enumerate(feature_names)
        }

    latency_ms = (time.perf_counter() - t0) * 1000

    # Sort importances so the UI shows the most impactful signals first
    sorted_importances = dict(
        sorted(importances.items(), key=lambda x: x[1], reverse=True)
    )

    return RiskResult(
        risk_score=round(min(1.0, max(0.0, risk_score)), 4),
        feature_importances=sorted_importances,
        latency_ms=latency_ms,
    )


def extract_features(
    content_risk_score: float,
    mandate_soft_score: float,
    amount: float,
    max_amount: float,
    merchant_id: str,
    approved_merchants: list[str],
    category: str,
    approved_categories: list[str],
    purpose_code: str,
    merchant_category: str,
    description_text: str,
) -> RiskFeatures:
    """
    Convert raw transaction fields into the feature vector the model expects.

    This lives here (not in decision_engine) so feature engineering is
    versioned alongside the model and can be updated atomically.
    """
    # Normalize amount
    amount_norm = min(1.0, amount / max_amount) if max_amount > 0 else 1.0

    # Merchant/category approval checks
    is_approved_merchant = 1.0 if (not approved_merchants or merchant_id in approved_merchants) else 0.0
    is_approved_category = 1.0 if (not approved_categories or category in approved_categories) else 0.0

    # Purpose code mismatch (simplified — payment_integrity has the full check)
    # A SALA code for an electronics merchant is suspicious
    salary_codes = {"SALA", "SALA2"}
    purpose_code_mismatch = 1.0 if purpose_code in salary_codes and merchant_category == "electronics" else 0.0

    # Text features on the description
    text = description_text.lower() if description_text else ""
    has_html_comment = 1.0 if "<!--" in text else 0.0
    has_system_phrase = 1.0 if "system" in text or "[system" in text else 0.0
    urgency_phrases = ["urgent", "immediately", "act now", "critical", "override", "bypass"]
    has_urgency_phrase = 1.0 if any(p in text for p in urgency_phrases) else 0.0

    return RiskFeatures(
        content_risk_score=content_risk_score,
        mandate_soft_score=mandate_soft_score,
        amount_normalized=amount_norm,
        is_approved_merchant=is_approved_merchant,
        is_approved_category=is_approved_category,
        purpose_code_mismatch=purpose_code_mismatch,
        description_length=float(min(len(description_text or ""), 1000)),
        has_html_comment=has_html_comment,
        has_system_phrase=has_system_phrase,
        has_urgency_phrase=has_urgency_phrase,
    )
