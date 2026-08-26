# Contributing to AgentGuard

Welcome to the AgentGuard team! This guide covers our workflow for the Mastercard Innovation Challenge 2026.

---

## Getting Started

1. **Clone the repo**
   ```bash
   git clone https://github.com/YOUR_USERNAME/AgentGuard.git
   cd AgentGuard
   ```

2. **Set up your environment**
   ```bash
   cp .env.example .env
   # Edit .env with your local settings if needed
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   cd web/dashboard && npm install && cd ../..
   ```

4. **Start services**
   ```bash
   docker compose up --build
   ```

---

## Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable, deployable code only. Never push directly. |
| `dev` | Integration branch. All features merge here first. |
| `feature/<name>` | Individual feature branches created from `dev`. |
| `hotfix/<name>` | Emergency fixes branching from `main`. |

---

## Workflow

### 1. Start a new feature

```bash
git checkout dev
git pull origin dev
git checkout -b feature/your-feature-name
```

### 2. Make your changes

- Follow the prompt order in `PROMPTS.md`
- Write tests for your component
- Keep commits small and descriptive

### 3. Commit messages

Use conventional commits:

```
feat: add injection detector FAISS indexing
fix: correct mandate expiration timezone handling
test: add decision engine timeout tests
docs: update API endpoint descriptions
refactor: extract fusion weights to env vars
```

### 4. Push and create a Pull Request

```bash
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub targeting the `dev` branch.

### 5. Code review

- At least 1 team member must review before merging
- Check that tests pass
- Verify no hardcoded secrets or real API keys

---

## Module Ownership

Assign yourselves to modules to avoid conflicts:

| Module | Owner | Prompts |
|--------|-------|---------|
| `red_team/` | TBD | 4, 11, 12 |
| `blue_team/` | TBD | 5, 6, 7, 8, 9, 12 |
| `simulation/` | TBD | 10, 11 |
| `api/` + `db/` | TBD | 2, 3, 13 |
| `web/dashboard/` | TBD | 14 |
| `scripts/` | TBD | 15, 16 |

---

## Non-Negotiable Rules

1. **NO LLM calls** in the real-time decision path (`blue_team/`)
2. **150ms total latency budget** for `decision_engine.decide()`
3. **SANDBOX only** -- no real payment endpoints, ever
4. **Never hardcode metrics** -- all numbers come from `scripts/evaluate.py`
5. **Always test** before pushing

---

## Running Tests

```bash
# All tests
pytest tests/

# Specific module
pytest tests/test_mandate_engine.py

# With coverage
pytest --cov=blue_team tests/
```

---

## Need Help?

- Check `PROMPTS.md` for detailed build instructions per component
- Ask in the team chat before making cross-module changes
- When in doubt, create a draft PR to discuss
