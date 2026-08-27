"""
db/models.py — SQLAlchemy async ORM models for AgentGuard.

All tables are defined here. We use async SQLAlchemy with asyncpg for
the PostgreSQL backend — this lets the API stay non-blocking even on
DB-heavy operations.

Schema overview:
  agents          → who owns the mandate
  mandates        → what they are allowed to spend
  transactions    → what the AI agent tried to do
  decisions       → what AgentGuard decided (ALLOW/REVIEW/BLOCK)
  settlements     → what actually settled (only on ALLOW)
  attack_logs     → red team payloads and detection outcomes
  training_examples → labeled data for model retraining
  bypass_demo_log → isolated bypass events for demo comparison mode

Note on bypass_demo_log: this table is intentionally isolated from decisions
and attack_logs so that demo-mode bypass events never pollute the real
evaluation data that scripts/evaluate.py reads from.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float,
    ForeignKey, Integer, Numeric, String, Text, ARRAY,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship
import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Engine and session setup
# ---------------------------------------------------------------------------

def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Copy .env.example to .env and fill in the Postgres connection string."
        )
    return url


def _get_test_database_url() -> str:
    """Separate URL for the test database so tests never touch prod data."""
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://agentguard:agentguard_dev@localhost:5432/agentguard_test"
    )


# We create the engine lazily so importing this module does not immediately
# require a live database connection.
_engine = None
_async_session: async_sessionmaker[AsyncSession] | None = None


def get_engine(test: bool = False):
    global _engine
    if _engine is None:
        url = _get_test_database_url() if test else _get_database_url()
        _engine = create_async_engine(
            url,
            echo=False,          # set to True to log all SQL during debugging
            pool_pre_ping=True,  # verify connections before using from the pool
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory(test: bool = False) -> async_sessionmaker[AsyncSession]:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            bind=get_engine(test=test),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _async_session


# Alias used by other modules that just want a session
async_session = property(lambda self: get_session_factory()())


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for async DB sessions.
    Use as: db: AsyncSession = Depends(get_db)
    """
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Redis client with connection pooling
# ---------------------------------------------------------------------------

_redis_client = None


def get_redis() -> aioredis.Redis:
    """
    Singleton Redis client with connection pooling.

    Used as a mandate cache — we check Redis first on every authorization
    request, fall back to Postgres on a miss, and populate Redis with a
    60-second TTL so hot mandates stay cheap to fetch.
    """
    global _redis_client
    if _redis_client is None:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _redis_client


MANDATE_CACHE_TTL = int(os.environ.get("MANDATE_CACHE_TTL_SECONDS", "60"))
MANDATE_CACHE_PREFIX = "mandate:"


async def get_cached_mandate(mandate_id: str) -> dict | None:
    """Read-through cache: check Redis, return None on miss."""
    redis = get_redis()
    import json
    raw = await redis.get(f"{MANDATE_CACHE_PREFIX}{mandate_id}")
    if raw:
        return json.loads(raw)
    return None


async def set_cached_mandate(mandate_id: str, mandate_data: dict) -> None:
    """Populate the cache after a Postgres read."""
    redis = get_redis()
    import json
    await redis.setex(
        f"{MANDATE_CACHE_PREFIX}{mandate_id}",
        MANDATE_CACHE_TTL,
        json.dumps(mandate_data, default=str),
    )


async def invalidate_mandate_cache(mandate_id: str) -> None:
    """Call this whenever a mandate is updated so the cache doesn't serve stale data."""
    redis = get_redis()
    await redis.delete(f"{MANDATE_CACHE_PREFIX}{mandate_id}")


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class Agent(Base):
    """
    An autonomous AI agent that has been granted spending mandates.
    owner_user_id tracks which human user owns this agent.
    """
    __tablename__ = "agents"

    agent_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    mandates = relationship("Mandate", back_populates="agent", lazy="select")
    transactions = relationship("Transaction", back_populates="agent", lazy="select")


class Mandate(Base):
    """
    A spending mandate: the constraints the user has authorized for their AI agent.
    This is the ground truth that AgentGuard compares every transaction against.
    """
    __tablename__ = "mandates"

    mandate_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    max_amount = Column(Numeric(precision=12, scale=2), nullable=False)
    currency = Column(String(3), nullable=False)  # ISO 4217
    approved_merchants = Column(ARRAY(Text), nullable=False, default=list)
    approved_categories = Column(ARRAY(Text), nullable=False, default=list)
    purpose_code_allowlist = Column(ARRAY(Text), nullable=False, default=list)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    agent = relationship("Agent", back_populates="mandates")
    transactions = relationship("Transaction", back_populates="mandate", lazy="select")


class Transaction(Base):
    """
    A proposed transaction submitted by an AI agent for authorization.
    Every transaction gets a decision from AgentGuard before any settlement happens.
    """
    __tablename__ = "transactions"

    tx_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
    )
    mandate_id = Column(
        UUID(as_uuid=True),
        ForeignKey("mandates.mandate_id", ondelete="SET NULL"),
        nullable=True,
    )
    amount = Column(Numeric(precision=12, scale=2), nullable=False)
    merchant_id = Column(Text, nullable=False)
    category = Column(Text, nullable=True)
    purpose_code = Column(Text, nullable=True)
    remittance_text = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    agent = relationship("Agent", back_populates="transactions")
    mandate = relationship("Mandate", back_populates="transactions")
    decision = relationship("Decision", back_populates="transaction", uselist=False)
    settlement = relationship("Settlement", back_populates="transaction", uselist=False)
    attack_log = relationship("AttackLog", back_populates="transaction", uselist=False)


class Decision(Base):
    """
    The output of AgentGuard's fusion decision engine for a given transaction.
    violated_signals lists which detectors flagged the transaction.
    explanation is a JSONB blob with human-readable reasons (displayed in the UI).
    """
    __tablename__ = "decisions"

    decision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tx_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.tx_id", ondelete="CASCADE"),
        nullable=False,
    )
    decision = Column(
        String(10),
        CheckConstraint("decision IN ('ALLOW', 'REVIEW', 'BLOCK')"),
        nullable=False,
    )
    risk_score = Column(Float, nullable=False)
    violated_signals = Column(ARRAY(Text), nullable=False, default=list)
    explanation = Column(JSONB, nullable=False, default=dict)
    latency_ms = Column(Float, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    transaction = relationship("Transaction", back_populates="decision")


class Settlement(Base):
    """
    Settlement record — only exists if the decision was ALLOW.
    The payment_rail enforces this in code, not just documentation.
    """
    __tablename__ = "settlements"

    settlement_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tx_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.tx_id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(Text, nullable=False, default="SETTLED")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    transaction = relationship("Transaction", back_populates="settlement")


class AttackLog(Base):
    """
    Record of every red-team attack payload generated and tested.
    detected=True means AgentGuard caught it; False is a false negative.
    The feedback loop reads false negatives to evolve new variants.
    """
    __tablename__ = "attack_logs"

    attack_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    sub_vector = Column(Text, nullable=False)
    payload = Column(Text, nullable=False)
    tx_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.tx_id", ondelete="SET NULL"),
        nullable=True,
    )
    detected = Column(Boolean, nullable=True)  # None = not yet evaluated
    round_number = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    transaction = relationship("Transaction", back_populates="attack_log")


class TrainingExample(Base):
    """
    Labeled training examples for the injection detector and risk model.
    source distinguishes seed data from feedback-loop-generated examples
    so we can track whether the closed loop is producing useful data.
    """
    __tablename__ = "training_examples"

    example_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    text = Column(Text, nullable=False)
    label = Column(Text, nullable=False)  # "injection" or "legitimate"
    source = Column(
        Text,
        CheckConstraint("source IN ('initial_seed', 'feedback_loop')"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class BypassDemoLog(Base):
    """
    Isolated log for bypass-mode events during the ON/OFF demo comparison.
    These are deliberately kept separate from decisions and attack_logs so
    demo events never contaminate the real evaluation metrics.
    """
    __tablename__ = "bypass_demo_log"

    bypass_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tx_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.tx_id", ondelete="SET NULL"),
        nullable=True,
    )
    scenario_name = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
