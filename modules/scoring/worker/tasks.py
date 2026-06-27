"""
LEAD-57 — Scoring worker (Epic 6: Lead Scoring Runtime)

Async background entry point for scoring leads off the request path. The
upstream enrichment pipeline (Epic 5) enqueues a job per enriched lead; this
worker pulls it and runs the same ScoringService used by the API, so there is
exactly one scoring code path whether a score is requested synchronously or
produced in a batch.

Uses ARQ (the async task queue already in the stack alongside Redis), but the
task function is a plain async callable that takes the service via the ARQ
context, so it is testable without a running Redis/ARQ.

Design choices:
- The worker holds no scoring logic. score_lead_task unpacks the job, calls
  ScoringService.score_lead, and returns a small JSON-serialisable summary that
  ARQ stores as the job result.
- The service is built once at worker startup (on_startup) and stashed in the
  ARQ context, so every job reuses the same engine/cache/session factory rather
  than rebuilding them per task.
- Failures are allowed to propagate so ARQ's retry/backoff handles them; a
  SignalSetNotConfigured is re-raised as a permanent failure (no retry) because
  retrying won't help until onboarding writes a config.
"""

from __future__ import annotations

from typing import Any

from services.service import ScoringService, SignalSetNotConfigured


class PermanentJobError(Exception):
    """A failure that should NOT be retried (e.g. tenant not configured)."""


async def score_lead_task(
    ctx: dict[str, Any],
    *,
    tenant_id: str,
    lead_id: str,
    features: dict[str, Any],
) -> dict[str, Any]:
    """ARQ task: score one enriched lead.

    `ctx["scoring_service"]` is populated by on_startup below. Returns a compact
    summary stored as the ARQ job result; the full breakdown is already
    persisted by the service.
    """
    service: ScoringService = ctx["scoring_service"]
    merged_features = {"lead_id": lead_id, **features}

    try:
        outcome = await service.score_lead(
            tenant_id=tenant_id, features=merged_features
        )
    except SignalSetNotConfigured as exc:
        # don't burn retries on a config that isn't there yet
        raise PermanentJobError(str(exc)) from exc

    result = outcome.result
    return {
        "lead_id": lead_id,
        "tenant_id": tenant_id,
        "score": _attr(result, "score", 0.0),
        "classification": _attr(result, "classification", "cold"),
        "llm_adjusted": outcome.from_llm_fallback,
        "record_id": outcome.record_id,
    }


# --------------------------------------------------------------------------- #
# ARQ worker settings                                                          #
# --------------------------------------------------------------------------- #
async def on_startup(ctx: dict[str, Any]) -> None:
    """Build the ScoringService once and reuse it across all jobs.

    The concrete collaborators (engine, adapter, cache, session factory, rating
    client) are constructed by build_scoring_service, which lives in the app
    composition root so this module stays free of those imports. Imported lazily
    here to keep the task function unit-testable in isolation.
    """
    from app.composition import build_scoring_service  # composition root

    ctx["scoring_service"] = build_scoring_service()


async def on_shutdown(ctx: dict[str, Any]) -> None:
    ctx.pop("scoring_service", None)


class WorkerSettings:
    """ARQ entry point: `arq worker.tasks.WorkerSettings`."""

    functions = [score_lead_task]
    on_startup = on_startup
    on_shutdown = on_shutdown
    # redis_settings supplied from app config at deploy time, e.g.
    # redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)


def _attr(obj: Any, name: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
