"""
simulation/payment_rail.py — Mock payment settlement rail.

Simulates the downstream payment network that executes a transaction after
AgentGuard's ALLOW decision. In production, this would call the actual
payment network API (Mastercard, UPI, SEPA, etc.).

Key constraint: settlement MUST only be called after an ALLOW decision.
This is enforced both in this module (raises if called with a non-ALLOW
decision) and in api/main.py (which checks before calling).

All settlements are mock — no real money moves, no real API calls.
Settlement results include a simulated latency to make the demo realistic.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from api.models import Decision, ProposedTransaction


@dataclass
class SettlementResult:
    """Result of a payment settlement attempt."""
    settlement_id: str
    tx_id: str
    status: str          # "SETTLED" | "FAILED" | "PENDING"
    amount: float
    currency: str
    merchant_id: str
    settled_at: str
    rail_latency_ms: float


async def settle(
    transaction: ProposedTransaction,
    decision: Decision,
) -> SettlementResult:
    """
    Simulate submitting a payment to the settlement rail.

    This is an async function because in production the real API call would
    be async. Here we just simulate latency.

    Raises:
        ValueError: If the decision is not ALLOW. This enforces the invariant
                    that we never settle a blocked/reviewed transaction.
    """
    if decision.decision != "ALLOW":
        raise ValueError(
            f"Cannot settle transaction {transaction.tx_id}: "
            f"AgentGuard decision was '{decision.decision}', not 'ALLOW'. "
            "Settlement is only permitted for approved transactions."
        )

    # Simulate network latency (20-80ms realistic for a payment rail)
    simulated_latency_ms = random.uniform(20, 80)
    await asyncio.sleep(simulated_latency_ms / 1000)

    # Simulate occasional rail failures (5% failure rate — realistic for demo)
    if random.random() < 0.05:
        return SettlementResult(
            settlement_id=str(uuid.uuid4()),
            tx_id=str(transaction.tx_id),
            status="FAILED",
            amount=float(transaction.amount),
            currency="INR",  # default for demo
            merchant_id=transaction.merchant_id,
            settled_at=datetime.now(timezone.utc).isoformat(),
            rail_latency_ms=simulated_latency_ms,
        )

    return SettlementResult(
        settlement_id=str(uuid.uuid4()),
        tx_id=str(transaction.tx_id),
        status="SETTLED",
        amount=float(transaction.amount),
        currency="INR",
        merchant_id=transaction.merchant_id,
        settled_at=datetime.now(timezone.utc).isoformat(),
        rail_latency_ms=simulated_latency_ms,
    )
