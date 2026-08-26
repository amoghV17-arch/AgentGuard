# AgentGuard

> **Security infrastructure layer for agentic AI payments**
> Built for the **Mastercard Innovation Challenge 2026**

---

## What is AgentGuard?

AgentGuard sits between an autonomous AI shopping/payment agent and a payment rail. It defends against **indirect prompt injection attacks** -- where an attacker hides malicious instructions inside content an AI agent reads (product descriptions, reviews, API responses) -- to prevent the agent from:

- Exceeding its spending mandate
- Changing a payment's purpose code
- Redirecting settlement

Four independent signals feed a fusion decision engine that returns **ALLOW**, **REVIEW**, or **BLOCK** within a **150ms budget**.

---

## Architecture

```
AI Shopping Agent --> AgentGuard Pipeline --> Mock Payment Rail
                         |
          +--------------+--------------+
          |              |              |
   Injection      Mandate         Payment
   Detector       Engine         Integrity
  (Embeddings) (Deterministic)  (ISO 20022)
          |              |              |
          +--------------+--------------+
                         |
               Risk Model (LightGBM/ONNX)
                         |
              Fusion Decision Engine
              ALLOW | REVIEW | BLOCK
```

---

## Repository Structure

```
AgentGuard/
|-- red_team/              # IDENTIFY + attack generation
|   |-- attack_discovery.py
|   |-- attack_generator.py
|   +-- mutations.py
|-- simulation/            # GENERATE (shared battleground)
|   |-- agent.py
|   |-- merchant.py
|   +-- payment_rail.py
|-- blue_team/             # DEFEND
|   |-- decision_engine.py
|   |-- injection_detector.py
|   |-- mandate_engine.py
|   |-- risk_model.py
|   |-- payment_integrity.py
|   +-- feedback_loop.py
|-- api/                   # FastAPI + WebSocket gateway
|   |-- main.py
|   +-- models/
|-- db/                    # SQLAlchemy models + Alembic migrations
|   |-- models.py
|   +-- migrations/
|-- training/              # LightGBM training pipeline
|   |-- train_lightgbm.py
|   +-- data/
|-- web/dashboard/         # React + Vite + TypeScript frontend
|-- scripts/               # Demo seeding + evaluation
|   |-- seed_demo_scenarios.py
|   +-- evaluate.py
+-- tests/
```

---

## Quickstart

### Prerequisites
- Docker and Docker Compose
- Python 3.11+
- Node.js 18+

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/AgentGuard.git
cd AgentGuard

# 2. Copy environment variables
cp .env.example .env

# 3. Start all services
docker compose up --build

# 4. Seed demo scenarios
make seed-demo

# 5. Open the dashboard at http://localhost:5173
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, async, WebSockets |
| Database | PostgreSQL + Redis |
| Injection Detection | sentence-transformers (all-MiniLM-L6-v2) + FAISS |
| Risk Model | LightGBM to ONNX Runtime |
| Attack Generation | LangGraph + Gemini |
| Evolution Loop | DEAP (genetic algorithm) + RQ (Redis Queue) |
| Frontend | React + Vite + TypeScript + TailwindCSS + shadcn/ui + Recharts |
| Deployment | Docker Compose |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for team workflow guidelines.

### Quick Workflow

```bash
git checkout dev
git pull origin dev
git checkout -b feature/your-feature-name
# ... make changes ...
git add .
git commit -m "feat: description of your change"
git push origin feature/your-feature-name
# Open a Pull Request targeting dev
```

---

## Team

| Member | Role | Focus Area |
|--------|------|------------|
| | | |
| | | |
| | | |
| | | |
| | | |

---

## Ethical Safeguards

- This is a **SANDBOX** system -- no component connects to real payment gateways, banks, or merchants
- `simulation/payment_rail.py` only writes to an in-memory/mock ledger
- Generated attack payloads never resolve to real external endpoints
- All metrics are computed by `scripts/evaluate.py` against held-out datasets -- never hardcoded

---

## License

This project is built for the Mastercard Innovation Challenge 2026.
