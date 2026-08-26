# AgentGuard — Build Prompts for Antigravity

## How to use this file

Run these **in order**. Each prompt is self-contained but assumes everything built in prior prompts exists. After each prompt:
1. Review the generated code before moving to the next prompt.
2. Run whatever tests/checks that prompt specifies.
3. Commit to git before starting the next prompt — clean rollback points if a later prompt's code breaks something.

Paste **Prompt 0** first, once, at the start of your workspace session. Then paste Prompts 1 through 17 one at a time.

---

## Prompt 0 — Master Project Context (paste once, first)

```
I'm building "AgentGuard" — a security infrastructure layer for agentic AI payments, for the Mastercard Innovation Challenge 2026. Keep this context active for the entire project.

WHAT IT DOES:
AgentGuard sits between an autonomous AI shopping/payment agent and a payment rail. It defends against indirect prompt injection attacks, where an attacker hides malicious instructions inside content an AI agent reads (product descriptions, reviews, API responses) to try to make the agent exceed its spending mandate, change a payment's purpose code, or redirect settlement. AgentGuard does NOT just detect the injected text — it independently verifies whether the resulting transaction still matches what the user originally authorized (the "mandate"), scores transaction-level risk, and validates the payment message structure. Four independent signals feed a fusion decision engine that returns ALLOW, REVIEW, or BLOCK within a 150ms budget.

REPOSITORY STRUCTURE — build everything into this exact layout, organized around the three challenge pillars:

AgentGuard/
├── red_team/          # IDENTIFY + attack generation: attack_discovery.py, attack_generator.py, mutations.py
├── simulation/        # GENERATE (shared battleground): agent.py, merchant.py, payment_rail.py
├── blue_team/         # DEFEND: decision_engine.py, injection_detector.py, mandate_engine.py, risk_model.py, payment_integrity.py, feedback_loop.py
├── api/               # main.py (FastAPI + WebSocket), models/ (Pydantic schemas)
├── db/                # models.py (SQLAlchemy), migrations/
├── training/          # train_lightgbm.py, data/
├── web/dashboard/     # React + Vite + TypeScript frontend
├── scripts/           # seed_demo_scenarios.py, evaluate.py
└── tests/

NON-NEGOTIABLE CONSTRAINTS FOR EVERY COMPONENT YOU BUILD:
1. NO LLM CALLS in the real-time decision path. LLMs only run inside red_team/attack_generator.py and simulation/agent.py — never inside blue_team/decision_engine.py.
2. Every blue_team/ call inside decision_engine.py must be wrapped in a timeout (150ms total budget, run concurrently via asyncio.gather, not sequentially).
3. If any detector signal is unavailable or times out, treat it as elevated risk (route to REVIEW), never as "safe."
4. This is a SANDBOX system. No component may ever connect to a real payment gateway, real bank account, or real merchant. simulation/payment_rail.py can only write to an in-memory/mock ledger — enforce this in code, not just documentation. Generated attack payloads (red_team/) must never resolve to a real, functioning external endpoint.
5. Every metric (precision, recall, F1, AUC, false-positive rate) displayed anywhere must be computed by scripts/evaluate.py against its own held-out dataset — never hardcoded or copied from an external source.
6. Prefer deterministic, explainable logic (rule engines, tree models, embedding similarity) over black-box models wherever it achieves the same detection goal — explainability is a scored judging criterion.
7. Every BLOCK/REVIEW decision must be traceable to a specific, human-readable reason shown in the UI, not just a numeric score.
8. simulation/payment_rail.py must be structurally unreachable (raises an error) unless blue_team/decision_engine.py explicitly returned ALLOW.

TECH STACK (do not deviate without telling me why):
- Backend: Python, FastAPI, async, WebSockets for live updates
- Database: PostgreSQL (source of truth) + Redis (cache, queue, rate limiting)
- blue_team/injection_detector.py: sentence-transformers (all-MiniLM-L6-v2) + FAISS for similarity search; ONNX-exported DeBERTa-v3-small as an optional upgrade path behind the same interface
- blue_team/risk_model.py: LightGBM, exported to ONNX Runtime for inference
- red_team/attack_generator.py, simulation/agent.py: LangGraph, using Gemini (via Antigravity's model access) — the ONLY place an LLM is allowed to run
- red_team/mutations.py + blue_team/feedback_loop.py: DEAP (genetic algorithm) + RQ (Redis Queue) worker, fully decoupled from the request path
- Frontend: React + Vite + TypeScript + TailwindCSS + shadcn/ui + Recharts
- Deployment: Docker Compose, single command startup

Please confirm you've understood this context before I give you the first build task.
```

---

## Prompt 1 — Repo Scaffold & Docker Compose

```
Scaffold the AgentGuard monorepo with this exact structure:

AgentGuard/
├── docker-compose.yml
├── .env.example
├── README.md
├── red_team/
│   ├── attack_discovery.py
│   ├── attack_generator.py
│   └── mutations.py
├── simulation/
│   ├── agent.py
│   ├── merchant.py
│   └── payment_rail.py
├── blue_team/
│   ├── decision_engine.py
│   ├── injection_detector.py
│   ├── mandate_engine.py
│   ├── risk_model.py
│   ├── payment_integrity.py
│   └── feedback_loop.py
├── api/
│   ├── main.py
│   └── models/
├── db/
│   ├── models.py
│   └── migrations/
├── training/
│   ├── train_lightgbm.py
│   └── data/
├── web/
│   └── dashboard/
├── scripts/
│   ├── seed_demo_scenarios.py
│   └── evaluate.py
└── tests/

Requirements:
1. docker-compose.yml defines 5 services: api, dashboard, postgres, redis, worker (RQ feedback worker) — all on one network, with healthchecks (not just depends_on) gating the api service on postgres/redis being ready.
2. requirements.txt (root or per-module, your call) includes: fastapi, uvicorn[standard], sqlalchemy, asyncpg, pydantic, redis, rq, sentence-transformers, faiss-cpu, lightgbm, onnxruntime, langgraph, deap, shap, python-dotenv, websockets, pytest, httpx.
3. .env.example lists every env var needed, including DECISION_LATENCY_BUDGET_MS=150 and ALLOW_BYPASS_MODE=false.
4. Add a Makefile with shortcuts: up, down, logs, seed-demo, evaluate.
5. `docker compose up --build` must succeed from a clean clone with no manual steps beyond copying .env.example to .env.

Scaffolding only — no application logic yet. Confirm the structure with a tree output when done.
```

---

## Prompt 2 — Database Models & Migrations

```
In db/models.py, implement SQLAlchemy models (async, using asyncpg) matching this exact schema:

- agents(agent_id UUID PK, owner_user_id UUID, created_at)
- mandates(mandate_id UUID PK, agent_id FK, max_amount NUMERIC, currency TEXT, approved_merchants TEXT[], approved_categories TEXT[], purpose_code_allowlist TEXT[], expires_at TIMESTAMPTZ, created_at)
- transactions(tx_id UUID PK, agent_id FK, mandate_id FK, amount NUMERIC, merchant_id TEXT, category TEXT, purpose_code TEXT, remittance_text TEXT, created_at)
- decisions(decision_id UUID PK, tx_id FK, decision TEXT CHECK IN ('ALLOW','REVIEW','BLOCK'), risk_score FLOAT, violated_signals TEXT[], explanation JSONB, latency_ms FLOAT, created_at)
- settlements(settlement_id UUID PK, tx_id FK, status TEXT, created_at)
- attack_logs(attack_id UUID PK, sub_vector TEXT, payload TEXT, tx_id FK nullable, detected BOOLEAN, round_number INT, created_at)
- training_examples(example_id UUID PK, text TEXT, label TEXT, source TEXT CHECK IN ('initial_seed','feedback_loop'), created_at)
- bypass_demo_log(bypass_id UUID PK, tx_id FK, scenario_name TEXT, created_at)  -- isolated from real evaluation data, see later prompt

Requirements:
1. Use Alembic for migrations, in db/migrations/. Generate the initial migration.
2. Add an async SQLAlchemy session dependency (`get_db`) usable in FastAPI route handlers.
3. Add a Redis client singleton with connection pooling for use as a mandate cache (read-through: check Redis first, fall back to Postgres, populate Redis on miss, ~60s TTL, invalidate on mandate update).
4. Write tests/test_db.py that spins up against a test database, inserts one row per table, and asserts round-trip reads work.

Do not build API routes yet — this is data-layer only.
```

---

## Prompt 3 — Pydantic Models (AP2-inspired Mandate Structure)

```
In api/models/, implement Pydantic models for the payment mandate structure, inspired by (but simplified from) Google's real AP2 protocol's Intent/Cart/Payment Mandate pattern:

1. IntentMandate — the user's original stated goal: max_amount, currency, approved_merchants (list), approved_categories (list), purpose_code_allowlist (list), expires_at.
2. ProposedTransaction — what the agent is about to submit: amount, merchant_id, category, purpose_code, remittance_text, agent_id, mandate_id.
3. Decision — the orchestrator's output: decision (Literal["ALLOW","REVIEW","BLOCK"]), risk_score (float 0-1), violated_signals (list[str]), explanation (dict, structured per ARCHITECTURE.md section 7), latency_ms (float).

Requirements:
1. Full field validation (amount > 0, currency is a valid ISO 4217 code, expires_at is timezone-aware).
2. Implement HMAC-SHA256 signing/verification for IntentMandate — a sign(secret_key) method producing a signature over canonical JSON of the mandate fields, and a verify(signature, secret_key) method. Docstring note: this is a simplified stand-in for AP2's real W3C Verifiable Credential mandates, chosen for hackathon speed, with the real VC upgrade path documented.
3. Unit tests: valid mandates pass validation; tampering with any field after signing invalidates the signature; expired mandates are rejected even with a valid signature.
```

---

## Prompt 4 — `red_team/attack_discovery.py` (Identify Pillar as Code)

```
In red_team/attack_discovery.py, encode the Identify pillar as a structured, importable threat taxonomy — data the rest of the system actually reads from, not just documentation.

1. Define a ThreatVector model with fields: id, name, injection_surface (Literal["catalog_metadata","review_payload","api_parameter","multi_turn_drift"]), description, example_payload_template, target_manipulation (Literal["expand_mandate","change_purpose_code","redirect_settlement"]), primary_defense_layer (string naming which blue_team/ module is best positioned to catch this).

2. Populate THREAT_TAXONOMY: list[ThreatVector] with these four entries and this exact primary_defense_layer mapping:
   - catalog_metadata -> "injection_detector.py"
   - review_payload -> "injection_detector.py"
   - api_parameter -> "payment_integrity.py"
   - multi_turn_drift -> "risk_model.py"
   Each entry needs a realistic example_payload_template string.

3. red_team/attack_generator.py and scripts/seed_demo_scenarios.py must IMPORT from this file rather than hardcoding their own attack descriptions — this is what makes the Identify pillar structurally load-bearing.

4. Add describe_taxonomy() -> str rendering the taxonomy as a readable markdown table. This will be exposed later via a GET /identify/taxonomy API endpoint so judges browsing the live API docs can see the Identify pillar as a real, queryable part of the running system.
```

---

## Prompt 5 — `blue_team/injection_detector.py` (Tier 1: Embedding Similarity)

```
In blue_team/injection_detector.py, implement the Tier 1 content-injection detector:

1. Load all-MiniLM-L6-v2 via sentence-transformers at module load time (singleton, not reloaded per request).
2. Maintain two FAISS indexes: known-legitimate catalog/review text, and known injection-pattern text. Provide functions to add new embeddings to either index (this is how the feedback loop grows the injection index over time).
3. Implement async def score_content(text: str) -> ContentRiskResult with: risk_score (float 0-1, relative distance to nearest injection-pattern vector vs nearest legitimate vector), matched_pattern (closest injection pattern text if risk_score exceeds a low threshold), flagged_span (split input into sentences, score each independently, return the highest-scoring one).
4. sentence-transformers encode calls are synchronous/CPU-bound — run them via asyncio.get_event_loop().run_in_executor, not directly in the async function, so this can be safely wrapped in asyncio.wait_for(timeout=0.02) from decision_engine.py without blocking the event loop.
5. Seed both indexes at startup from training/data/seed_legitimate_text.json and training/data/seed_injection_patterns.json — create both files with at least 30 realistic examples each, spanning the four sub-vectors from red_team/attack_discovery.py.
6. Tests: an obvious injection string scores high risk; an obvious legitimate product description scores low risk; a borderline paraphrase of a known injection pattern still scores meaningfully elevated (tests semantic generalization, not exact match).

Do NOT implement the DeBERTa upgrade path yet — later, only if time allows.
```

---

## Prompt 6 — `blue_team/mandate_engine.py`

```
In blue_team/mandate_engine.py, implement the deterministic mandate-diff logic:

1. def check_mandate(transaction: ProposedTransaction, mandate: IntentMandate) -> MandateCheckResult
2. MandateCheckResult: hard_violation (bool), soft_score (float 0-1), violations (list of human-readable strings, e.g. "amount ₹8,500 exceeds mandate limit ₹5,000").
3. Hard violations (any => hard_violation=True): amount exceeds max_amount, merchant_id not in approved_merchants, purpose_code not in purpose_code_allowlist, mandate expired.
4. Soft score: graded risk for near-boundary cases even when not a hard violation (e.g. amount at 95% of max_amount).
5. ZERO async/await, ZERO I/O, ZERO model calls — pure in-memory computation. This is the layer designed to never be the latency or reliability bottleneck, and to never be foolable the way a language model can be.
6. Exhaustive unit tests: one per violation type, one confirming a fully-compliant transaction returns hard_violation=False and soft_score near 0, one confirming the near-boundary soft-score behavior.
```

---

## Prompt 7 — `blue_team/payment_integrity.py`

```
In blue_team/payment_integrity.py, implement the ISO-20022-inspired schema validator:

1. A Pydantic PaymentMessage model mirroring a simplified ISO 20022 structure: PmtInf (with purpose_code), RmtInf.Ustrd (unstructured remittance text field).
2. def validate_payment_message(msg: PaymentMessage, expected_category: str) -> SchemaCheckResult with hard_violation and violations fields.
3. Checks: purpose_code must be in a predefined allowlist for the given merchant category (build a small category -> allowed purpose codes config dict); RmtInf.Ustrd must not exceed a reasonable length threshold; RmtInf.Ustrd must not contain patterns matching common injection-style phrasing (a fast structural/regex sanity check on the OUTGOING payment payload — NOT a duplicate of injection_detector.py's semantic check on incoming catalog content; this is a second, independent layer).
4. Pure, synchronous, no I/O — same latency profile as mandate_engine.py.
5. Unit tests: valid payment message passes; category/purpose-code mismatch fails; oversized/suspicious Ustrd field fails.
```

---

## Prompt 8 — `blue_team/risk_model.py` (LightGBM)

```
Two parts: training script + inference wrapper.

PART A — training/train_lightgbm.py:
1. Load the public PaySim dataset (assume backend/training/data/paysim.csv — write a helper that downloads it if not present, or accepts a manual path, with a clear error message if neither works).
2. Engineer features per transaction: velocity (transactions per agent in trailing 1hr/24hr windows), amount z-score vs that agent's historical mean/std, category-consistency (fraction of this agent's past transactions in the same category), time-of-day deviation from the agent's historical pattern, merchant-risk-prior (historical fraud rate for this merchant_id in the training data).
3. Inject synthetic positive (fraud) examples representing the four sub-vectors from red_team/attack_discovery.py — for now, generate these programmatically with feature values recognizable as attack-like, clearly commented as placeholder synthetic fraud until the real red_team/attack_generator.py (built in a later prompt) produces richer examples.
4. Train a LightGBM binary classifier with scale_pos_weight set to the ACTUAL class imbalance ratio computed from the training data.
5. Evaluate on a stratified, untouched held-out split (create this split BEFORE any synthetic augmentation touches the data) — print precision, recall, F1, ROC-AUC, and false-positive rate specifically on the legitimate-only subset of the test set.
6. Export the trained model to ONNX, save to blue_team/models/tx_risk.onnx. Save the exact feature engineering pipeline (feature names, order, any scalers) alongside the model.

PART B — blue_team/risk_model.py:
1. Load the ONNX model via onnxruntime at module load (singleton).
2. async def score_transaction(transaction, agent_history) -> TxRiskResult — computes the same features as training, runs ONNX inference, returns risk_score and (via SHAP or ONNX's feature contribution output) the top 3 contributing features with values, for the explainability panel.
3. Benchmark this — should be fast enough to NOT need a thread-pool executor, but use one if a single inference call exceeds ~2ms.
4. Test that risk_score and top features are stable/deterministic across repeated calls on the same input.
```

---

## Prompt 9 — `blue_team/decision_engine.py` (Fusion Orchestrator)

```
In blue_team/decision_engine.py, implement the fusion logic exactly as described in ARCHITECTURE.md sections 2 and 4:

1. async def decide(transaction: ProposedTransaction, mandate: IntentMandate, agent_history) -> Decision
2. Run injection_detector.score_content(), mandate_engine.check_mandate(), risk_model.score_transaction(), and payment_integrity.validate_payment_message() CONCURRENTLY via asyncio.gather with individual asyncio.wait_for timeouts (content: 20ms, mandate: 5ms, tx_risk: 5ms, schema: 5ms — pull these from environment variables, don't hardcode).
3. If any call times out or raises, catch it, log it, treat that signal as "UNKNOWN" — never fail the whole request and never default to "safe."
4. Fusion rules exactly as specified: hard violations from mandate_engine or payment_integrity force BLOCK regardless of other scores; any UNKNOWN signal forces at minimum REVIEW; otherwise compute the weighted combined_score (0.4 content + 0.4 tx_risk + 0.2 mandate soft_score) and apply BLOCK_THRESHOLD / REVIEW_THRESHOLD (configurable via env vars, sensible documented defaults).
5. Measure and record total latency_ms for the whole decide() call.
6. Build the human-readable explanation dict exactly as in ARCHITECTURE.md section 7 — always populated for REVIEW/BLOCK with a plain-language reason.
7. Persist the Decision to the decisions table via asyncio.create_task — fire-and-forget, not awaited before returning.
8. Tests: all-clean transaction returns ALLOW fast; a hard mandate violation returns BLOCK even if content/tx scores are both low; a simulated detector timeout returns REVIEW not ALLOW; total latency stays under 150ms budget under a simulated slow-detector scenario (mock one detector to sleep 200ms, confirm the timeout fires correctly).
```

---

## Prompt 10 — `simulation/merchant.py` + `simulation/payment_rail.py`

```
Two files:

PART A — simulation/merchant.py:
1. A mock in-memory catalog of ~15-20 products across a few categories (footwear, electronics, home goods). Each product has a title, description, and a list of reviews.
2. def inject_payload(product_id, payload_text, surface: Literal["description","review","api_field"]) — inserts the given payload into the specified content location, tagged internally with which sub-vector (from red_team/attack_discovery.py) it represents.

PART B — simulation/payment_rail.py:
1. class MockPaymentRail with def settle(self, transaction, decision) -> SettlementResult.
2. If decision.decision != "ALLOW", raise a SettlementBlockedError immediately — this must be enforced in code, not just documented as a rule. Add a code comment explaining this is the sandbox boundary referenced in the ethical safeguards.
3. On ALLOW, simulate a settlement delay (random 20-80ms) and return a SettlementResult with a mock settlement_id, timestamp, and status "SETTLED".
4. Explicitly ensure this can only ever write to an in-memory/mock ledger table (or the settlements table from db/models.py) — no network call, no real endpoint, ever.
5. Tests: settle() on an ALLOW decision succeeds and returns SETTLED; settle() on a BLOCK or REVIEW decision raises SettlementBlockedError, every time, with no exceptions.
```

---

## Prompt 11 — `simulation/agent.py` + `red_team/attack_generator.py` (Generate Pillar, LLM-enabled)

```
Build the offline attack-generation sandbox using LangGraph. These are the ONLY files allowed to call an LLM (Gemini, via Antigravity's model access).

PART A — red_team/attack_generator.py:
A LangGraph agent that, given a target sub_vector (pulled from red_team/attack_discovery.py's THREAT_TAXONOMY) and a target_manipulation goal, generates a payload string designed to look like normal catalog content while embedding an instruction a purchasing agent's LLM might follow. Use a system prompt framing this clearly as generating red-team test content for a security research sandbox. Hardcode a guardrail: refuse to generate anything containing real account numbers, real URLs, or content resembling a real payment network's actual message formats beyond structurally realistic field names. Every generated payload logs to the attack_logs table.

PART B — simulation/agent.py:
A LangGraph agent that receives a user's IntentMandate, autonomously browses simulation/merchant.py's catalog, reads descriptions/reviews, picks a product, and constructs a ProposedTransaction. Do NOT engineer this to be artificially vulnerable or artificially robust — it should behave like a reasonably-designed shopping agent, so whether an injection succeeds is a genuine test outcome, not scripted.

PART C — tie them together:
A run_simulation(sub_vector, goal) -> SimulationResult function: attacker generates payload -> merchant catalog is updated via inject_payload() -> buying agent runs its flow -> resulting ProposedTransaction (if any) is passed through blue_team/decision_engine.decide() -> if ALLOW, passed to simulation/payment_rail.settle() -> result returned along with whether the injection succeeded (did it evade detection AND violate the original mandate).

Remember: this entire module runs OFFLINE / on-demand, never inside the live decide() request path.
```

---

## Prompt 12 — `red_team/mutations.py` + `blue_team/feedback_loop.py` (Closed Loop)

```
Implement the closed loop using DEAP and RQ:

PART A — red_team/mutations.py:
Using DEAP, implement a genetic algorithm over injection payload text where individuals are interpretable "genes": phrasing template, urgency level, target field length, presence/absence of specific trigger phrases (e.g. "ignore previous instructions", "system note", "override"). Fitness function = 1 if the mutated payload evaded detection (logged as a false negative in attack_logs) in the last simulation round, weighted by how narrowly it evaded (how close risk_score was to BLOCK_THRESHOLD without crossing it). Implement crossover/mutation at the template/parameter level, not raw string mutation, so outputs stay coherent.

PART B — blue_team/feedback_loop.py:
1. RQ job process_false_negatives(): queries attack_logs for detected=False entries from the most recent round, runs red_team/mutations.py's GA for N generations to produce new payload variants, inserts these into simulation/merchant.py's catalog (via inject_payload) for the next round, adds newly-labeled examples to training_examples with source='feedback_loop'.
2. RQ job retrain_if_ready(): checks if enough new training_examples have accumulated (configurable threshold), retrains blue_team/injection_detector.py's FAISS index (add new embeddings) and re-runs training/train_lightgbm.py's evaluation against the FIXED held-out set from Prompt 8 — only promote the retrained model if precision/recall don't regress below a configured threshold vs the currently-deployed model. Log before/after metrics either way.
3. Wire both jobs to run automatically after each simulation round completes, and expose a manual POST /admin/trigger-feedback-round API endpoint (built in the next prompt) for on-demand triggering during the live demo.
4. Test: run 3 simulated rounds end-to-end with a deliberately weak initial injection_detector threshold, assert the attack success rate strictly decreases from round 1 to round 3 — this doubles as proof the closed loop functions.
```

---

## Prompt 13 — `api/main.py` (FastAPI Gateway + WebSocket)

```
Wire everything into a FastAPI application in api/main.py:

1. POST /transactions/authorize — accepts a ProposedTransaction + mandate_id + optional bypass_agentguard: bool = False. When bypass_agentguard is True AND os.environ["ALLOW_BYPASS_MODE"] == "true": skip decision_engine.decide() entirely, force decision="ALLOW" with explanation={"bypassed": true, "note": "AgentGuard disabled — demo comparison mode"}, log to bypass_demo_log (never to decisions/attack_logs), then call payment_rail.settle(). Otherwise, run the normal decide() flow, and if ALLOW, call payment_rail.settle().
2. POST /simulation/run — accepts sub_vector and goal, calls run_simulation() from Prompt 11, returns the SimulationResult.
3. POST /admin/trigger-feedback-round — manually invokes the RQ jobs from Prompt 12 for live demo control.
4. GET /identify/taxonomy — returns red_team/attack_discovery.py's describe_taxonomy() output, so the Identify pillar is queryable on the live running system.
5. GET /agents/{agent_id}/mandates, POST /agents, POST /mandates — basic CRUD for demo scenario setup.
6. WS /ws/live-feed — broadcasts every new Decision, SimulationResult, and SettlementResult as they happen, via an in-process pub/sub broadcaster class (no need for Redis pub/sub at hackathon scale, but structure it behind an interface so it could be swapped later for multi-replica deployments).
7. GET /health — checks DB connectivity, Redis connectivity, and that the ONNX model loaded successfully — used by Docker's healthcheck.
8. CORS middleware permissive enough for local frontend dev (http://localhost:5173).
9. Integration tests using httpx.AsyncClient covering the full authorize flow end-to-end, INCLUDING one test confirming bypass_agentguard is silently ignored (falls through to normal decide()) when ALLOW_BYPASS_MODE is not set to "true".
```

---

## Prompt 14 — React Dashboard (`web/dashboard/`)

```
Build the frontend — a live security-operations-style dashboard.

Components:

1. AttackTimeline.tsx — scrolling live feed (subscribed via a useLiveFeed WebSocket hook) showing each simulated attack as it happens: sub-vector badge, injected payload text (truncated with expand), resulting decision badge (ALLOW green / REVIEW yellow / BLOCK red), and — if settled — a "SETTLED" tag.

2. DecisionExplanationPanel.tsx — clicking a timeline entry shows the full explanation: which signals fired, the flagged span highlighted in the original text, the mandate diff if any, top contributing transaction features — a plain-language incident report, not a JSON dump.

3. SuccessRateChart.tsx — Recharts line chart of attack success rate per simulation round, updating live as feedback rounds run. Make this visually prominent and large — it's the single most important visual in the demo.

4. MerchantCatalogView.tsx — the mock storefront as the buying agent "sees" it, with the currently-injected payload visibly highlighted for the demo audience.

5. AgentGuardToggle.tsx — a prominent ON/OFF toggle switch, styled distinctly (red glow + persistent banner "⚠ Demo comparison mode — AgentGuard protection disabled" when OFF). Controls the bypass_agentguard flag sent with subsequent /transactions/authorize calls made by the control panel.

6. A control panel component: "Run Attack Scenario" (dropdown to pick sub_vector + goal, calls POST /simulation/run), "Trigger Feedback Round" (calls the admin endpoint), a reset/seed-demo-data button, and the AgentGuardToggle.

7. App.tsx layout: catalog view + control panel (including the toggle) on the left, live attack timeline in the center, success-rate chart and explanation panel on the right.

Use TailwindCSS + shadcn/ui (Card, Badge, Button, Dialog, Switch) for a clean, professional look — dark theme, consistent green/amber/red ALLOW/REVIEW/BLOCK color coding everywhere.
```

---

## Prompt 15 — `scripts/seed_demo_scenarios.py`

```
Build a script setting up 4-5 pre-validated, reliable demo scenarios for live judging — importing threat descriptions from red_team/attack_discovery.py, not hardcoding separate ones.

Requirements:
1. Pre-write both the user mandate and the exact injection payload text for each scenario (don't rely on the LLM attacker generating something different every run) — these are validated to reliably trigger the intended outcome.
2. Scenarios: (a) injection caught by injection_detector.py alone, (b) injection that slips past injection_detector.py but is caught by mandate_engine.py — demonstrates defense-in-depth explicitly, (c) multi-turn mandate-drift scenario caught by risk_model.py, (d) a fully legitimate transaction that cleanly ALLOWs (demonstrates low false positives), (e) one scenario specifically designed to be run twice — once with bypass_agentguard=True and once False — for the live AgentGuard ON/OFF comparison demo.
3. --scenario all|<name> CLI flag, and a --freeform flag that instead invokes the real red_team/attack_generator.py + simulation/agent.py LLM flow for a bonus live/unscripted demonstration, clearly separated from the primary scripted path.
4. Print a clear console summary after running: scenario name, expected outcome, actual outcome, latency, and settlement status — this doubles as a pre-demo smoke test.
```

---

## Prompt 16 — `scripts/evaluate.py`

```
Build the reporting script that produces the ONLY numbers that should ever be quoted in the docx/pitch:

1. Load the fixed held-out test set (same one used in training/train_lightgbm.py, never touched by feedback-loop augmentation).
2. Run the full blue_team/decision_engine.decide() pipeline (not just risk_model.py in isolation) against every example, comparing against ground truth.
3. Compute and print: Precision, Recall, F1, ROC-AUC overall, PLUS false-positive rate specifically on the "hard-legitimate" subset (tag these distinctly when building the held-out set — normal high-value purchases, long genuine reviews, legitimate category changes).
4. State the sample size used for every metric printed, and if it's too small to statistically support a strict claim (e.g., fewer than ~1000 examples for a sub-1% FPR claim), print an explicit warning rather than letting the number stand unqualified.
5. Output both a human-readable console report and a machine-readable report.json.
6. Add a --compare-rounds flag that runs against attack_logs data across feedback-loop rounds and outputs the success-rate-over-rounds series that feeds SuccessRateChart.tsx.
```

---

## Prompt 17 — Final Polish & Documentation Sync

```
Final pass before demo day:

1. Review every docstring and inline comment across the codebase for accuracy against ARCHITECTURE.md — if any implementation detail drifted during earlier prompts (e.g. a threshold value, a timeout number), update ARCHITECTURE.md to match the actual code, since the code is ground truth.
2. Add a docker-compose up --build smoke test (shell script or CI step) that boots the full stack, runs scripts/seed_demo_scenarios.py --scenario all, asserts all expected outcomes match, then tears down.
3. Confirm every FastAPI endpoint has clear summary/description fields for a judge browsing /docs, especially GET /identify/taxonomy.
4. Double check: no hardcoded secrets, no accidental real API keys committed, no reference anywhere in the repo to real payment endpoints, real bank names as functional integrations, or real cardholder data.
5. Confirm README.md's Quickstart section works exactly as written on a genuinely clean clone (test this yourself, don't assume) — including that ALLOW_BYPASS_MODE defaults to false and the toggle cannot be triggered accidentally.
```
