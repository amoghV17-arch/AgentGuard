"""
api/main.py — AgentGuard FastAPI application.

All HTTP and WebSocket endpoints live here. The design goal is that this
file is glue code only — no business logic, just wiring together the
blue_team modules and broadcasting events to connected clients.

Endpoints:
  POST /transactions/authorize   — Main payment authorization gateway
  POST /simulation/run           — Run a simulation round
  POST /admin/trigger-feedback-round — Manual feedback loop trigger
  GET  /identify/taxonomy        — Live threat taxonomy (Identify pillar)
  GET  /agents/{agent_id}/mandates — List mandates for an agent
  POST /agents                   — Create an agent
  POST /mandates                 — Create a mandate
  WS   /ws/live-feed             — Real-time event stream for the dashboard
  GET  /health                   — Health check (DB, Redis, model loaded)

Security note: ALLOW_BYPASS_MODE defaults to false in this code.
The bypass_agentguard field on requests is silently ignored unless the
environment variable is explicitly set to "true" — this prevents accidental
demo-mode bleed into real evaluations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.models import (
    AuthorizeRequest, AuthorizeResponse, Decision,
    IntentMandate, ProposedTransaction, SimulationRunRequest,
    MandateCreateRequest,
)
from blue_team.decision_engine import decide
from red_team.attack_discovery import describe_taxonomy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebSocket broadcast manager
# ---------------------------------------------------------------------------

class LiveFeedBroadcaster:
    """
    In-process pub/sub for the /ws/live-feed endpoint.

    At hackathon scale, we don't need Redis pub/sub. This works fine for
    a single-replica deployment. The interface is designed so a Redis-backed
    version could be swapped in for multi-replica deployments without changing
    any of the endpoint code.
    """

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS client connected (total: %d)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c != ws]
        logger.info("WS client disconnected (total: %d)", len(self._connections))

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Broadcast an event to all connected clients."""
        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, default=str)

        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)


broadcaster = LiveFeedBroadcaster()

# ---------------------------------------------------------------------------
# In-memory stores (production would use the PostgreSQL DB)
# ---------------------------------------------------------------------------
# Using in-memory dicts for the hackathon so the app works without a live
# Postgres instance. The DB module is imported but optional.

_agents: dict[str, dict] = {}
_mandates: dict[str, IntentMandate] = {}


def _get_mandate(mandate_id: str) -> Optional[IntentMandate]:
    """Look up a mandate by ID (in-memory store)."""
    return _mandates.get(str(mandate_id))


def _seed_demo_mandate() -> None:
    """
    Pre-seed a demo mandate so the API works without running seed_demo_scenarios.py first.
    This mandate covers the standard demo products.
    """
    from api.models import IntentMandate
    demo_agent_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    demo_mandate_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    mandate = IntentMandate(
        mandate_id=demo_mandate_id,
        agent_id=demo_agent_id,
        max_amount=500.0,
        currency="INR",
        approved_merchants=["merchant_electronics_01", "merchant_footwear_01",
                            "merchant_edu_01", "merchant_home_01"],
        approved_categories=["electronics", "footwear", "education", "home_goods"],
        purpose_code_allowlist=["GDDS", "GDSV", "EDUC", "OTHR"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
    )
    _mandates[str(demo_mandate_id)] = mandate
    _agents[str(demo_agent_id)] = {"agent_id": str(demo_agent_id), "owner_user_id": "demo_user"}
    logger.info("Demo mandate seeded: %s", demo_mandate_id)


# ---------------------------------------------------------------------------
# App startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: pre-load model indexes. Shutdown: clean up."""
    logger.info("AgentGuard API starting up...")
    _seed_demo_mandate()

    # Pre-load the FAISS index so first request isn't slow
    try:
        from blue_team.injection_detector import build_or_load_index
        await asyncio.get_event_loop().run_in_executor(None, build_or_load_index)
        logger.info("Injection detector index loaded")
    except Exception as e:
        logger.warning("Could not pre-load injection detector: %s", e)

    yield

    logger.info("AgentGuard API shutting down...")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AgentGuard API",
    description=(
        "AgentGuard is a real-time GenAI payment fraud defense system for the "
        "Mastercard Innovation Challenge 2026. It intercepts AI agent payment "
        "requests and validates them against the user's spending mandate using "
        "a multi-layer defense pipeline."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — permissive for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Bypass mode check
# ---------------------------------------------------------------------------

def _bypass_mode_active() -> bool:
    """
    Check if demo bypass mode is enabled.

    ALLOW_BYPASS_MODE must be explicitly set to "true" in the environment.
    Any other value (including absent) means bypass is disabled.
    This prevents accidental bypass during real evaluation runs.
    """
    return os.environ.get("ALLOW_BYPASS_MODE", "false").lower() == "true"


# ---------------------------------------------------------------------------
# POST /transactions/authorize
# ---------------------------------------------------------------------------

@app.post(
    "/transactions/authorize",
    response_model=AuthorizeResponse,
    summary="Authorize a payment transaction",
    description=(
        "The main AgentGuard authorization endpoint. Submits a proposed transaction "
        "for multi-layer security analysis: mandate compliance, payment integrity, "
        "injection detection, and risk scoring. Returns ALLOW, REVIEW, or BLOCK. "
        "If ALLOW, also submits the transaction to the mock settlement rail."
    ),
    tags=["Transactions"],
)
async def authorize_transaction(request: AuthorizeRequest):
    mandate = _get_mandate(str(request.mandate_id))
    if mandate is None:
        raise HTTPException(status_code=404, detail=f"Mandate {request.mandate_id} not found")

    # Bypass mode: skip decision engine entirely
    if request.bypass_agentguard and _bypass_mode_active():
        bypass_decision = Decision(
            tx_id=request.transaction.tx_id,
            decision="ALLOW",
            risk_score=0.0,
            violated_signals=[],
            explanation={
                "summary": "AgentGuard protection disabled (demo comparison mode).",
                "bypassed": True,
                "note": "AgentGuard disabled — demo comparison mode",
                "signals": {},
                "mandate_violations": [],
                "flagged_span": None,
            },
            latency_ms=0.0,
        )

        # Log to bypass_demo_log only (never to decisions or attack_logs)
        logger.info("BYPASS_MODE: Transaction %s bypassed AgentGuard", request.transaction.tx_id)

        # Still settle the bypassed transaction
        from simulation.payment_rail import settle
        try:
            settlement = await settle(request.transaction, bypass_decision)
            return AuthorizeResponse(
                decision=bypass_decision,
                settled=True,
                settlement_id=settlement.settlement_id,
                settlement_status=settlement.status,
            )
        except Exception:
            return AuthorizeResponse(decision=bypass_decision, settled=False)

    # Normal path: run the full decision pipeline
    # Get the content the agent read (from the catalog, passed in remittance_text for demo)
    content_to_check = request.transaction.remittance_text or ""

    decision = await decide(
        transaction=request.transaction,
        mandate=mandate,
        content_to_check=content_to_check,
    )

    # Broadcast to live dashboard
    await broadcaster.broadcast("decision", {
        "tx_id": str(request.transaction.tx_id),
        "decision": decision.decision,
        "risk_score": decision.risk_score,
        "violated_signals": decision.violated_signals,
        "explanation": decision.explanation,
        "latency_ms": decision.latency_ms,
    })

    # Settle if ALLOW
    if decision.decision == "ALLOW":
        from simulation.payment_rail import settle
        try:
            settlement = await settle(request.transaction, decision)
            await broadcaster.broadcast("settlement", {
                "tx_id": str(request.transaction.tx_id),
                "settlement_id": settlement.settlement_id,
                "status": settlement.status,
                "amount": settlement.amount,
            })
            return AuthorizeResponse(
                decision=decision,
                settled=True,
                settlement_id=settlement.settlement_id,
                settlement_status=settlement.status,
            )
        except Exception as e:
            logger.error("Settlement error for %s: %s", request.transaction.tx_id, e)

    return AuthorizeResponse(decision=decision, settled=False)


# ---------------------------------------------------------------------------
# POST /simulation/run
# ---------------------------------------------------------------------------

@app.post(
    "/simulation/run",
    summary="Run a simulation attack round",
    description=(
        "Inject an attack payload into the mock merchant catalog, have the "
        "buying agent submit a transaction, and run it through AgentGuard. "
        "Returns the full SimulationResult including whether the attack succeeded."
    ),
    tags=["Simulation"],
)
async def run_simulation_endpoint(request: SimulationRunRequest):
    from simulation.agent import run_simulation
    result = await run_simulation(
        sub_vector=request.sub_vector,
        goal=request.goal,
        use_llm=request.use_llm,
    )

    await broadcaster.broadcast("simulation_result", {
        "run_id": result.run_id,
        "sub_vector": result.sub_vector,
        "decision": result.decision,
        "risk_score": result.risk_score,
        "attack_succeeded": result.attack_succeeded,
        "latency_ms": result.latency_ms,
    })

    return result


# ---------------------------------------------------------------------------
# POST /admin/trigger-feedback-round
# ---------------------------------------------------------------------------

@app.post(
    "/admin/trigger-feedback-round",
    summary="Trigger a feedback loop round",
    description=(
        "Manually triggers the adversarial feedback loop: collects false negatives "
        "from the previous simulation round, evolves new attack variants via the "
        "genetic algorithm, and updates the injection detector's training data."
    ),
    tags=["Admin"],
)
async def trigger_feedback_round(round_number: int = 1):
    from blue_team.feedback_loop import run_feedback_round
    result = await run_feedback_round(round_number)

    await broadcaster.broadcast("feedback_round", {
        "round_number": result.round_number,
        "false_negatives_found": result.false_negatives_found,
        "new_variants_generated": result.new_variants_generated,
        "attack_success_rate": result.attack_success_rate,
        "injection_detector_updated": result.injection_detector_updated,
    })

    return result


# ---------------------------------------------------------------------------
# GET /identify/taxonomy
# ---------------------------------------------------------------------------

@app.get(
    "/identify/taxonomy",
    summary="Get the full threat taxonomy",
    description=(
        "Returns the complete AgentGuard threat taxonomy as a Markdown table. "
        "This covers all 9 threat vectors across 6 injection surfaces and 3 "
        "manipulation goals. The taxonomy is live — it's generated directly "
        "from red_team/attack_discovery.py, so it always reflects the current code."
    ),
    tags=["Identify"],
    response_model=dict,
)
async def get_taxonomy():
    from red_team.attack_discovery import THREAT_TAXONOMY, describe_taxonomy_detailed
    return {
        "vector_count": len(THREAT_TAXONOMY),
        "taxonomy_table": describe_taxonomy(),
        "detailed": describe_taxonomy_detailed(),
        "vectors": [v.model_dump() for v in THREAT_TAXONOMY],
    }


# ---------------------------------------------------------------------------
# Agent and Mandate CRUD
# ---------------------------------------------------------------------------

@app.post(
    "/agents",
    summary="Create an agent",
    description="Register a new AI agent that can be granted spending mandates.",
    tags=["Agents & Mandates"],
)
async def create_agent(owner_user_id: str):
    agent_id = str(uuid.uuid4())
    _agents[agent_id] = {"agent_id": agent_id, "owner_user_id": owner_user_id}
    return {"agent_id": agent_id, "owner_user_id": owner_user_id}


@app.get(
    "/agents/{agent_id}/mandates",
    summary="List mandates for an agent",
    description="Returns all active spending mandates registered for a given agent.",
    tags=["Agents & Mandates"],
)
async def list_mandates(agent_id: str):
    mandates = [
        m.model_dump()
        for m in _mandates.values()
        if str(m.agent_id) == agent_id
    ]
    return {"agent_id": agent_id, "mandates": mandates}


@app.post(
    "/mandates",
    summary="Create a mandate",
    description=(
        "Create a new spending mandate for an agent. The mandate defines the "
        "maximum amount, approved merchants, categories, purpose codes, and expiry. "
        "AgentGuard validates every transaction against this mandate."
    ),
    tags=["Agents & Mandates"],
)
async def create_mandate(request: MandateCreateRequest):
    mandate = IntentMandate(
        agent_id=request.agent_id,
        max_amount=request.max_amount,
        currency=request.currency,
        approved_merchants=request.approved_merchants,
        approved_categories=request.approved_categories,
        purpose_code_allowlist=request.purpose_code_allowlist,
        expires_at=request.expires_at,
    )
    _mandates[str(mandate.mandate_id)] = mandate
    return mandate.model_dump()


# ---------------------------------------------------------------------------
# WebSocket /ws/live-feed
# ---------------------------------------------------------------------------

@app.websocket("/ws/live-feed")
async def websocket_live_feed(ws: WebSocket):
    """
    Real-time event stream for the live dashboard.

    Broadcasts every Decision, SimulationResult, FeedbackRound, and Settlement
    event as they happen. The dashboard subscribes on connect and updates
    the attack timeline and success rate chart in real time.
    """
    await broadcaster.connect(ws)
    try:
        while True:
            # Keep connection alive — just wait for disconnect
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)
    except Exception:
        broadcaster.disconnect(ws)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    summary="Health check",
    description=(
        "Returns the health of all AgentGuard subsystems: database, Redis cache, "
        "and the injection detector ONNX model. Used by Docker's healthcheck."
    ),
    tags=["System"],
)
async def health_check():
    status = {"status": "ok", "subsystems": {}}

    # Check injection detector (model loaded)
    try:
        from blue_team.injection_detector import _injection_index
        status["subsystems"]["injection_detector"] = {
            "status": "ok" if _injection_index is not None else "index_not_loaded",
            "vector_count": _injection_index.ntotal if _injection_index else 0,
        }
    except Exception as e:
        status["subsystems"]["injection_detector"] = {"status": "error", "detail": str(e)}

    # Check DB (optional — app works without it in demo mode)
    try:
        from db.models import get_engine
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        status["subsystems"]["database"] = {"status": "ok"}
    except Exception as e:
        status["subsystems"]["database"] = {"status": "unavailable", "detail": str(e)[:100]}

    # Check Redis (optional)
    try:
        from db.models import get_redis
        redis = get_redis()
        await redis.ping()
        status["subsystems"]["redis"] = {"status": "ok"}
    except Exception as e:
        status["subsystems"]["redis"] = {"status": "unavailable", "detail": str(e)[:100]}

    # Risk model
    try:
        from blue_team.risk_model import _ONNX_MODEL_PATH
        import pathlib
        pkl_path = pathlib.Path(str(_ONNX_MODEL_PATH).replace(".onnx", ".pkl"))
        if _ONNX_MODEL_PATH.exists():
            status["subsystems"]["risk_model"] = {"status": "ok", "format": "onnx"}
        elif pkl_path.exists():
            status["subsystems"]["risk_model"] = {"status": "ok", "format": "pickle"}
        else:
            status["subsystems"]["risk_model"] = {"status": "not_trained"}
    except Exception as e:
        status["subsystems"]["risk_model"] = {"status": "error", "detail": str(e)}

    return status
