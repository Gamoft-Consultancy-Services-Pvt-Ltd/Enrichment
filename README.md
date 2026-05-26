# Lead Intelligence Engine

A multi-tenant lead intelligence platform built as a modular monolith in Python and FastAPI. The system ingests leads from multiple sources, enriches them with external data, scores them against per-tenant Ideal Customer Profiles, and surfaces the results through a dashboard with real-time hot-lead notifications.

---

## Project Structure

The application is a single deployable artifact, but it is internally split into independent modules with clean boundaries. This shape gives operational simplicity today and a clean path to extract modules into separate services later, without redesign.

```
mcp-leadgen-orchestrator/
├── main.py                  Application entry point
├── pyproject.toml           Project metadata and dependencies
├── alembic.ini              Database migration configuration
├── Makefile                 Shortcuts for common commands
├── Dockerfile               Builds the production container
├── docker-compose.yml       Runs the full local stack
├── .env.example             Template for environment variables
│
├── core/                    Shared infrastructure (config, database, cache, queue, logging)
├── auth/                    Google OAuth and role-based access control
├── shared/                  Cross-cutting domain concepts used by many modules
│   ├── tenant/              Master tenant record and lifecycle
│   ├── tenant_config/       Per-tenant configuration (weights, thresholds, flags)
│   ├── prompt_registry/     Versioned prompt templates and PersonaObject cache
│   ├── audit/               Append-only audit log
│   └── events/              Event types and dispatcher
├── clients/                 External service wrappers (Surepass, Probe42, NewsCatcher, Serper, Sonnet)
├── modules/                 Business logic, one folder per module
│   ├── tenant_onboarding/   Business profile, persona, ICP, signals, prompt generation
│   ├── lead_ingestion/      Accepts and filters leads from four sources
│   ├── orchestration/       Coordinates the per-lead workflow
│   ├── enrichment/          External data lookup with cache-first logic
│   ├── scoring/             Produces score, bucket, and reasoning for each lead
│   ├── reporting/           Read APIs for the dashboard
│   └── notification/        Hot-lead alerts with delivery and retry
├── api/                     HTTP layer, split by audience
│   ├── admin/               Routes for the platform admin interface
│   ├── tenant/              Routes for the tenant interface
│   └── public/              Login and health check (no auth)
├── workers/                 Background worker and job definitions
├── migrations/              Alembic database migrations
├── scripts/                 One-off operational scripts
├── docs/                    Architecture notes and ADRs
├── tests/                   Unit, integration, and end-to-end tests
└── .github/workflows/       GitHub Actions CI pipeline
```

For deeper architectural detail, see `docs/architecture.md`.

---

## Module Boundary Rule

Each module exposes a small public surface, typically `service.py` and `schemas.py`. Other modules import only that surface — they never reach into another module's internals. Cross-cutting data lives in `shared/`, never inside a single module. Following this rule is what keeps the future service extraction mechanical rather than a rewrite.

**Dependency direction:** `main.py` → `api/` and `workers/` → `modules/` and `auth/` → `shared/` → `clients/` → `core/`. No module imports another module. `core/` imports nothing from the application.

---

## Technology Stack

- **Language:** Python 3.13
- **Framework:** FastAPI
- **Package manager:** uv
- **Database:** PostgreSQL with SQLAlchemy and Alembic
- **Cache:** Redis (shared) and cachetools (in-process)
- **Background queue:** ARQ (Redis-backed, async)
- **LLM provider:** Anthropic Sonnet
- **Testing:** pytest with test-driven development
- **CI:** GitHub Actions

---

## Getting Started

### Prerequisites

Make sure the following are installed on your machine:

- **Python 3.13** (uv can install this for you if you do not have it)
- **uv** — Python package manager. Install instructions: https://docs.astral.sh/uv/
- **Docker** and **Docker Compose** — for running the local stack
- **Git** — for version control

### First-time setup

1. **Clone the repository**

```bash
   git clone <repo-url>
   cd mcp-leadgen-orchestrator
```

2. **Set up environment variables**

```bash
   cp .env.example .env
```

   Open `.env` in an editor and fill in the values. Ask a teammate for the shared API keys (Surepass, NewsCatcher, Serper, Probe42, Anthropic, Google OAuth). Never commit `.env` — it is excluded by `.gitignore`.

3. **Install dependencies**

```bash
   uv sync
```

   This creates a virtual environment in `.venv/`, installs Python 3.13 if needed, and installs every dependency declared in `pyproject.toml`. The first run takes 1 to 3 minutes; subsequent runs are nearly instant.

4. **Install pre-commit hooks (one-time per machine)**

```bash
   uv run pre-commit install
```

   This makes the lint, format, and type-check tools run automatically on every `git commit`, so problems are caught before they reach CI.

5. **Verify the local setup**

```bash
   uv run pytest
   uv run ruff check .
   uv run black --check .
   uv run mypy .
```

   All four should pass. If any fail, see Troubleshooting below.

### Running the application locally

The recommended way is via Docker Compose, which starts the full stack (app, worker, Postgres, Redis) at once:

```bash
docker-compose up
```

The app will be available at http://localhost:8000. Visit http://localhost:8000/health to confirm it is running — you should see `{"status":"ok"}`.

To stop the stack:

```bash
docker-compose down
```

### Running without Docker (app only, no database or Redis)

For quick iteration on code that does not need the database:

```bash
uv run uvicorn main:app --reload
```

---

## Common Commands

The `Makefile` provides shortcuts for everything you will run often:

| Command | What it does |
|---|---|
| `make install` | Install dependencies via uv |
| `make lint` | Run ruff (lint check) |
| `make format` | Auto-format code with black and ruff |
| `make typecheck` | Run mypy (type check) |
| `make test` | Run all tests |
| `make test-unit` | Run unit tests only |
| `make test-integration` | Run integration tests only |
| `make ci` | Run the full local CI pipeline (lint + typecheck + tests) |
| `make up` | Start the Docker stack |
| `make down` | Stop the Docker stack |
| `make logs` | Tail the Docker logs |
| `make migrate` | Apply pending database migrations |

---

## Development Workflow

### Branch protection (by team agreement)

GitHub Free tier does not enforce branch protection on private organisation repos. The team enforces it by agreement. **Everyone follows these four rules without exception:**

1. **Never push directly to `main`.** All changes go through a pull request.
2. **Wait for CI to be green before merging.** If CI is red, fix it first.
3. **No force-pushes to `main`, ever.**
4. **Do not merge your own pull request** without at least a thumbs-up from another team member.

### The standard workflow for any change

1. Make sure your local `main` is up to date:

```bash
   git checkout main
   git pull
```

2. Create a feature branch:

```bash
   git checkout -b feature/short-description
```

3. Make changes. Pre-commit will run automatically on `git commit` and may auto-fix formatting issues.

4. Push the feature branch:

```bash
   git push -u origin feature/short-description
```

5. Open a pull request on GitHub, targeting `main`. Wait for CI to be green. Get a teammate's thumbs-up. Merge.

6. Pull the merged changes back to your local `main`:

```bash
   git checkout main
   git pull
```

### Branch and commit naming conventions

**Branch names:**
- `feature/<short-description>` — for new features
- `fix/<short-description>` — for bug fixes
- `chore/<short-description>` — for infrastructure, tooling, refactors

**Commit messages:**
- `feat: <subject>` — for new features
- `fix: <subject>` — for bug fixes
- `chore: <subject>` — for infrastructure changes
- `docs: <subject>` — for documentation changes
- `test: <subject>` — for test changes

### Test-driven development

Tests come before code. For every function we add:

1. Write the test first, describing the expected input, output, and behaviour.
2. Run the test and confirm it fails (because the function does not exist yet).
3. Write the minimum code that makes the test pass.
4. Refactor for cleanliness while keeping the test green.

Unit tests go in `tests/unit/`. Integration tests (which use real Postgres and Redis) go in `tests/integration/`. End-to-end tests go in `tests/e2e/`.

---

## Continuous Integration

Every push and every pull request triggers the GitHub Actions pipeline defined in `.github/workflows/ci.yml`. The pipeline runs three jobs in parallel:

- **lint-and-typecheck** — runs ruff, black (check only), and mypy on the whole codebase
- **test** — runs the test suite against a real Postgres and Redis (started as service containers)
- **docker-build** — verifies the Docker image builds cleanly

A pull request cannot be merged until all three jobs are green.

---

## Troubleshooting

**`uv sync` fails on a specific package.**
Check whether the package supports Python 3.13. If not, find a compatible version or alternative.

**Pre-commit hook fails on `mypy` with "Untyped decorator" errors.**
This is already handled in `pyproject.toml` via `disallow_untyped_decorators = false`. If you still see the error, confirm your `pyproject.toml` includes that line under `[tool.mypy]`.

**Docker Compose cannot connect to Postgres.**
The healthcheck in `docker-compose.yml` waits for Postgres to be ready. If you see connection errors at startup, give it 30 seconds. If it still fails, run `docker-compose down -v` to wipe the volume and try again.

**`docker-compose up` says port 8000 is already in use.**
You have something else running on port 8000. Either stop it, or change the port mapping in `docker-compose.yml` from `8000:8000` to `8001:8000`.

**Tests pass locally but fail in CI.**
Check that you have not committed any local-only file (an `.env`, a database dump) by accident. Also check that all your test files were committed — sometimes a new test file gets missed.

---

## Documentation

- `docs/architecture.md` — full architecture and folder structure rationale
- `docs/adr/` — Architecture Decision Records (one file per major design decision)

When you make a significant architectural choice, write a short ADR (Architecture Decision Record) in `docs/adr/` so the reasoning is preserved.

---

## Project Status

Phase 0 — early development. The foundation skeleton, CI pipeline, and Docker setup are in place. Business logic modules are not yet implemented. See `docs/architecture.md` for the planned scope.
