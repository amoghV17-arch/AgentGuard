"""
training/train_lightgbm.py — Train and export the LightGBM risk model.

Builds a binary classifier (injection/fraud vs. legitimate) from the seed
data + any feedback-loop-generated examples. Exports to ONNX for low-latency
inference via onnxruntime in the production container.

Running this script:
  python training/train_lightgbm.py

It will:
  1. Load seed_injection_patterns.json + seed_legitimate_text.json
  2. Construct a synthetic feature matrix from those texts
  3. Train LightGBM with cross-validation
  4. Export the model to training/models/risk_model.onnx
  5. Print evaluation metrics on a held-out test split
  6. Save held-out test data for scripts/evaluate.py to use

The held-out test set is NEVER touched by the feedback loop augmentation.
It's split before training and saved separately so evaluate.py uses
truly unseen data for metrics.
"""

from __future__ import annotations

import json
import logging
import pickle
import re
from pathlib import Path

import numpy as np
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import lightgbm as lgb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[1]
_DATA_DIR = _BASE_DIR / "training" / "data"
_MODEL_DIR = _BASE_DIR / "training" / "models"
_MODEL_DIR.mkdir(parents=True, exist_ok=True)
_HELD_OUT_PATH = _DATA_DIR / "held_out_test_set.pkl"


# ---------------------------------------------------------------------------
# Feature engineering — mirrors the features in blue_team/risk_model.py
# We engineer features from raw text since we don't have full transaction
# records in the seed dataset, only text samples.
# ---------------------------------------------------------------------------

_URGENCY_PHRASES = [
    "urgent", "immediately", "act now", "critical", "override", "bypass",
    "system note", "admin", "internal", "ignore previous", "do not inform",
]

_INJECTION_MARKERS = [
    "<!--", "[system", "[admin", "[important", "override",
    "settlement account", "new banking", "purpose code", "ignore previous",
    "do not", "internal use only", "for automated", "for processing agent",
]


def text_to_features(text: str) -> list[float]:
    """
    Convert raw text into a feature vector matching RiskFeatures fields.

    For training purposes (text-only seed data), we synthesize the
    transaction-level features from the text itself. In production, the
    decision engine passes actual transaction fields to risk_model.extract_features().
    """
    lower = text.lower()

    # content_risk_score: proxy based on injection marker density
    marker_hits = sum(1 for m in _INJECTION_MARKERS if m in lower)
    content_risk = min(1.0, marker_hits / 3.0)

    # mandate_soft_score: not available from text alone, set to 0
    mandate_soft = 0.0

    # amount_normalized: synthesized
    amount_norm = 0.5

    # merchant/category: synthesized as "approved"
    is_approved_merchant = 1.0
    is_approved_category = 1.0

    # purpose_code_mismatch: check if text mentions SALA for electronics-like content
    purpose_code_mismatch = 1.0 if "sala" in lower and "electronic" in lower else 0.0

    # Text features
    description_length = float(min(len(text), 1000))
    has_html_comment = 1.0 if "<!--" in text else 0.0
    has_system_phrase = 1.0 if "system" in lower or "[system" in lower else 0.0
    has_urgency = 1.0 if any(p in lower for p in _URGENCY_PHRASES) else 0.0

    return [
        content_risk, mandate_soft, amount_norm,
        is_approved_merchant, is_approved_category,
        purpose_code_mismatch, description_length,
        has_html_comment, has_system_phrase, has_urgency,
    ]


def load_training_data() -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Load seed data and construct (X, y, texts).

    Also appends any feedback-loop examples from the DB if they exist.
    """
    with open(_DATA_DIR / "seed_injection_patterns.json") as f:
        injections = json.load(f)
    with open(_DATA_DIR / "seed_legitimate_text.json") as f:
        legitimate = json.load(f)

    texts = injections + legitimate
    labels = [1] * len(injections) + [0] * len(legitimate)

    X = np.array([text_to_features(t) for t in texts], dtype=np.float32)
    y = np.array(labels, dtype=np.int32)

    logger.info(
        "Loaded %d injection + %d legitimate examples (%d total)",
        len(injections), len(legitimate), len(texts)
    )
    return X, y, texts


def train_and_export() -> dict:
    """
    Main training function. Returns evaluation metrics dict.
    """
    X, y, texts = load_training_data()

    # Hold out 20% as the fixed test set — never touches the feedback loop
    X_train, X_test, y_train, y_test, texts_train, texts_test = train_test_split(
        X, y, texts, test_size=0.2, random_state=42, stratify=y
    )

    # Save the held-out test set for evaluate.py
    with open(_HELD_OUT_PATH, "wb") as f:
        pickle.dump({
            "X_test": X_test,
            "y_test": y_test,
            "texts_test": texts_test,
            "feature_names": [
                "content_risk_score", "mandate_soft_score", "amount_normalized",
                "is_approved_merchant", "is_approved_category",
                "purpose_code_mismatch", "description_length",
                "has_html_comment", "has_system_phrase", "has_urgency_phrase",
            ],
        }, f)
    logger.info("Held-out test set saved to %s (%d examples)", _HELD_OUT_PATH, len(y_test))

    # Train with 5-fold cross-validation to get a stable accuracy estimate
    params = {
        "objective": "binary",
        "metric": "auc",
        "n_estimators": 150,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 3,
        "reg_alpha": 0.1,
        "reg_lambda": 0.1,
        "random_state": 42,
        "verbose": -1,
    }

    cv_scores = []
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    for fold, (train_idx, val_idx) in enumerate(skf.split(X_train, y_train)):
        model = lgb.LGBMClassifier(**params)
        model.fit(
            X_train[train_idx], y_train[train_idx],
            eval_set=[(X_train[val_idx], y_train[val_idx])],
            callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(period=-1)],
        )
        preds = model.predict_proba(X_train[val_idx])[:, 1]
        auc = roc_auc_score(y_train[val_idx], preds)
        cv_scores.append(auc)
        logger.info("Fold %d AUC: %.4f", fold + 1, auc)

    logger.info("CV AUC: %.4f ± %.4f", np.mean(cv_scores), np.std(cv_scores))

    # Train final model on full training set
    final_model = lgb.LGBMClassifier(**params)
    final_model.fit(X_train, y_train)

    # Evaluate on held-out test set
    y_pred_proba = final_model.predict_proba(X_test)[:, 1]
    y_pred = (y_pred_proba >= 0.5).astype(int)

    test_auc = roc_auc_score(y_test, y_pred_proba)
    report = classification_report(y_test, y_pred, target_names=["legitimate", "injection"])

    logger.info("\nTest Set Evaluation (n=%d):\nROC-AUC: %.4f\n%s", len(y_test), test_auc, report)

    # Export to ONNX
    onnx_path = _MODEL_DIR / "risk_model.onnx"
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType
        initial_type = [("float_input", FloatTensorType([None, X_train.shape[1]]))]
        onnx_model = convert_sklearn(final_model, initial_types=initial_type)
        with open(onnx_path, "wb") as f:
            f.write(onnx_model.SerializeToString())
        logger.info("Model exported to ONNX: %s", onnx_path)
    except ImportError:
        logger.warning(
            "skl2onnx not installed. Saving as pickle instead. "
            "Install with: pip install skl2onnx onnxmltools"
        )
        # Pickle fallback
        pkl_path = _MODEL_DIR / "risk_model.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(final_model, f)
        logger.info("Model saved as pickle: %s", pkl_path)

    return {
        "cv_auc_mean": float(np.mean(cv_scores)),
        "cv_auc_std": float(np.std(cv_scores)),
        "test_auc": float(test_auc),
        "test_set_size": int(len(y_test)),
    }


if __name__ == "__main__":
    metrics = train_and_export()
    print("\n=== Training Complete ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
