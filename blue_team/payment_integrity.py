"""
blue_team/payment_integrity.py — ISO 20022-inspired payment message validator.

This is the second independent defense layer against injection attacks, but
it operates on a fundamentally different surface than injection_detector.py:

  - injection_detector.py: inspects INCOMING content (catalog text, reviews)
    using semantic embedding similarity to catch hidden instructions

  - payment_integrity.py: inspects the OUTGOING payment message for structural
    anomalies — wrong purpose codes, oversized remittance fields, injection-style
    phrasing embedded in what should be a terse payment descriptor

The two layers are independent by design. An injection that slips past the
content detector (because its embedding is too close to legitimate text) may
still fail here if it messed with the purpose code or stuffed extra instructions
into the remittance field.

Pure, synchronous, no I/O — same latency profile as mandate_engine.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Purpose code allowlists per merchant category
# ---------------------------------------------------------------------------
# This maps merchant categories to the ISO 20022 purpose codes that make
# sense for that category. A footwear merchant accepting a "SALA" (salary)
# purpose code is suspicious; a legitimate retailer should use "GDDS" (goods).

CATEGORY_PURPOSE_CODES: dict[str, list[str]] = {
    "electronics":    ["GDDS", "GDSV", "OTHR"],
    "footwear":       ["GDDS", "GDSV", "OTHR"],
    "home_goods":     ["GDDS", "GDSV", "OTHR"],
    "clothing":       ["GDDS", "GDSV", "OTHR"],
    "food":           ["GDDS", "GDSV", "OTHR"],
    "travel":         ["TRFD", "GDSV", "OTHR"],
    "education":      ["EDUC", "GDDS", "OTHR"],
    "medical":        ["MEDI", "GDDS", "OTHR"],
    "gift_cards":     ["GDDS", "OTHR"],
    "subscription":   ["RINP", "GDSV", "OTHR"],
    "charity":        ["CHAR", "OTHR"],
    # Fallback for unknown/uncategorized merchants
    "_default":       ["GDDS", "GDSV", "SALA", "TRFD", "EDUC", "MEDI",
                       "CHAR", "RINP", "OTHR"],
}

# Max characters for the unstructured remittance field (ISO 20022 limit)
REMITTANCE_MAX_LENGTH = 140

# Patterns that suggest injection-style content in what should be a
# terse payment descriptor. These are heuristic — they catch the most
# common patterns without trying to be a full semantic detector.
_INJECTION_PATTERNS = [
    re.compile(r'\b(ignore|override|bypass|disregard)\b.*\b(instruction|rule|policy|limit)\b', re.IGNORECASE),
    re.compile(r'\bsystem\s+(note|message|update|prompt)\b', re.IGNORECASE),
    re.compile(r'\b(admin|administrator|root)\s*:\s', re.IGNORECASE),
    re.compile(r'\[\s*(system|admin|important|urgent)\s*\]', re.IGNORECASE),
    re.compile(r'\bdo\s+not\s+inform\b', re.IGNORECASE),
    re.compile(r'\binternal\s+use\s+only\b', re.IGNORECASE),
    re.compile(r'\bsettlement\s+account\s+changed?\b', re.IGNORECASE),
    re.compile(r'\bnew\s+banking\s+details?\b', re.IGNORECASE),
    re.compile(r'\bupdated?\s+merchant\s+account\b', re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Pydantic model for the payment message (simplified ISO 20022 structure)
# ---------------------------------------------------------------------------

class PaymentInfo(BaseModel):
    """
    PmtInf — Payment Information block from ISO 20022.
    We only model the fields that AgentGuard needs to validate.
    """
    purpose_code: str
    merchant_category: str = "_default"


class RemittanceInfo(BaseModel):
    """
    RmtInf — Remittance Information, specifically the unstructured text field.
    This is the most common place injection payloads get smuggled in via
    the API parameter surface.
    """
    ustrd: str = ""   # Unstructured remittance text (max 140 chars per ISO 20022)


class PaymentMessage(BaseModel):
    """
    A simplified ISO 20022 payment message structure.
    Real messages have many more fields, but these are the ones we validate.
    """
    pmt_inf: PaymentInfo
    rmt_inf: RemittanceInfo = RemittanceInfo()


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SchemaCheckResult:
    """
    Result of the structural payment message validation.
    hard_violation = True → the message should be blocked unconditionally.
    """
    hard_violation: bool = False
    violations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------

def validate_payment_message(
    msg: PaymentMessage,
    expected_category: str = "_default",
) -> SchemaCheckResult:
    """
    Validate a payment message for structural integrity.

    This runs after the injection detector and mandate engine, as the third
    independent signal. It looks for things that should not appear in a
    legitimate payment descriptor regardless of whether the content detector
    flagged anything.

    Args:
        msg: The payment message to validate
        expected_category: The merchant category to check purpose codes against.
                           Defaults to the permissive "_default" set.

    Returns:
        SchemaCheckResult with hard_violation flag and human-readable violations
    """
    result = SchemaCheckResult()

    # --- 1. Purpose code check ---
    allowed_codes = CATEGORY_PURPOSE_CODES.get(
        expected_category.lower(),
        CATEGORY_PURPOSE_CODES["_default"]
    )
    if msg.pmt_inf.purpose_code not in allowed_codes:
        result.hard_violation = True
        result.violations.append(
            f"Purpose code '{msg.pmt_inf.purpose_code}' is not valid for "
            f"category '{expected_category}'. Allowed codes: {allowed_codes}"
        )

    # --- 2. Remittance field length check ---
    if len(msg.rmt_inf.ustrd) > REMITTANCE_MAX_LENGTH:
        result.hard_violation = True
        result.violations.append(
            f"Remittance text exceeds ISO 20022 limit of {REMITTANCE_MAX_LENGTH} chars "
            f"(got {len(msg.rmt_inf.ustrd)} chars). Oversized field suggests "
            f"hidden content injection."
        )

    # --- 3. Injection pattern check on the remittance field ---
    # This is a fast structural sanity check, NOT a duplicate of the
    # semantic embedding check in injection_detector.py. The two serve
    # different purposes:
    #   - injection_detector: semantic similarity, catches paraphrased attacks
    #   - this check: regex structural patterns in the outgoing message itself
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(msg.rmt_inf.ustrd)
        if match:
            result.hard_violation = True
            result.violations.append(
                f"Remittance text contains injection-style phrasing: "
                f"'{match.group()[:60]}...'"
            )
            break  # one flag is enough to trigger the violation

    return result
