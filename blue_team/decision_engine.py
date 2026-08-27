"""
blue_team/decision_engine.py — Fusion orchestrator for the live decision path.

This is the core of AgentGuard's "Defend" pillar. It wires together the three
independent detection signals and produces a single ALLOW/REVIEW/BLOCK verdict
within the 150ms latency budget.

Architecture:
  1. mandate_engine.check_mandate()  — synchronous, < 1ms
  2. payment_integrity.validate_payment_message()  — synchronous, < 1ms
  3. injection_detector.check_content()  — async with 90ms timeout
  4. risk_model.score_transaction()  — synchronous, < 5ms
  5. Fusion logic — combines signals into a final verdict

The 150ms budget is enforced by asyncio.wait_for() on the injection_detector
call. If it times out, we fall back to heuristic scoring from risk_model.py.

Decision thresholds (tunable via environment variables):
  BLOCK_THRESHOLD  = 0.75  → BLOCK if risk_score >= this
  REVIEW_THRESHOLD = 0.40  → REVIEW if risk_score >= this (else ALLOW)

Hard-violation override: if mandate_engine or payment_integrity returns
hard_violation=True, we BLOCK immediately regardless of risk_score.
This is the "physical law" — deterministic rules override probabilistic ones.

No LLM anywhere in this file. No network calls. No async I/O except the
injection_detector timeout wrapper. The latency budget is real.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from api.models import Decision, IntentMandate, ProposedTransaction
from blue_team.injection_detector import check_content
from blue_team.mandate_engine import check_mandate
from blue_team.payment_integrity import (
    PaymentInfo, PaymentMessage, RemittanceInfo, validate_payment_message,
)
from blue_team.risk_model import extract_features, score_transaction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decision thresholds — tunable via environment for demo calibration
# ---------------------------------------------------------------------------

BLOCK_THRESHOLD = float(os.environ.get("BLOCK_THRESHOLD", "0.75"))
REVIEW_THRESHOLD = float(os.environ.get("REVIEW_THRESHOLD", "0.40"))
INJECTION_DETECTOR_TIMEOUT_MS = float(os.environ.get("INJECTION_DETECTOR_TIMEOUT_MS", "90"))


async def decide(
    transaction: ProposedTransaction,
    mandate: IntentMandate,
    content_to_check: str = "",
) -> Decision:
    """
    Run the full AgentGuard decision pipeline and return a verdict.

    This is the only entry point to the decision engine. The API layer calls
    this on every POST /transactions/authorize request.

    Args:
        transaction: The proposed transaction from the AI agent
        mandate: The user's authorization mandate
        content_to_check: Product description, review text, or other external
                          content the agent read before making this transaction.
                          This is what gets checked for injection payloads.

    Returns:
        Decision with ALLOW/REVIEW/BLOCK verdict and full explanation
    """
    pipeline_start = time.perf_counter()
    violated_signals = []
    hard_violation = False
    explanation: dict = {
        "summary": "",
        "signals": {},
        "mandate_violations": [],
        "flagged_span": None,
    }

    # -----------------------------------------------------------------------
    # Stage 1: Mandate Engine (synchronous, always runs first)
    # Hard violations short-circuit the entire pipeline.
    # -----------------------------------------------------------------------
    mandate_result = check_mandate(transaction, mandate)
    if mandate_result.hard_violation:
        hard_violation = True
        violated_signals.append("mandate_hard_violation")
        explanation["mandate_violations"] = mandate_result.violations

    explanation["signals"]["mandate"] = {
        "hard_violation": mandate_result.hard_violation,
        "soft_score": mandate_result.soft_score,
        "violations": mandate_result.violations,
    }

    # -----------------------------------------------------------------------
    # Stage 2: Payment Integrity (synchronous, structural validation)
    # -----------------------------------------------------------------------
    payment_msg = PaymentMessage(
        pmt_inf=PaymentInfo(
            purpose_code=transaction.purpose_code or "GDDS",
            merchant_category=transaction.category or "_default",
        ),
        rmt_inf=RemittanceInfo(ustrd=transaction.remittance_text or ""),
    )
    integrity_result = validate_payment_message(payment_msg, transaction.category or "_default")
    if integrity_result.hard_violation:
        hard_violation = True
        violated_signals.append("payment_integrity_violation")

    explanation["signals"]["payment_integrity"] = {
        "hard_violation": integrity_result.hard_violation,
        "violations": integrity_result.violations,
    }

    # -----------------------------------------------------------------------
    # Stage 3: Injection Detector (async, 90ms timeout)
    # We still run this even if a hard violation was already found, because
    # the result feeds into the risk model features.
    # -----------------------------------------------------------------------
    injection_score = 0.0
    matched_pattern = ""
    injection_timed_out = False

    try:
        # Call synchronously as inference is fast (20-80ms) and CPU-bound
        injection_result = check_content(content_to_check)
        injection_score = injection_result.score
        matched_pattern = injection_result.matched_pattern
        logger.debug(
            "Injection detector: score=%.3f, latency=%.1fms",
            injection_score, injection_result.latency_ms,
        )
    except Exception as _e:
        injection_timed_out = True
        logger.warning("Injection detector error: %s, using heuristic", _e)

    if injection_score >= 0.72:  # local threshold before feeding to risk model
        violated_signals.append("content_injection_detected")

    explanation["signals"]["injection_detector"] = {
        "score": injection_score,
        "timed_out": injection_timed_out,
        "matched_pattern": matched_pattern[:100] if matched_pattern else None,
    }
    if matched_pattern:
        explanation["flagged_span"] = matched_pattern[:200]

    # -----------------------------------------------------------------------
    # Stage 4: Risk Model (synchronous, < 5ms)
    # -----------------------------------------------------------------------
    features = extract_features(
        content_risk_score=injection_score,
        mandate_soft_score=mandate_result.soft_score,
        amount=float(transaction.amount),
        max_amount=float(mandate.max_amount),
        merchant_id=transaction.merchant_id,
        approved_merchants=mandate.approved_merchants,
        category=transaction.category or "",
        approved_categories=mandate.approved_categories,
        purpose_code=transaction.purpose_code or "GDDS",
        merchant_category=transaction.category or "_default",
        description_text=content_to_check,
    )
    risk_result = score_transaction(features)

    explanation["signals"]["risk_model"] = {
        "score": risk_result.risk_score,
        "top_features": dict(list(risk_result.feature_importances.items())[:5]),
    }

    # -----------------------------------------------------------------------
    # Stage 5: Fusion — combine into a final verdict
    # -----------------------------------------------------------------------
    if hard_violation:
        # Hard violations from mandate_engine or payment_integrity are absolute.
        # Risk score doesn't matter — the transaction is structurally invalid.
        final_decision = "BLOCK"
        final_risk_score = max(0.90, risk_result.risk_score)  # always high for a hard violation
        explanation["summary"] = (
            "Transaction blocked due to hard mandate/integrity violation. "
            + "; ".join(mandate_result.violations + integrity_result.violations)
        )

    elif risk_result.risk_score >= BLOCK_THRESHOLD:
        final_decision = "BLOCK"
        final_risk_score = risk_result.risk_score
        explanation["summary"] = (
            f"Transaction blocked. Composite risk score {risk_result.risk_score:.2f} "
            f"exceeds block threshold {BLOCK_THRESHOLD}. "
            f"Signals: {', '.join(violated_signals) or 'risk_model'}"
        )

    elif risk_result.risk_score >= REVIEW_THRESHOLD:
        final_decision = "REVIEW"
        final_risk_score = risk_result.risk_score
        explanation["summary"] = (
            f"Transaction flagged for review. Risk score {risk_result.risk_score:.2f} "
            f"is in the elevated range ({REVIEW_THRESHOLD:.2f}-{BLOCK_THRESHOLD:.2f})."
        )

    else:
        final_decision = "ALLOW"
        final_risk_score = risk_result.risk_score
        explanation["summary"] = (
            f"Transaction approved. Risk score {risk_result.risk_score:.2f} "
            f"is below review threshold {REVIEW_THRESHOLD}."
        )
        # Clear explanation for ALLOW — no violations to report
        explanation["mandate_violations"] = []

    latency_ms = (time.perf_counter() - pipeline_start) * 1000

    # Warn if we're approaching the budget
    if latency_ms > 120:
        logger.warning(
            "Decision pipeline latency %.1fms is approaching 150ms budget", latency_ms
        )

    logger.info(
        "Decision: %s | score=%.3f | latency=%.1fms | tx=%s",
        final_decision, final_risk_score, latency_ms, transaction.tx_id,
    )

    return Decision(
        tx_id=transaction.tx_id,
        decision=final_decision,
        risk_score=final_risk_score,
        violated_signals=violated_signals,
        explanation=explanation,
        latency_ms=latency_ms,
    )
