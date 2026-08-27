"""
blue_team/mandate_engine.py — Deterministic mandate compliance checker.

This is intentionally the simplest and most boring module in the project.
No async, no I/O, no model calls — just pure in-memory logic comparing
a proposed transaction against the user's mandate.

Why so simple? Because this layer is supposed to be the thing that never
lies, never times out, and can never be fooled by clever prompt engineering.
An LLM might be tricked into thinking a transaction is fine; a hardcoded
numeric comparison cannot be.

The soft score handles the grey zone: a transaction that is technically
within bounds but suspiciously close to the limit still gets a risk signal
that the fusion engine can weight appropriately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from api.models import IntentMandate, ProposedTransaction


@dataclass
class MandateCheckResult:
    """
    Result of checking a transaction against a mandate.

    hard_violation = True means this transaction MUST be blocked,
    regardless of what the other detectors say.
    soft_score is a 0-1 risk contribution for borderline cases.
    violations is the list of human-readable reasons shown in the UI.
    """
    hard_violation: bool = False
    soft_score: float = 0.0
    violations: list[str] = field(default_factory=list)


def check_mandate(
    transaction: ProposedTransaction,
    mandate: IntentMandate,
) -> MandateCheckResult:
    """
    Check whether a proposed transaction complies with its mandate.

    This function has zero side effects and is safe to call from anywhere.
    It runs fast enough that even the synchronous path is fine — the
    decision engine runs it with a 5ms timeout just for safety, but
    realistically this returns in microseconds.

    Prompt 18 adds the agent_id check: the transaction's agent_id must
    match the mandate's actual owning agent, not just the mandate_id.
    This catches the Agent Identity Spoofing vector.
    """
    result = MandateCheckResult()

    # --- 1. Check mandate expiry ---
    if mandate.is_expired():
        result.hard_violation = True
        result.violations.append(
            f"Mandate {mandate.mandate_id} expired at "
            f"{mandate.expires_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # --- 2. Agent identity check (Prompt 18 addition) ---
    # The transaction claims to operate under this mandate, but is the
    # submitting agent actually the mandate's owner?
    if str(transaction.agent_id) != str(mandate.agent_id):
        result.hard_violation = True
        result.violations.append(
            f"agent_id {transaction.agent_id} does not match "
            f"mandate owner {mandate.agent_id} — possible identity spoofing"
        )

    # --- 3. Amount check ---
    if transaction.amount > mandate.max_amount:
        result.hard_violation = True
        result.violations.append(
            f"Amount {transaction.amount:.2f} {mandate.currency} "
            f"exceeds mandate limit {mandate.max_amount:.2f} {mandate.currency}"
        )
    else:
        # Soft score: how close to the limit are we?
        # 0% of limit → 0.0, 90% → 0.1, 95% → 0.3, 99% → 0.7, 100%+ → 1.0
        ratio = transaction.amount / mandate.max_amount
        if ratio >= 0.99:
            result.soft_score = max(result.soft_score, 0.7)
        elif ratio >= 0.95:
            result.soft_score = max(result.soft_score, 0.3)
        elif ratio >= 0.90:
            result.soft_score = max(result.soft_score, 0.1)

    # --- 4. Merchant check ---
    if mandate.approved_merchants and transaction.merchant_id not in mandate.approved_merchants:
        result.hard_violation = True
        result.violations.append(
            f"Merchant '{transaction.merchant_id}' is not in the approved merchant list"
        )

    # --- 5. Purpose code check ---
    if mandate.purpose_code_allowlist and transaction.purpose_code not in mandate.purpose_code_allowlist:
        result.hard_violation = True
        result.violations.append(
            f"Purpose code '{transaction.purpose_code}' is not in the mandate's "
            f"allowlist: {mandate.purpose_code_allowlist}"
        )

    # --- 6. Category check (soft only — agents sometimes browse multiple categories) ---
    if mandate.approved_categories and transaction.category:
        if transaction.category not in mandate.approved_categories:
            # Not a hard violation by itself — risk model may have more context.
            # But it's worth flagging as elevated risk.
            result.soft_score = max(result.soft_score, 0.25)
            result.violations.append(
                f"Category '{transaction.category}' is outside the mandate's "
                f"approved categories {mandate.approved_categories} — elevated risk"
            )

    return result
