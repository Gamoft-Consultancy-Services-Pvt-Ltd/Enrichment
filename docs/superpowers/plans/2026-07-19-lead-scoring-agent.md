# Lead Scoring Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score each enriched lead HOT/WARM/COLD against its tenant's ACTIVE `tenant_config` and persist score, bucket and reasoning trace on the lead row.

**Architecture:** A per-signal LLM judgement (`judge.py`) feeds pure deterministic arithmetic (`engine.py`); `service.run_scoring` wires them and takes no DB session. `modules/orchestration/service.process_lead` calls it after enrichment and persists via `lead_ingestion.store_lead_score`.

**Tech Stack:** Python 3.13 · FastAPI · SQLAlchemy 2.0 + Alembic (async asyncpg) · ARQ · OpenRouter (`deepseek/deepseek-v4-flash`, function calling, temperature 0) · pydantic v2.

**Spec:** `docs/superpowers/specs/2026-07-19-lead-scoring-agent-design.md`

## Global Constraints

- **DEMO MODE — no tests in this plan.** The user explicitly deferred unit,
  integration and e2e tests to get a demo running. This overrides the repo's
  TDD convention. Task 5 substitutes a manual end-to-end run as the only
  verification. Tests from the spec's testing section remain owed work.
- `mypy` runs in **strict** mode over the whole repo. All new code must be fully
  annotated (`make typecheck` must pass).
- `ruff` line length 100.
- **Dependency rule:** `modules/scoring` may import `shared/`, `clients/`,
  `core/` — never another module's internals. Only `modules/orchestration` may
  import another module's public `service.py` (lite coupling rule).
- Reuse `shared.events.schemas.LeadBucket` (HOT/WARM/COLD). Do not define a new
  bucket enum.
- Do **not** modify the existing unintegrated `modules/scoring/` Epic 6 files
  (`agent.py`, `engine.py`, `db/`, `services/`, `worker/`, `routes_api/`,
  `schemas/`, `tests/`). New files land beside them. Name collision note:
  Task 3 creates `modules/scoring/scoring_engine.py`, **not** `engine.py`,
  because `modules/scoring/engine.py` already exists.
- Alembic head at plan time is `c1d2e3f4a5b6` (`add_enrichment_columns_to_leads`).

---

### Task 1: Add scoring columns to the leads table

**Files:**
- Modify: `modules/lead_ingestion/db/models.py:79-80` (insert after `enriched_at`)
- Create: `migrations/versions/d1e2f3a4b5c6_add_scoring_columns_to_leads.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Lead.lead_bucket: str | None`, `Lead.lead_score: float | None`,
  `Lead.scoring: dict[str, Any] | None`, `Lead.scored_at: datetime | None`.

- [ ] **Step 1: Add the four columns to the Lead model**

In `modules/lead_ingestion/db/models.py`, immediately after the `enriched_at`
line (currently line 80), insert:

```python
    # Scoring output, attached after enrichment. NULL until scoring runs, and
    # bucket/score stay NULL when no signal could be judged (trace still written).
    # lead_bucket is String, not a PG ENUM — same reasoning as pipeline_stage.
    lead_bucket: Mapped[str | None] = mapped_column(String, nullable=True)
    lead_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scoring: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add `Float` to the `sqlalchemy` import block at line 16-26 (keep alphabetical:
it goes between `ForeignKey` and `Index`).

- [ ] **Step 2: Create the migration**

Create `migrations/versions/d1e2f3a4b5c6_add_scoring_columns_to_leads.py`:

```python
"""add scoring columns to leads

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-07-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("lead_bucket", sa.String(), nullable=True))
    op.add_column("leads", sa.Column("lead_score", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("scoring", postgresql.JSONB(), nullable=True))
    op.add_column(
        "leads", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("leads", "scored_at")
    op.drop_column("leads", "scoring")
    op.drop_column("leads", "lead_score")
    op.drop_column("leads", "lead_bucket")
```

- [ ] **Step 3: Apply the migration**

Run: `make migrate`
Expected: `Running upgrade c1d2e3f4a5b6 -> d1e2f3a4b5c6, add scoring columns to leads`

- [ ] **Step 4: Typecheck**

Run: `make typecheck`
Expected: `Success: no issues found`

- [ ] **Step 5: Commit**

```bash
git add modules/lead_ingestion/db/models.py migrations/versions/d1e2f3a4b5c6_add_scoring_columns_to_leads.py
git commit -m "feat: add scoring columns to the leads table"
```

---

### Task 2: Scoring schemas

**Files:**
- Create: `modules/scoring/schemas.py`

**Interfaces:**
- Consumes: `shared.events.schemas.LeadBucket`.
- Produces: `Verdict` (StrEnum: `SATISFIED`/`NOT_SATISFIED`/`UNKNOWN`),
  `SignalJudgment(signal_id: str, verdict: Verdict, confidence: float, evidence: str)`,
  `DimensionScore(score: float, weight: float, satisfied: int, judged: int, unknown: int)`,
  `ScoringResult(config_version: int, total_score: float | None, bucket: LeadBucket | None, coverage: dict[str, int], dimensions: dict[str, DimensionScore], judgments: list[SignalJudgment])`.

- [ ] **Step 1: Write the schemas file**

Create `modules/scoring/schemas.py`:

```python
"""Scoring data contracts: LLM judgements in, score + bucket out.

Pure data — no I/O, no LLM. `ScoringResult.to_trace()` produces the JSONB blob
persisted on leads.scoring.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from shared.events.schemas import LeadBucket

# Judgements below this confidence are downgraded to UNKNOWN (project
# calibration rule: a low-confidence guess should neither help nor hurt).
CONFIDENCE_FLOOR = 0.7


class Verdict(StrEnum):
    """The LLM's answer to one signal's question."""

    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"


class SignalJudgment(BaseModel):
    """One signal, judged against the lead's enrichment + first-party data."""

    signal_id: str
    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    evidence: str = ""


class DimensionScore(BaseModel):
    """One dimension's outcome. `score` is satisfied/judged, or None if nothing
    in the dimension could be judged (that dimension is dropped from the total)."""

    score: float | None
    weight: float
    satisfied: int
    judged: int
    unknown: int


class ScoringResult(BaseModel):
    """The full scoring outcome for one lead."""

    config_version: int
    total_score: float | None
    bucket: LeadBucket | None
    coverage: dict[str, int]
    dimensions: dict[str, DimensionScore]
    judgments: list[SignalJudgment]

    def to_trace(self) -> dict[str, Any]:
        """The JSONB blob written to leads.scoring."""
        trace = self.model_dump(mode="json")
        trace["scored_at"] = datetime.now(UTC).isoformat()
        return trace
```

- [ ] **Step 2: Typecheck**

Run: `make typecheck`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add modules/scoring/schemas.py
git commit -m "feat: add scoring schemas"
```

---

### Task 3: Deterministic scoring engine

**Files:**
- Create: `modules/scoring/scoring_engine.py` (NOT `engine.py` — that name is taken by the unintegrated Epic 6 file)

**Interfaces:**
- Consumes: Task 2's `SignalJudgment`, `DimensionScore`, `ScoringResult`, `Verdict`, `CONFIDENCE_FLOOR`; `shared.tenant_config.schemas.TenantConfigRead`; `shared.events.schemas.LeadBucket`.
- Produces: `compute_score(config: TenantConfigRead, judgments: list[SignalJudgment]) -> ScoringResult`.

- [ ] **Step 1: Write the engine**

Create `modules/scoring/scoring_engine.py`:

```python
"""Pure deterministic scoring: judgements + weights + thresholds -> score, bucket.

No I/O and no LLM, so identical judgements plus an identical config version
always produce an identical score (ADR 0003).

Rules:
- A judgement below CONFIDENCE_FLOOR is downgraded to UNKNOWN.
- UNKNOWN signals are excluded from the denominator: a dimension scores
  satisfied / (satisfied + not_satisfied), not satisfied / total.
- A dimension where nothing could be judged is dropped entirely and the
  remaining weights are renormalized, so a lead is not punished for a
  dimension enrichment could not reach.
"""

from shared.events.schemas import LeadBucket
from shared.tenant_config.schemas import Dimension, TenantConfigRead

from modules.scoring.schemas import (
    CONFIDENCE_FLOOR,
    DimensionScore,
    ScoringResult,
    SignalJudgment,
    Verdict,
)


def compute_score(
    config: TenantConfigRead, judgments: list[SignalJudgment]
) -> ScoringResult:
    """Turn per-signal judgements into a total score and bucket."""
    by_id = {j.signal_id: j for j in judgments}

    dimensions: dict[str, DimensionScore] = {}
    total_satisfied = total_judged = total_unknown = 0

    # Iterate the enum, not a set comprehension over signals: set order varies
    # between runs, and the trace must be byte-identical for identical inputs.
    for dimension in Dimension:
        signals = [s for s in config.signals if s.dimension == dimension]
        if not signals:
            continue
        key = dimension.value.lower()
        satisfied = judged = unknown = 0

        for signal in signals:
            verdict = _effective_verdict(by_id.get(signal.id))
            if verdict is Verdict.UNKNOWN:
                unknown += 1
                continue
            judged += 1
            if verdict is Verdict.SATISFIED:
                satisfied += 1

        dimensions[key] = DimensionScore(
            score=(satisfied / judged) if judged else None,
            weight=float(getattr(config.weights, key)),
            satisfied=satisfied,
            judged=judged,
            unknown=unknown,
        )
        total_satisfied += satisfied
        total_judged += judged
        total_unknown += unknown

    total_score = _weighted_total(dimensions)
    bucket = _bucket_for(total_score, config)

    return ScoringResult(
        config_version=config.version,
        total_score=total_score,
        bucket=bucket,
        coverage={
            "judged": total_judged,
            "unknown": total_unknown,
            "total": total_judged + total_unknown,
        },
        dimensions=dimensions,
        judgments=judgments,
    )


def _effective_verdict(judgment: SignalJudgment | None) -> Verdict:
    """A missing judgement, or one the LLM was not confident about, is UNKNOWN."""
    if judgment is None:
        return Verdict.UNKNOWN
    if judgment.confidence < CONFIDENCE_FLOOR:
        return Verdict.UNKNOWN
    return judgment.verdict


def _weighted_total(dimensions: dict[str, DimensionScore]) -> float | None:
    """Weighted mean over scorable dimensions only, renormalized to 0-100.

    Returns None when no dimension could be scored at all.
    """
    scorable = [d for d in dimensions.values() if d.score is not None]
    weight_sum = sum(d.weight for d in scorable)
    if not scorable or weight_sum == 0:
        return None
    earned = sum((d.score or 0.0) * d.weight for d in scorable)
    return round(earned / weight_sum * 100, 2)


def _bucket_for(score: float | None, config: TenantConfigRead) -> LeadBucket | None:
    if score is None:
        return None
    if score >= config.thresholds.hot:
        return LeadBucket.HOT
    if score >= config.thresholds.warm:
        return LeadBucket.WARM
    return LeadBucket.COLD
```

- [ ] **Step 2: Typecheck**

Run: `make typecheck`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add modules/scoring/scoring_engine.py
git commit -m "feat: add the deterministic scoring engine"
```

---

### Task 4: LLM judge and the public service

**Files:**
- Create: `modules/scoring/judge.py`
- Create: `modules/scoring/service.py`

**Interfaces:**
- Consumes: Task 2 schemas; Task 3's `compute_score`; `clients.llm_client.call_with_tool`; `shared.events.schemas.EnrichmentResult`; `shared.tenant_config.schemas.TenantConfigRead`; `modules.lead_ingestion.db.models.Lead`.
- Produces: `judge_signals(config, enrichment, lead) -> list[SignalJudgment]` and `run_scoring(config, enrichment, lead) -> ScoringResult`.

- [ ] **Step 1: Write the judge**

Create `modules/scoring/judge.py`:

```python
"""The one LLM call in scoring: answer every signal question for one lead.

One call per lead, not per signal. The model never sees weights or thresholds
and never produces a score — it only answers each signal's yes/no question and
cites the field it relied on. Arithmetic happens in scoring_engine.py.
"""

import json
import logging
from typing import Any

from clients.llm_client import call_with_tool
from shared.events.schemas import EnrichmentResult
from shared.tenant_config.schemas import TenantConfigRead

from modules.lead_ingestion.db.models import Lead
from modules.scoring.schemas import SignalJudgment, Verdict

logger = logging.getLogger(__name__)

_TOOL_NAME = "judge_signals"
_TOOL_DESCRIPTION = (
    "Answer each lead-scoring signal question using ONLY the supplied lead and "
    "research data. Return one judgment per signal_id. Use SATISFIED when the "
    "data shows the signal is true, NOT_SATISFIED when it shows it is false, and "
    "UNKNOWN when the data does not answer the question. Never guess: UNKNOWN is "
    "always better than an unsupported answer. Every judgment must cite the field "
    "it relied on in `evidence`."
)
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal_id": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["SATISFIED", "NOT_SATISFIED", "UNKNOWN"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {
                        "type": "string",
                        "description": "The data field supporting this verdict.",
                    },
                },
                "required": ["signal_id", "verdict", "confidence", "evidence"],
            },
        }
    },
    "required": ["judgments"],
}


async def judge_signals(
    config: TenantConfigRead, enrichment: EnrichmentResult, lead: Lead
) -> list[SignalJudgment]:
    """Ask the LLM to answer every signal question for this lead."""
    raw = await call_with_tool(
        prompt=_build_prompt(config, enrichment, lead),
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        max_tokens=4096,
    )
    return _parse(raw, config)


def _build_prompt(
    config: TenantConfigRead, enrichment: EnrichmentResult, lead: Lead
) -> str:
    """Business context + signals + everything known about the lead."""
    signals = "\n".join(
        f"- {s.id} ({s.dimension.value}): {s.question}" for s in config.signals
    )
    lead_data = {
        "full_name": lead.full_name,
        "email": lead.email,
        "location": lead.location,
        "source_channel": lead.source_channel,
        "extra_fields": lead.extra_fields,
    }
    return (
        "You are scoring an inbound lead for a business.\n\n"
        f"## The business\n{json.dumps(config.business_profile, indent=2)}\n\n"
        f"## Their ideal customer profile\n{json.dumps(config.icp, indent=2)}\n\n"
        f"## The lead (first-party data, as it arrived)\n"
        f"{json.dumps(lead_data, indent=2, default=str)}\n\n"
        f"## Research findings about the lead\n"
        f"{json.dumps(enrichment.model_dump(mode='json'), indent=2)}\n\n"
        f"## Signals to judge\n{signals}\n\n"
        "Answer every signal listed above, exactly once each, using only the data "
        "shown. If the data does not answer a question, return UNKNOWN."
    )


def _parse(raw: dict[str, Any], config: TenantConfigRead) -> list[SignalJudgment]:
    """Keep judgements for known signals; drop anything the model invented."""
    known = {s.id for s in config.signals}
    judgments: list[SignalJudgment] = []
    seen: set[str] = set()

    for item in raw.get("judgments", []):
        signal_id = str(item.get("signal_id", ""))
        if signal_id not in known:
            logger.warning("scoring: dropping unrecognised signal_id %r", signal_id)
            continue
        if signal_id in seen:
            continue
        seen.add(signal_id)
        judgments.append(
            SignalJudgment(
                signal_id=signal_id,
                verdict=Verdict(item.get("verdict", Verdict.UNKNOWN)),
                confidence=float(item.get("confidence", 0.0)),
                evidence=str(item.get("evidence", "")),
            )
        )

    # Signals the model skipped are simply absent; the engine treats a missing
    # judgement as UNKNOWN, so no placeholder is needed here.
    if missing := known - seen:
        logger.info("scoring: %d signals unanswered by the model: %s", len(missing), missing)
    return judgments
```

- [ ] **Step 2: Write the public service**

Create `modules/scoring/service.py`:

```python
"""Public surface of the scoring module.

run_scoring is a function from (config, enrichment, lead) to a ScoringResult.
It takes no DB session — the caller loads the ACTIVE tenant_config and persists
the outcome. Callers import from here and nothing else in this module.
"""

from shared.events.schemas import EnrichmentResult
from shared.tenant_config.schemas import TenantConfigRead

from modules.lead_ingestion.db.models import Lead
from modules.scoring.judge import judge_signals
from modules.scoring.schemas import ScoringResult
from modules.scoring.scoring_engine import compute_score

__all__ = ["ScoringResult", "run_scoring"]


async def run_scoring(
    config: TenantConfigRead, enrichment: EnrichmentResult, lead: Lead
) -> ScoringResult:
    """Judge every signal with the LLM, then score deterministically."""
    judgments = await judge_signals(config, enrichment, lead)
    return compute_score(config, judgments)
```

- [ ] **Step 3: Typecheck and lint**

Run: `make typecheck && make lint`
Expected: `Success: no issues found` then `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add modules/scoring/judge.py modules/scoring/service.py
git commit -m "feat: add the LLM signal judge and the scoring service"
```

---

### Task 5: Persist the score and wire it into the pipeline

**Files:**
- Modify: `modules/lead_ingestion/db/repository.py` (add `set_lead_score` after `set_lead_enrichment`, currently lines 43-53)
- Modify: `modules/lead_ingestion/service.py` (add `store_lead_score` after `store_lead_enrichment`, currently lines 50-60; add to `__all__` near line 95)
- Modify: `modules/orchestration/service.py` (whole file)

**Interfaces:**
- Consumes: Task 4's `run_scoring`; Task 2's `ScoringResult`; `shared.tenant_config.service.get_active_config`.
- Produces: `store_lead_score(session, lead_id, result) -> Lead`; `process_lead` now scores.

- [ ] **Step 1: Add the repository write**

In `modules/lead_ingestion/db/repository.py`, after `set_lead_enrichment`
(ends line 53), add:

```python
async def set_lead_score(
    session: AsyncSession,
    lead_id: uuid.UUID,
    *,
    bucket: str | None,
    score: float | None,
    trace: dict[str, Any],
) -> Lead | None:
    """Write bucket/score/trace (+ scored_at) onto a lead row; None if missing."""
    lead = await get_lead_by_id(session, lead_id)
    if lead is None:
        return None
    lead.lead_bucket = bucket
    lead.lead_score = score
    lead.scoring = trace
    lead.scored_at = datetime.now(UTC)
    await session.flush()
    return lead
```

- [ ] **Step 2: Add the service wrapper**

In `modules/lead_ingestion/service.py`, after `store_lead_enrichment`
(ends line 60), add:

```python
async def store_lead_score(
    session: AsyncSession, lead_id: UUID, result: "ScoringResult"
) -> Lead:
    """Persist a ScoringResult onto the lead row (bucket, score, trace, scored_at).

    Raises ValueError if the lead does not exist.
    """
    lead = await repository.set_lead_score(
        session,
        lead_id,
        bucket=result.bucket.value if result.bucket else None,
        score=result.total_score,
        trace=result.to_trace(),
    )
    if lead is None:
        raise ValueError(f"lead {lead_id} not found")
    return lead
```

Add the import at the top of the file, under `TYPE_CHECKING` to avoid a
`lead_ingestion -> scoring` runtime dependency (only orchestration may couple
modules at runtime):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.scoring.schemas import ScoringResult
```

Add `"store_lead_score",` to `__all__` beside `"store_lead_enrichment"` (line ~96).

- [ ] **Step 3: Wire scoring into process_lead**

Replace the whole of `modules/orchestration/service.py` with:

```python
"""Orchestration: drive a received lead through enrichment, then scoring.

process_lead is the pipeline mediator — it sequences public services across
modules (tenant lookup → enrichment → persistence → scoring → persistence).
The hold/drain gate still slots in before enrichment. Under the lite coupling
rule it may import other modules' public service.py.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from modules.enrichment.service import run_enrichment
from modules.lead_ingestion.service import store_lead_enrichment, store_lead_score
from modules.scoring.service import run_scoring
from shared.events.schemas import LeadReceived
from shared.tenant.schemas import TenantRead
from shared.tenant.service import get_tenant
from shared.tenant_config.service import get_active_config

logger = logging.getLogger(__name__)


async def process_lead(session: AsyncSession, event: LeadReceived) -> None:
    """Enrich the lead carried on `event`, score it, and persist both results.

    Assumes the tenant is ACTIVE (the hold/drain gate is not built yet).
    Idempotent: both stores overwrite, so a redelivered event is safe.
    """
    tenant = await get_tenant(session, event.tenant_id)
    result = await run_enrichment(event.payload, TenantRead.model_validate(tenant))
    lead = await store_lead_enrichment(session, event.lead_id, result)

    config = await get_active_config(session, event.tenant_id)
    if config is None:
        # Not retryable — a retry cannot create a config, and raising here would
        # discard the enrichment we just persisted. Loud, but not fatal.
        logger.error(
            "scoring skipped: tenant %s has no ACTIVE tenant_config (lead %s)",
            event.tenant_id,
            event.lead_id,
        )
        return

    scoring = await run_scoring(config, result, lead)
    await store_lead_score(session, event.lead_id, scoring)
    logger.info(
        "lead %s scored %s (%s), coverage %s",
        event.lead_id,
        scoring.total_score,
        scoring.bucket,
        scoring.coverage,
    )
```

- [ ] **Step 4: Typecheck and lint**

Run: `make typecheck && make lint`
Expected: `Success: no issues found` then `All checks passed!`

- [ ] **Step 5: Verify end to end against a real lead**

This replaces the test suite the user deferred — it is the only verification
in this plan, so do not skip it.

```bash
make up                                  # app + worker + Postgres + Redis
uv run python scripts/enrich_lead_demo.py    # existing enrichment demo path
```

Then confirm the columns landed:

```bash
docker compose exec -T postgres psql -U postgres -d postgres -c \
  "SELECT id, lead_bucket, lead_score, scored_at,
          scoring->'coverage' AS coverage
     FROM leads
    WHERE scored_at IS NOT NULL
    ORDER BY scored_at DESC LIMIT 5;"
```

Expected: at least one row with `lead_bucket` in (HOT, WARM, COLD), a
`lead_score` between 0 and 100, and a non-null `coverage` object. If
`lead_bucket` is NULL, read `scoring->'judgments'` — every signal came back
UNKNOWN, which means either the enrichment was empty or the prompt needs work.

If `scripts/enrich_lead_demo.py` does not drive `process_lead`, invoke the
mediator directly in a `uv run python -c` snippet with a real `lead_id` and
`tenant_id` from the `leads` table instead.

- [ ] **Step 6: Commit**

```bash
git add modules/lead_ingestion/db/repository.py modules/lead_ingestion/service.py modules/orchestration/service.py
git commit -m "feat: score enriched leads and persist the result on the lead row"
```

---

### Task 6: Refresh the stale module status in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the "Project status" section — the "Still empty stubs" line naming `scoring`)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Correct the status**

CLAUDE.md currently lists `scoring` among "Still empty stubs", which is wrong on
two counts: the unintegrated Epic 6 code exists, and this plan adds a working
scorer. Remove `scoring` from that list and add a status entry:

```markdown
**`modules/scoring` (demo-complete):** `schemas.py`, `scoring_engine.py` (pure
deterministic math), `judge.py` (one LLM call per lead, per-signal verdicts),
`service.py` (`run_scoring`). Called by `modules/orchestration.process_lead`
after enrichment; result persisted to `leads.lead_bucket/lead_score/scoring/
scored_at` via `lead_ingestion.store_lead_score`. **No tests yet — deferred for
the demo.** The older Epic 6 files in this folder (`agent.py`, `engine.py`,
`db/`, `services/`, `worker/`, `routes_api/`, `schemas/`, `tests/`) are
unintegrated dead code pending deletion; do not import them.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: correct the scoring module status"
```

---

## Deferred work (owed after the demo)

Tracked here so it is not lost:

- **All tests from the spec's testing section** — unit (`scoring_engine.py`
  weights, renormalization, threshold boundaries, all-unknown, determinism;
  `judge.py` confidence floor, missing signals, unrecognised ids), integration
  (`store_lead_score`, migration), e2e (`process_lead`).
- Emitting `LeadScored` and HOT-lead notification.
- Deleting the unintegrated Epic 6 `modules/scoring/` files.
- Re-scoring when a tenant's config changes.
- An API endpoint to read scores.
