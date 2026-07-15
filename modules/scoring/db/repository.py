"""
LEAD-48 — Scoring repository (Epic 6: Lead Scoring Runtime)

The persistence layer between the service (LEAD-55) and the ORM models
(LEAD-47). It is the ONLY place that talks to the database for scoring;
everything above it works with plain Pydantic / domain objects and never
sees a SQLAlchemy session.

Responsibilities:
- LEAD-48-S1: persist a completed ScoringResult (save_scoring_result).
- LEAD-48-S2: load the latest score for a lead (get_latest_result).
- LEAD-48-S3: persist a tenant SignalSet config and *activate* it, atomically
  deactivating any prior active version so the "one active per tenant" rule
  (LEAD-47-S3) is never violated mid-transaction.
- LEAD-48-S4: load the active SignalSet for a tenant (get_active_signal_set).

Design choices:
- The repository receives an AsyncSession that it does NOT own — it neither
  opens nor commits the engine-level transaction by default. The caller
  (service / worker) controls the transaction boundary so several repository
  calls can share one unit of work. `flush()` is used to push INSERTs and get
  generated PKs back without ending the transaction; the caller commits.
  (`save_signal_set(..., activate=True)` is the one operation that must read
  -then-write atomically, so it flushes the deactivation before the insert.)
- Provenance-aware (COMP-1201): save_scoring_result accepts an optional
  `provenance` dict (prompt_version, model_version, signal sources, evidence
  refs) and folds it into the breakdown JSONB under a "provenance" key. No
  schema change to LEAD-47 is required now; these can be promoted to real
  columns later without touching callers.
- Reads return ORM records; mapping back to Pydantic ScoringResult/SignalSet
  is the service layer's job (keeps this file free of scoring-domain imports).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ScoringResultRecord, SignalSetRecord


class ScoringRepository:
    """Async data-access object for the two scoring tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # ScoringResult                                                       #
    # ------------------------------------------------------------------ #
    async def save_scoring_result(
        self,
        *,
        lead_id: str,
        tenant_id: str,
        signal_set_version: int,
        score: float,
        classification: str,
        breakdown: dict[str, Any],
        llm_adjusted: bool = False,
        provenance: dict[str, Any] | None = None,
    ) -> ScoringResultRecord:
        """LEAD-48-S1 — persist one scoring outcome.

        `breakdown` is the JSONB blob from ScoringResult.model_dump()
        (per-dimension points, confidence, soft deductions, etc.).
        `provenance` (COMP-1201) is merged into breakdown["provenance"] so
        prompt/model version and signal/evidence sources travel with the score
        without a schema change.
        """
        if provenance:
            # copy so we never mutate the caller's dict
            breakdown = {**breakdown, "provenance": provenance}

        record = ScoringResultRecord(
            lead_id=lead_id,
            tenant_id=tenant_id,
            signal_set_version=signal_set_version,
            score=score,
            classification=classification,
            llm_adjusted=llm_adjusted,
            breakdown=breakdown,
        )
        self._session.add(record)
        await self._session.flush()  # assigns record.id, stays in the tx
        return record

    async def get_latest_result(
        self, *, tenant_id: str, lead_id: str
    ) -> ScoringResultRecord | None:
        """LEAD-48-S2 — most recent score for a lead, or None if never scored."""
        stmt = (
            select(ScoringResultRecord)
            .where(
                ScoringResultRecord.tenant_id == tenant_id,
                ScoringResultRecord.lead_id == lead_id,
            )
            .order_by(
                ScoringResultRecord.scored_at.desc(),
                ScoringResultRecord.id.desc(),  # tie-break: same-tick rescores
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_results_for_lead(
        self, *, tenant_id: str, lead_id: str
    ) -> list[ScoringResultRecord]:
        """Full scoring history for a lead, newest first (audit / COMP-1201)."""
        stmt = (
            select(ScoringResultRecord)
            .where(
                ScoringResultRecord.tenant_id == tenant_id,
                ScoringResultRecord.lead_id == lead_id,
            )
            .order_by(
                ScoringResultRecord.scored_at.desc(),
                ScoringResultRecord.id.desc(),
            )
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def delete_results_for_lead(
        self, *, tenant_id: str, lead_id: str
    ) -> int:
        """COMP-303-ST5 — erase ALL scoring results (and their provenance, which
        lives in the breakdown JSONB) for one lead. Returns the row count deleted.

        This is the scoring-owned operation the erasure orchestration (COMP-303-
        ST2) calls when a data-subject erasure request is processed. The caller
        owns the transaction boundary, as elsewhere in this repository; this
        method issues the DELETE and flushes but does not commit.
        """
        result = await self._session.execute(
            delete(ScoringResultRecord).where(
                ScoringResultRecord.tenant_id == tenant_id,
                ScoringResultRecord.lead_id == lead_id,
            )
        )
        await self._session.flush()
        # rowcount is reliable for DELETE on the async drivers in use
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------ #
    # SignalSet                                                           #
    # ------------------------------------------------------------------ #
    async def get_active_signal_set(
        self, *, tenant_id: str
    ) -> SignalSetRecord | None:
        """LEAD-48-S4 — the one active scoring config for a tenant."""
        stmt = select(SignalSetRecord).where(
            SignalSetRecord.tenant_id == tenant_id,
            SignalSetRecord.is_active.is_(True),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def save_signal_set(
        self,
        *,
        tenant_id: str,
        version: int,
        payload: dict[str, Any],
        source_pipeline: str | None = None,
        activate: bool = True,
    ) -> SignalSetRecord:
        """LEAD-48-S3 — persist a config version, optionally making it active.

        When `activate` is True, any currently-active version for this tenant
        is deactivated first (and the change flushed) so the partial-unique
        index on (tenant_id) WHERE is_active never sees two active rows in the
        same transaction.
        """
        if activate:
            await self._session.execute(
                update(SignalSetRecord)
                .where(
                    SignalSetRecord.tenant_id == tenant_id,
                    SignalSetRecord.is_active.is_(True),
                )
                .values(is_active=False)
            )
            await self._session.flush()  # land the deactivation before insert

        record = SignalSetRecord(
            tenant_id=tenant_id,
            version=version,
            is_active=activate,
            source_pipeline=source_pipeline,
            payload=payload,
        )
        self._session.add(record)
        await self._session.flush()
        return record
