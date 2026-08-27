"""
api/models/__init__.py — Pydantic schemas for AgentGuard's payment mandate flow.

Inspired by Google's AP2 protocol pattern (Intent → Cart → Payment Mandate),
but simplified for the hackathon timeline. The key primitives:

  IntentMandate  → what the user has authorized their AI agent to spend
  ProposedTransaction → what the AI agent is about to submit
  Decision       → what AgentGuard decided (ALLOW / REVIEW / BLOCK)

The HMAC-SHA256 signing on IntentMandate is a simplified stand-in for AP2's
real W3C Verifiable Credential mandates. The upgrade path would be to replace
sign() / verify() with a VC issuance + verification library while keeping
the same interface — nothing else in the codebase would need to change.

All validation is at the Pydantic layer so bad inputs fail fast at the API
boundary, never reaching the decision engine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

# ISO 4217 currency codes — the set we support in this sandbox.
# Keeping it to a realistic subset rather than all 180+ codes.
_VALID_CURRENCIES = {
    "INR", "USD", "EUR", "GBP", "JPY", "CAD", "AUD",
    "SGD", "AED", "CHF", "CNY", "HKD", "MYR", "THB",
}


# ---------------------------------------------------------------------------
# IntentMandate — the user's original authorization
# ---------------------------------------------------------------------------

class IntentMandate(BaseModel):
    """
    The user's spending mandate: what their AI agent is allowed to do.

    This is the ground truth that every proposed transaction is checked against.
    Fields match the mandates table in db/models.py.
    """

    mandate_id: UUID = Field(default_factory=uuid4)
    agent_id: UUID = Field(..., description="Which agent this mandate belongs to")
    max_amount: float = Field(..., gt=0, description="Maximum transaction amount")
    currency: str = Field(..., description="ISO 4217 currency code")
    approved_merchants: list[str] = Field(
        default_factory=list,
        description="Allowlisted merchant IDs. Empty list = no merchant restriction."
    )
    approved_categories: list[str] = Field(
        default_factory=list,
        description="Allowlisted spending categories."
    )
    purpose_code_allowlist: list[str] = Field(
        default_factory=list,
        description="ISO 20022 purpose codes the agent may use."
    )
    expires_at: datetime = Field(
        ...,
        description="Mandate expiry. Must be timezone-aware."
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in _VALID_CURRENCIES:
            raise ValueError(
                f"'{v}' is not a supported ISO 4217 currency code. "
                f"Supported: {sorted(_VALID_CURRENCIES)}"
            )
        return v

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "expires_at must be timezone-aware. "
                "Use datetime.now(timezone.utc) + timedelta(...) or provide tzinfo."
            )
        return v

    @field_validator("max_amount")
    @classmethod
    def validate_max_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_amount must be positive")
        return round(v, 2)

    def is_expired(self) -> bool:
        """Quick expiry check used by mandate_engine and the sign/verify flow."""
        return datetime.now(timezone.utc) > self.expires_at

    # --- HMAC signing ---
    # This is a simplified stand-in for AP2's W3C Verifiable Credential mandates.
    # We sign over a canonical JSON serialization of the mandate fields so that
    # any tampering with any field (including amounts, merchants, or expiry)
    # invalidates the signature.

    def _canonical_json(self) -> str:
        """
        Produce a deterministic JSON string for signing.
        Keys are sorted to ensure consistent serialization regardless of
        dict insertion order. UUIDs and datetimes are stringified.
        """
        data = {
            "mandate_id": str(self.mandate_id),
            "agent_id": str(self.agent_id),
            "max_amount": self.max_amount,
            "currency": self.currency,
            "approved_merchants": sorted(self.approved_merchants),
            "approved_categories": sorted(self.approved_categories),
            "purpose_code_allowlist": sorted(self.purpose_code_allowlist),
            "expires_at": self.expires_at.isoformat(),
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def sign(self, secret_key: str | bytes) -> str:
        """
        Produce an HMAC-SHA256 signature over the canonical mandate fields.

        Returns a hex digest string. Store this alongside the mandate and
        pass it with every transaction request for verification.
        """
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        canonical = self._canonical_json().encode("utf-8")
        sig = hmac.new(secret_key, canonical, hashlib.sha256)
        return sig.hexdigest()

    def verify(self, signature: str, secret_key: str | bytes) -> bool:
        """
        Verify that a signature matches the current mandate fields.

        Returns False if ANY field has been tampered with since signing,
        including changing the expiry date.
        """
        expected = self.sign(secret_key)
        # Use hmac.compare_digest to prevent timing attacks
        return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# ProposedTransaction — what the AI agent wants to submit
# ---------------------------------------------------------------------------

class ProposedTransaction(BaseModel):
    """
    A transaction proposed by an AI shopping agent, awaiting AgentGuard's decision.
    """

    tx_id: UUID = Field(default_factory=uuid4)
    agent_id: UUID = Field(..., description="Which agent is submitting this")
    mandate_id: UUID = Field(..., description="Which mandate this transaction claims to operate under")
    amount: float = Field(..., gt=0, description="Transaction amount in the mandate's currency")
    merchant_id: str = Field(..., min_length=1, description="Merchant identifier")
    category: str = Field(default="", description="Merchant category code or label")
    purpose_code: str = Field(default="GDDS", description="ISO 20022 purpose code")
    remittance_text: str = Field(
        default="",
        max_length=140,
        description="Unstructured remittance information (ISO 20022 RmtInf.Ustrd, max 140 chars)"
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Transaction amount must be positive")
        return round(v, 2)

    @field_validator("remittance_text")
    @classmethod
    def validate_remittance_text(cls, v: str) -> str:
        if len(v) > 140:
            raise ValueError(
                f"remittance_text exceeds 140-char ISO 20022 limit "
                f"(got {len(v)} chars)"
            )
        return v


# ---------------------------------------------------------------------------
# Decision — what AgentGuard decided
# ---------------------------------------------------------------------------

class SignalDetail(BaseModel):
    """Score and details from a single detection signal."""
    score: float = Field(..., ge=0.0, le=1.0)
    timed_out: bool = False
    hard_violation: bool = False
    detail: str = ""


class Decision(BaseModel):
    """
    The AgentGuard fusion engine's verdict on a proposed transaction.

    Every BLOCK or REVIEW decision carries a human-readable explanation
    so the dashboard can show the judge exactly why the transaction was stopped.
    The explanation dict structure matches ARCHITECTURE.md section 7.
    """

    decision_id: UUID = Field(default_factory=uuid4)
    tx_id: UUID | None = None
    decision: Literal["ALLOW", "REVIEW", "BLOCK"]
    risk_score: float = Field(..., ge=0.0, le=1.0)
    violated_signals: list[str] = Field(default_factory=list)
    explanation: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Human-readable explanation of the decision. Always populated for "
            "REVIEW/BLOCK. Keys: 'summary' (str), 'signals' (dict of SignalDetail), "
            "'mandate_violations' (list[str]), 'flagged_span' (str|None)."
        )
    )
    latency_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def explanation_required_for_non_allow(self) -> Decision:
        """REVIEW and BLOCK decisions must carry an explanation — this is non-negotiable."""
        if self.decision in ("REVIEW", "BLOCK") and not self.explanation:
            raise ValueError(
                f"A '{self.decision}' decision must include an explanation. "
                "Every BLOCK/REVIEW must be traceable to a human-readable reason."
            )
        return self


# ---------------------------------------------------------------------------
# Lightweight request/response models for the API layer
# ---------------------------------------------------------------------------

class AuthorizeRequest(BaseModel):
    """Body of POST /transactions/authorize"""
    transaction: ProposedTransaction
    mandate_id: UUID
    mandate_signature: str = Field(
        default="",
        description="HMAC-SHA256 signature from IntentMandate.sign()"
    )
    bypass_agentguard: bool = Field(
        default=False,
        description=(
            "Demo comparison mode only. Silently ignored unless "
            "ALLOW_BYPASS_MODE=true in environment."
        )
    )


class AuthorizeResponse(BaseModel):
    """Response from POST /transactions/authorize"""
    decision: Decision
    settled: bool = False
    settlement_id: str | None = None
    settlement_status: str | None = None


class SimulationRunRequest(BaseModel):
    """Body of POST /simulation/run"""
    sub_vector: str = Field(..., description="Threat vector ID from THREAT_TAXONOMY")
    goal: str = Field(..., description="Target manipulation goal")
    use_llm: bool = Field(
        default=False,
        description="If True, use LangGraph/Gemini for payload generation. "
                    "If False, use the template fallback (no API key needed)."
    )


class MandateCreateRequest(BaseModel):
    """Body of POST /mandates"""
    agent_id: UUID
    max_amount: float = Field(..., gt=0)
    currency: str
    approved_merchants: list[str] = Field(default_factory=list)
    approved_categories: list[str] = Field(default_factory=list)
    purpose_code_allowlist: list[str] = Field(default_factory=list)
    expires_at: datetime
