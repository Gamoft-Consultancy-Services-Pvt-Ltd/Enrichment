# Lead scoring agent: signals + enrichment → HOT/WARM/COLD

- **Status:** Design (approved in brainstorming, pending spec review)
- **Date:** 2026-07-19
- **Branch:** `feature/orchestration-enrichment-trigger`

## Problem

`modules/orchestration/service.py::process_lead` currently ends at
`store_lead_enrichment`. Its docstring reserves the next seam — "the hold/drain
gate (before enrichment) and scoring (after) will slot in later" — but nothing
scores a lead, so `tenant_configs` (signals, weights, thresholds) is written by
onboarding and never read at runtime.

Goal: score each enriched lead against its tenant's ACTIVE `tenant_config` and
persist the outcome on the lead row, so the ingestion → enrichment → scoring
chain runs end-to-end.

### The existing `modules/scoring/` code is not used

One squashed commit (`8cb9754 "Epic 6 Lead Scoring module"`) added ~3,400 lines:
a deterministic engine, an LLM-fallback agent, ORM models, repository, cache,
worker tasks, API routes and 12 test files. It has never been integrated, and
three things block it:

1. **It does not import.** Every file uses flat imports (`from engine import
   ScoringEngine`, `from schemas.signal_set import ...`) rather than
   `modules.scoring.*`. Nothing in `main.py`, `api/` or `workers/` references it,
   and its two tables have no Alembic migration.
2. **It duplicates `shared/tenant_config`.** `SignalSetRecord` re-implements
   per-tenant versioning and the "one ACTIVE per tenant" partial unique index.
3. **Its input contracts do not exist.** `signal_set_adapter.py` expects signals
   carrying a per-signal `weight`, a machine-readable `condition`, `points` and
   `hard_block`; the real `tenant_config.Signal` is `{id, dimension, question}`.
   `lead_mapper.py` expects the lead to arrive with `signal_values` (per-signal
   booleans) already computed; the real `EnrichmentResult` is
   `{company_info, person_info, sources, confidence, reasoning_trace}` — and
   **nothing anywhere computes `signal_values`**.

That third point is the substantive gap: signals are natural-language questions
and enrichment output is unstructured JSON, so evaluating a signal is inherently
an LLM judgement. The Epic 6 design confines the LLM to a gap-filling fallback,
which cannot bridge it.

**Decision: build fresh; leave the Epic 6 files untouched for now.** Deleting
them is a separate cleanup once this design is running. CLAUDE.md's claim that
`modules/scoring` is an empty stub is stale and should be corrected.

## Decisions and rationale

1. **Per-signal LLM judgement, deterministic arithmetic.** The LLM answers each
   signal's question (`SATISFIED` / `NOT_SATISFIED` / `UNKNOWN`) with confidence
   and evidence; pure Python then applies weights and thresholds. The LLM never
   sees the weights and never produces the score. This satisfies ADR 0003
   (identical inputs + identical config version → identical score) and yields a
   per-signal reasoning trace. Rejected: per-dimension judgement (coarser trace —
   cannot see which signal drove the result) and one-shot LLM scoring (the model
   does the arithmetic; weakest determinism, hardest to audit).

2. **`UNKNOWN` signals are excluded from the denominator, not counted as false.**
   A dimension with 4 signals, 1 satisfied and 2 unknown scores `1/2 = 0.50`, not
   `1/4 = 0.25`. Thin enrichment would otherwise drag every lead toward COLD,
   contradicting the project calibration rule that a missed lead costs more than
   a processed non-lead. Coverage counts are recorded in the trace so a
   thinly-evidenced score is visible rather than silently confident.

3. **A fully-unknown dimension is dropped and remaining weights renormalized.**
   `satisfied / judged` is `0/0` when no signal in a dimension could be judged.
   Treating it as zero would reintroduce exactly the drag decision 2 rejects, so
   the dimension is excluded and the total is divided by the summed weight of
   scorable dimensions. A lead is not punished for a dimension enrichment could
   not reach.

4. **Four columns on `leads`, mirroring the enrichment precedent.** `leads`
   already carries `enrichment` (JSONB) + `enriched_at`; scoring adds
   `lead_bucket`, `lead_score`, `scoring` (JSONB trace) and `scored_at`. Storing
   only the bucket would discard the numeric score and reasoning, violating ADR
   0003 and leaving "why is this lead HOT?" unanswerable. A separate
   `scoring_results` history table is deferred — it can be added later without
   changing these columns.

5. **Inline in `process_lead`, not a separate ARQ job.** Scoring becomes a fourth
   step in the existing mediator, filling the seam its docstring reserved. This
   follows the project's recurring "prefer the simpler option now, defer Option B
   until needed" pattern. Accepted cost: an LLM failure retries the whole chain,
   re-running enrichment (correct, since enrichment overwrites, but not free). If
   that becomes a cost problem, splitting scoring into its own
   `score:{lead_id}`-deduped job is a contained change.

6. **`run_scoring` takes no DB session.** Orchestration loads the ACTIVE config
   via `shared.tenant_config.service.get_active_config` and passes it in;
   scoring is a function from (config, enrichment) to a result and never touches
   the database.

7. **Persistence lives in `lead_ingestion`, not `scoring`.** The `leads` table
   belongs to `lead_ingestion`, so `store_lead_score` is added there as a sibling
   of `store_lead_enrichment`. Scoring writing to `leads` directly would breach
   the module boundary.

8. **Reuse the existing frozen contracts.** `shared/events` already defines
   `LeadBucket` (HOT/WARM/COLD) and `LeadScored`. No new bucket type.

## Architecture

```
modules/scoring/
  schemas.py   # SignalJudgment, DimensionScore, ScoringResult (pure data)
  judge.py     # the one LLM call: signals + enrichment -> judgments
  engine.py    # pure math: judgments + weights + thresholds -> score, bucket
  service.py   # public surface: run_scoring(...) — the only import point
```

`engine.py` has no I/O and no LLM. That is what makes ADR 0003 determinism
testable and lets most tests run without a DB or network.

`run_scoring` takes three inputs, all already in hand at the call site:

- `config: TenantConfigRead` — signals, weights, thresholds, business profile, ICP.
- `enrichment: EnrichmentResult` — the external research findings.
- `lead: Lead` — first-party fields (`full_name`, `email`, `location`,
  `source_channel`, `extra_fields`). Enrichment covers company and person
  research, but Engagement and Behaviour signals are typically answerable only
  from how the lead arrived and what they said, so the lead row is judged
  alongside the enrichment rather than in place of it.

Flow through the existing seam:

```
process_lead(session, event)
  tenant  = get_tenant(...)                        # existing
  result  = run_enrichment(...)                    # existing
  store_lead_enrichment(...)                       # existing
  config  = get_active_config(session, tenant_id)  # shared/tenant_config
  scoring = run_scoring(config, result, lead)      # NEW
  store_lead_score(session, lead_id, scoring)      # NEW
```

## Scoring mechanics

### The LLM call (`judge.py`)

One `call_with_tool` per lead — not one per signal. Input: the tenant's business
profile and ICP (for context), the full signal list, and the enrichment JSON.
Output, forced via function calling at `temperature=0`:

```json
{"judgments": [
  {"signal_id": "fit_company_size",
   "verdict": "SATISFIED",
   "confidence": 0.85,
   "evidence": "company_info.employee_count = 240"}
]}
```

`verdict` is one of `SATISFIED`, `NOT_SATISFIED`, `UNKNOWN`. `evidence` must
quote or cite the enrichment field relied on — this makes the trace auditable and
discourages unsupported assertions.

Applying the project calibration rule (confidence < 0.7 is not trustworthy): any
judgment below 0.7 is **downgraded to `UNKNOWN`**. Combined with decision 2, a
low-confidence guess neither helps nor hurts the lead.

### The math (`engine.py`)

Per dimension, over judged signals only:

```
dimension_score = satisfied / (satisfied + not_satisfied)
```

Total, renormalized across dimensions that could be scored:

```
total = Σ(dimension_score × weight) / Σ(weight of scorable dimensions) × 100
```

If `context` (weight 0.10) is entirely unknown, the other four are scored across
their 0.90 of weight and scaled back to 100.

Bucketing uses the tenant's own `thresholds` from `tenant_config`: `HOT` at
`score >= thresholds.hot`, `WARM` at `score >= thresholds.warm`, else `COLD`.
Defaults are 80/55; per-tenant values win.

If no signal in any dimension could be judged, `lead_bucket` and `lead_score` are
left `NULL` and the trace is still written so the reason is visible.

### Persisted trace (`leads.scoring`)

```json
{"config_version": 3,
 "total_score": 60.0,
 "bucket": "WARM",
 "coverage": {"judged": 9, "unknown": 3, "total": 12},
 "dimensions": {"fit": {"score": 0.75, "weight": 0.30,
                        "satisfied": 3, "judged": 4, "unknown": 0}},
 "judgments": [ ... per-signal, as above ... ],
 "scored_at": "2026-07-19T..."}
```

`config_version` pins which `tenant_config` version produced the score, so stale
scores are distinguishable from current ones after a config change. `coverage` is
the first thing a reviewer checks to judge whether a score is trustworthy.

## Failure handling

Behaviour is split by whether a retry could help.

| Failure | Behaviour | Why |
|---|---|---|
| No ACTIVE `tenant_config` | Error-level log; lead left unscored (`bucket = NULL`); no exception | Retrying cannot create a config, and crashing would lose the enrichment that already succeeded |
| LLM call fails / malformed output | Propagate `ExternalServiceError` | Transient; ARQ retry is the right response |
| LLM omits a signal | Treated as `UNKNOWN` | Same path as any unjudgeable signal |
| LLM returns an unrecognised `signal_id` | Ignored, warning logged | Hallucinated signals must not enter the math |
| Enrichment failed | Never reached | `run_enrichment` raises before scoring runs |

The "no ACTIVE config" case is deliberately loud (error-level, with tenant and
lead ids) rather than silent — consistent with the recent
`fix: stop onboarding failing silently on research problems`.

## Testing

TDD as usual: failing test first, watch it fail for the right reason, then
minimum code. The pure-function split means most tests need no DB and no network.

- **Unit, `engine.py`:** weight arithmetic; renormalization when a dimension is
  fully unknown; threshold boundaries (exactly 80, exactly 55); all-signals-
  unknown → `NULL` bucket; determinism (same input twice → identical output).
- **Unit, `judge.py`:** mocked `call_with_tool` — confidence < 0.7 downgraded to
  `UNKNOWN`; omitted signals defaulted to `UNKNOWN`; unrecognised ids dropped.
- **Unit, `service.py`:** judge + engine wired together against a fixture config.
- **Integration:** `store_lead_score` against real Postgres; the migration
  applies cleanly.
- **E2E:** `process_lead` end to end with a stubbed LLM, asserting all four
  columns land on the lead row.

## Migration

One Alembic revision adding to `leads`, all nullable, no backfill (existing rows
are simply unscored):

| Column | Type |
|---|---|
| `lead_bucket` | `String` (not a PG ENUM — matches the `pipeline_stage` precedent) |
| `lead_score` | `Float` |
| `scoring` | `JSONB` |
| `scored_at` | `timestamptz` |

## Out of scope

Deliberately deferred; none require changing this design to add later:

- Emitting `LeadScored` and HOT-lead notification (the natural next piece).
- Re-scoring existing leads when a tenant's config changes — leads keep their
  old scores, distinguishable via `config_version`.
- Any API endpoint to read scores.
- A `scoring_results` history table.
- Deleting the unintegrated Epic 6 `modules/scoring/` code.
- The hold/drain gate for non-ACTIVE tenants (unchanged from the enrichment
  trigger design).