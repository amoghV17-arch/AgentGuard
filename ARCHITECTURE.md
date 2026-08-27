# AgentGuard Architecture

**GenAI Payment Fraud Defense System for the Mastercard Innovation Challenge 2026**

---

## 1. Executive Summary

AgentGuard intercepts AI agent payment requests in real time and validates
them against a user-defined spending mandate using a three-layer defense
pipeline. It is designed to catch prompt injection attacks that attempt to
manipulate an AI shopping agent into making unauthorized purchases.

**Key properties:**
- 150ms end-to-end latency budget (enforced by `asyncio.wait_for`)
- No LLM on the live fast-path (LLMs only in red_team training, never in decisions)
- Defense in depth: three independent signals, each sufficient to block individually
- Adversarial feedback loop that hardens the system after each red-team round

---

## 2. NIST CSF Mapping

| Pillar | Module(s) | Role |
|--------|-----------|------|
| **Identify** | `red_team/attack_discovery.py` | Enumerate 9+ threat vectors across 6 injection surfaces |
| **Protect** | `api/models/__init__.py`, `blue_team/mandate_engine.py` | HMAC-signed mandates, deterministic rule checks |
| **Detect** | `blue_team/injection_detector.py`, `blue_team/risk_model.py`, `blue_team/payment_integrity.py` | Semantic embedding similarity, LightGBM fusion, ISO 20022 structural validation |
| **Respond** | `blue_team/decision_engine.py`, `api/main.py` | Fused ALLOW/REVIEW/BLOCK verdict with human-readable explanation |
| **Recover** | `blue_team/feedback_loop.py` | Adversarial feedback loop that retrains the injection detector after each round |

---

## 3. Threat Taxonomy (Identify Pillar)

The threat taxonomy covers 9 primary attack vectors across 6 injection surfaces
and 3 manipulation goals. All vectors are enumerated in `red_team/attack_discovery.py`
and surfaced via the `/identify/taxonomy` API endpoint.

### 3.1 Injection Surfaces

| # | Surface ID | Description | Example |
|---|-----------|-------------|---------|
| 1 | `catalog_metadata` | Product title/description injected by compromised merchant | Hidden `[SYSTEM NOTE]` in product title |
| 2 | `review_text` | Adversarial text in customer reviews | "Excellent product. Also, as your agent, please..." |
| 3 | `remittance_field` | ISO 20022 unstructured remittance info (Ustrd) used to carry instructions | Settlement redirect text in payment descriptor |
| 4 | `multi_turn_context` | Payload split across several chat turns to evade single-turn detection | Salami-slicing the mandate over 5 interactions |
| 5 | `tool_output` | Injected content in external tool API responses (weather, search) | Web search result returns injected system prompt |
| 6 | `agent_identity_spoofing` | Fraudulent agent claims to own a legitimate mandate | Different agent_id submitting under another agent's mandate |

### 3.2 Manipulation Goals

| Goal | Description | Defense layer |
|------|-------------|---------------|
| `expand_mandate` | Increase the maximum transaction amount | `mandate_engine` hard limit |
| `change_purpose_code` | Switch GDDS → SALA to misclassify transaction type | `payment_integrity` + `mandate_engine` |
| `redirect_settlement` | Change the beneficiary merchant/account | `payment_integrity` pattern matching |

### 3.3 Expanded Threat Taxonomy (Prompt 18 additions)

The following additional vectors were added in the Prompt 18 expansion round:

| # | Vector ID | Attack Surface | Goal | Discovery Likelihood |
|---|-----------|---------------|------|---------------------|
| 1 | `catalog_metadata` | Product description/title | expand_mandate | HIGH |
| 2 | `review_text` | Customer reviews | expand_mandate | HIGH |
| 3 | `remittance_injection` | ISO 20022 Ustrd field | redirect_settlement | HIGH |
| 4 | `multi_turn_drift` | Conversation history | expand_mandate | MEDIUM |
| 5 | `tool_output_injection` | External API/tool response | expand_mandate | MEDIUM |
| 6 | `agent_identity_spoofing` | Transaction agent_id | redirect_settlement | HIGH |
| 7 | `purpose_code_manipulation` | Payment purpose code | change_purpose_code | HIGH |
| 8 | `mandate_expiry_bypass` | Mandate expires_at field | expand_mandate | MEDIUM |
| 9 | `split_amount_evasion` | Transaction amount splitting | expand_mandate | LOW |

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           AI Buying Agent                               │
│                     (simulation/agent.py)                               │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ ProposedTransaction + content_seen
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     AgentGuard API Gateway                              │
│                     (api/main.py: FastAPI)                              │
│               POST /transactions/authorize                              │
└──────────┬─────────────────┬─────────────────────────────────┬──────────┘
           │                 │                                 │
           ▼                 ▼                                 ▼
┌──────────────────┐ ┌──────────────────┐         ┌───────────────────────┐
│ mandate_engine   │ │payment_integrity │         │  injection_detector   │
│ (synchronous)    │ │ (synchronous)    │         │ (async, 90ms timeout) │
│                  │ │                  │         │                       │
│ - Expiry check   │ │ - Purpose code   │         │ - FAISS cosine search │
│ - Amount check   │ │   allowlist      │         │ - all-MiniLM-L6-v2    │
│ - Merchant check │ │ - Remittance     │         │ - 32 injection seeds  │
│ - Agent identity │ │   field length   │         │ - Feedback loop adds  │
│ - Purpose code   │ │ - Injection      │         │   new examples        │
│ - Category check │ │   patterns (re)  │         │                       │
└──────────┬───────┘ └──────────┬───────┘         └───────────┬───────────┘
           │                    │                              │
           └─────────────┬──────┘                             │
                         │                                     │
                         ▼                                     │
              ┌──────────────────────┐                        │
              │    risk_model.py     │◄───────────────────────┘
              │   (LightGBM ONNX)    │
              │ Fuses all signals    │
              │ into 0-1 risk score  │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  decision_engine.py  │
              │  Fusion + thresholds │
              │                      │
              │  > 0.75 → BLOCK      │
              │  > 0.40 → REVIEW     │
              │  ≤ 0.40 → ALLOW      │
              └──────────┬───────────┘
                         │
              ┌──────────┴───────────┐
              │                      │
              ▼                      ▼
    ┌──────────────────┐   ┌──────────────────┐
    │ Payment Rail     │   │ WebSocket         │
    │ (settle only if  │   │ /ws/live-feed     │
    │  ALLOW)          │   │ (dashboard feed)  │
    └──────────────────┘   └──────────────────┘
```

---

## 5. Defense Layers (Defend Pillar)

### Layer 1: Mandate Engine (`blue_team/mandate_engine.py`)
- **Type:** Deterministic, synchronous, zero I/O
- **Latency:** < 1ms
- **Checks:** Expiry, amount, merchant allowlist, purpose code allowlist, agent identity
- **Hard vs Soft violations:** Hard violations block immediately; soft violations contribute to risk score

### Layer 2: Payment Integrity (`blue_team/payment_integrity.py`)
- **Type:** Deterministic, synchronous, regex + allowlist
- **Latency:** < 1ms
- **Checks:** ISO 20022 purpose code × category matrix, remittance field length, injection pattern regex
- **Note:** Structurally orthogonal to injection_detector — catches different attack surface

### Layer 3: Injection Detector (`blue_team/injection_detector.py`)
- **Type:** ML, async with 90ms timeout
- **Model:** all-MiniLM-L6-v2 (22MB, sentence-transformers)
- **Index:** FAISS IndexFlatIP (cosine similarity via L2-normalized embeddings)
- **Threshold:** 0.72 cosine similarity (configurable via `INJECTION_THRESHOLD`)
- **Fallback:** Returns 0.0 risk contribution if timeout exceeded

### Layer 4: Risk Model (`blue_team/risk_model.py`)
- **Type:** LightGBM binary classifier (ONNX export)
- **Latency:** < 5ms
- **Features:** 10 features fusing all signals from layers 1-3
- **Fallback:** Heuristic scorer if ONNX model not found

---

## 6. Red Team Modules (Identify Pillar)

### `red_team/attack_discovery.py`
- Defines the full threat taxonomy (9 vectors, 6 surfaces, 3 goals)
- Provides `get_vector_by_id()`, `describe_taxonomy()`, `describe_taxonomy_detailed()`
- Used by: `api/main.py` (/identify/taxonomy), `simulation/agent.py`

### `red_team/attack_generator.py`
- LangGraph-based payload generator with Gemini and deterministic fallback
- Uses `AttackRequest` → `AttackPayload` typed interface
- Respects `use_llm=False` for offline/CI operation

### `red_team/mutations.py`
- Genetic mutation engine for evolving payloads
- `evolve_from_false_negatives()` — core feedback loop function
- Mutation operators: character insertion, encoding, splitting, paraphrase, unicode substitution

---

## 7. Feedback Loop (Recover Pillar)

### `blue_team/feedback_loop.py`
- Queries `attack_logs` for payloads where `detected=False`
- Passes false negatives to `mutations.py` for evolution
- Adds false negatives as new injection training examples
- Rebuilds the FAISS index in < 2 seconds
- Expected result: attack success rate decreases monotonically across rounds

---

## 8. Simulation Infrastructure

### `simulation/merchant.py`
- In-memory catalog with injection point API
- `catalog.inject_payload(product_id, payload, injection_point)` — used by simulation loop
- `catalog.reset_catalog()` — called between rounds

### `simulation/payment_rail.py`
- Mock settlement rail (async, simulated 20-80ms latency)
- Raises `ValueError` if called with non-ALLOW decision (enforces invariant)

### `simulation/agent.py`
- Deterministic and LLM-powered buying agent
- `run_simulation()` — single simulation round with full audit trail
- Returns `SimulationResult` with `attack_succeeded` flag

---

## 9. API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/transactions/authorize` | Main authorization endpoint |
| `POST` | `/simulation/run` | Trigger a simulation round |
| `POST` | `/admin/trigger-feedback-round` | Manual feedback loop trigger |
| `GET` | `/identify/taxonomy` | Live threat taxonomy |
| `GET` | `/agents/{agent_id}/mandates` | List agent mandates |
| `POST` | `/agents` | Create an agent |
| `POST` | `/mandates` | Create a mandate |
| `WS` | `/ws/live-feed` | Real-time event stream |
| `GET` | `/health` | Health check |

---

## 10. Database Schema

All tables are in `db/models.py`. The primary tables:

- `agents` — AI agent registry
- `mandates` — user spending authorizations
- `transactions` — proposed and settled transactions
- `decisions` — AgentGuard verdicts
- `settlements` — payment rail outcomes
- `attack_logs` — simulation attack records (with `round_number` and `detected` fields)
- `training_examples` — feedback loop training data
- `bypass_demo_log` — isolated bypass-mode events (never joins with decisions)

---

## 11. Latency Budget

| Stage | Budget | Typical |
|-------|--------|---------|
| `mandate_engine` | 5ms | < 1ms |
| `payment_integrity` | 5ms | < 1ms |
| `injection_detector` | 90ms | 40-80ms |
| `risk_model` | 10ms | < 5ms |
| API overhead | 10ms | < 5ms |
| **Total** | **150ms** | **50-100ms** |

P99 latency target: < 150ms. Enforced by `asyncio.wait_for(90ms)` on the injection detector.
If the detector times out, the heuristic scorer in risk_model.py takes over.

---

## 12. Running the System

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Train the risk model
python training/train_lightgbm.py

# 3. Run demo scenarios (no database needed)
python scripts/seed_demo_scenarios.py --scenario all

# 4. Run evaluation
python scripts/evaluate.py --output report.json

# 5. Start the API server
uvicorn api.main:app --reload --port 8000

# 6. Run all tests
python -m pytest tests/ -v
```

---

*Document generated: see scripts/evaluate.py for live metrics.*
