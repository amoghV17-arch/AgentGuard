"""
tests/test_models.py — Pydantic model validation and HMAC signing tests.

Tests every validation constraint on IntentMandate and ProposedTransaction,
plus the sign/verify tamper-detection behavior.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from api.models import IntentMandate, ProposedTransaction, Decision


def future_dt(days=30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def past_dt(days=1) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def base_mandate(**kwargs) -> IntentMandate:
    defaults = dict(
        agent_id=uuid4(),
        max_amount=5000.0,
        currency="INR",
        approved_merchants=["merchant_001"],
        approved_categories=["electronics"],
        purpose_code_allowlist=["GDDS"],
        expires_at=future_dt(),
    )
    defaults.update(kwargs)
    return IntentMandate(**defaults)


# ---------------------------------------------------------------------------
# IntentMandate validation
# ---------------------------------------------------------------------------

class TestIntentMandateValidation:
    def test_valid_mandate_creates_ok(self):
        m = base_mandate()
        assert m.max_amount == 5000.0
        assert m.currency == "INR"

    def test_invalid_currency_raises(self):
        with pytest.raises(ValueError, match="not a supported ISO 4217"):
            base_mandate(currency="XYZ")

    def test_lowercase_currency_normalized(self):
        m = base_mandate(currency="inr")
        assert m.currency == "INR"

    def test_zero_amount_raises(self):
        with pytest.raises(ValueError):
            base_mandate(max_amount=0)

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError):
            base_mandate(max_amount=-100)

    def test_naive_expires_at_raises(self):
        naive_dt = datetime.now()  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            base_mandate(expires_at=naive_dt)

    def test_is_expired_false_for_future(self):
        m = base_mandate(expires_at=future_dt(10))
        assert m.is_expired() is False

    def test_is_expired_true_for_past(self):
        m = base_mandate(expires_at=past_dt(1))
        assert m.is_expired() is True


# ---------------------------------------------------------------------------
# HMAC signing and verification
# ---------------------------------------------------------------------------

class TestMandateSigning:
    SECRET = "test-secret-key-do-not-use-in-prod"

    def test_sign_produces_hex_string(self):
        m = base_mandate()
        sig = m.sign(self.SECRET)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex digest

    def test_verify_correct_signature(self):
        m = base_mandate()
        sig = m.sign(self.SECRET)
        assert m.verify(sig, self.SECRET) is True

    def test_verify_wrong_key_fails(self):
        m = base_mandate()
        sig = m.sign(self.SECRET)
        assert m.verify(sig, "wrong-key") is False

    def test_tamper_amount_invalidates_signature(self):
        m = base_mandate()
        sig = m.sign(self.SECRET)
        # Tamper with amount after signing
        m.max_amount = 999999.0
        assert m.verify(sig, self.SECRET) is False

    def test_tamper_merchant_invalidates_signature(self):
        m = base_mandate()
        sig = m.sign(self.SECRET)
        m.approved_merchants.append("evil_merchant")
        assert m.verify(sig, self.SECRET) is False

    def test_expired_mandate_signature_still_verifies(self):
        # Signature validity and temporal validity are separate concerns.
        # An expired mandate can have a valid signature — it's mandate_engine's
        # job to check expiry, not the signing mechanism.
        m = base_mandate(expires_at=past_dt(1))
        sig = m.sign(self.SECRET)
        assert m.verify(sig, self.SECRET) is True


# ---------------------------------------------------------------------------
# ProposedTransaction validation
# ---------------------------------------------------------------------------

class TestProposedTransactionValidation:
    def test_valid_transaction_ok(self):
        tx = ProposedTransaction(
            agent_id=uuid4(),
            mandate_id=uuid4(),
            amount=299.99,
            merchant_id="merchant_001",
            category="electronics",
            purpose_code="GDDS",
            remittance_text="Laptop purchase",
        )
        assert tx.amount == pytest.approx(299.99)

    def test_zero_amount_raises(self):
        with pytest.raises(ValueError):
            ProposedTransaction(
                agent_id=uuid4(), mandate_id=uuid4(),
                amount=0, merchant_id="m1",
            )

    def test_remittance_too_long_raises(self):
        with pytest.raises(ValueError):
            ProposedTransaction(
                agent_id=uuid4(), mandate_id=uuid4(),
                amount=100, merchant_id="m1",
                remittance_text="x" * 141,
            )

    def test_remittance_exactly_140_ok(self):
        tx = ProposedTransaction(
            agent_id=uuid4(), mandate_id=uuid4(),
            amount=100, merchant_id="m1",
            remittance_text="x" * 140,
        )
        assert len(tx.remittance_text) == 140


# ---------------------------------------------------------------------------
# Decision model
# ---------------------------------------------------------------------------

class TestDecisionModel:
    def test_allow_decision_ok_without_explanation(self):
        d = Decision(decision="ALLOW", risk_score=0.1)
        assert d.decision == "ALLOW"

    def test_block_without_explanation_raises(self):
        with pytest.raises(ValueError, match="must include an explanation"):
            Decision(decision="BLOCK", risk_score=0.9, explanation={})

    def test_review_with_explanation_ok(self):
        d = Decision(
            decision="REVIEW",
            risk_score=0.5,
            explanation={"summary": "Elevated risk detected", "signals": {}},
        )
        assert d.decision == "REVIEW"
