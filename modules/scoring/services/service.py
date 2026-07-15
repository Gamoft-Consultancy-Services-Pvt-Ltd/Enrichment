"""
LEAD-55 — Scoring service (Epic 6: Lead Scoring Runtime)

The orchestrator that ties the whole runtime together. Everything below it
(engine, adapter, repository, cache, rating client) does one job; the service
sequences them into the end-to-end flow:

    load active SignalSet (cache -> repo)
        -> adapt to scoring weights
        -> score the lead (deterministic engine)
        -> if low confidence: Sonnet fallback estimates skipped signals, rescore
        -> assemble provenance (COMP-1201)
        -> persist the result (repository)
        -> return the domain ScoringResult

It owns the transaction boundary (the repository deliberately does not commit),
and it is the single place provenance is assembled, so COMP-1201 has exactly
one wiring point.

Design choices:
- Dependencies are injected (engine, rating client, cache, repo factory). The
  service imports no concrete DB/LLM client directly, which keeps it unit-
  testable with fakes and free of import cycles.
- score_lead does NOT open its own DB session for reads that the cache can
  serve; it only takes a session to persist. The config read goes through the
  cache, whose loader opens a short-lived session via the injected factory.
- Provenance is assembled here because this is the only layer that sees all
  the pieces at once: which SignalSet version, whether the LLM ran and with
  what prompt/model version, the confidence, and the per-dimension points.
- invalidate_signal_set is exposed so the onboarding/worker path that writes a
  new config can drop the cache entry immediately (TTL is only a backstop).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.cache import SignalSetCache
from db.repository import ScoringRepository


# --------------------------------------------------------------------------- #
# Structural typing for the injected collaborators                            #
# --------------------------------------------------------------------------- #
class Engine(Protocol):
    """The deterministic ScoringEngine (LEAD-51)."""

    def score(self, features: Any, weights: Any) -> Any: ...


class RatingClient(Protocol):
    """The Sonnet fallback (LEAD-52). Estimates skipped signals only."""

    async def estimate_skipped(self, features: Any, weights: Any) -> Any: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def model_version(self) -> str: ...


class Adapter(Protocol):
    """signal_set_adapter (LEAD-50): payload dict -> scoring weights."""

    def to_weights(self, payload: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class ScoreOutcome:
    """What the service returns to the API/worker layer."""

    result: Any  # the domain ScoringResult (Pydantic)
    record_id: int  # PK of the persisted row
    from_llm_fallback: bool


class SignalSetNotConfigured(Exception):
    """Raised when a tenant has no active SignalSet to score against."""

    def __init__(self, tenant_id: str) -> None:
        super().__init__(f"No active SignalSet configured for tenant {tenant_id!r}")
        self.tenant_id = tenant_id


# --------------------------------------------------------------------------- #
# Service                                                                      #
# --------------------------------------------------------------------------- #
class ScoringService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cache: SignalSetCache,
        adapter: Adapter,
        engine: Engine,
        rating_client: RatingClient | None = None,
        confidence_floor: float = 0.5,
        audit_sink: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache
        self._adapter = adapter
        self._engine = engine
        self._rating_client = rating_client
        # below this derived-confidence we invoke the Sonnet fallback
        self._confidence_floor = confidence_floor
        # COMP-801-ST4: where AI-activity audit events go. Defaults to the
        # logging sink so events are emitted even before Epic 1's sink is wired.
        if audit_sink is None:
            from services.audit import LoggingAuditSink

            audit_sink = LoggingAuditSink()
        self._audit_sink = audit_sink

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    async def score_lead(self, *, tenant_id: str, features: Any) -> ScoreOutcome:
        """Full scoring flow for one lead, persisted and returned."""
        payload = await self._load_active_payload(tenant_id)
        if payload is None:
            raise SignalSetNotConfigured(tenant_id)

        signal_set_version = int(payload.get("version", 1))
        weights = self._adapter.to_weights(payload)

        # 1) deterministic pass
        result = self._engine.score(features, weights)
        used_llm = False
        llm_prompt_version: str | None = None
        llm_model_version: str | None = None

        # 2) low-confidence -> Sonnet fallback estimates skipped signals, rescore
        if (
            self._rating_client is not None
            and _confidence_value(result) < self._confidence_floor
        ):
            enriched_features = await self._rating_client.estimate_skipped(
                features, weights
            )
            result = self._engine.score(enriched_features, weights)
            used_llm = True
            llm_prompt_version = self._rating_client.prompt_version
            llm_model_version = self._rating_client.model_version

        # 3) the agent-owned fallback path (composition root) runs the LLM
        # *inside* engine.score and surfaces it on the result. Treat that as an
        # LLM score too, so provenance (ST4/ST5) is captured either way.
        if not used_llm and _result_llm_adjusted(result):
            used_llm = True

        # 4) assemble provenance (COMP-1201) — single wiring point
        provenance = self._build_provenance(
            payload=payload,
            signal_set_version=signal_set_version,
            used_llm=used_llm,
            prompt_version=llm_prompt_version,
            model_version=llm_model_version,
            result=result,
        )

        # 4b) capture confidence as a traceable record (COMP-1203-ST4) and store
        # it alongside provenance under its own key in the breakdown JSONB.
        provenance["confidence_capture"] = self._build_confidence_capture(
            result=result, used_llm=used_llm
        )

        # 5) persist within one transaction (service owns the boundary)
        async with self._session_factory() as session:
            repo = ScoringRepository(session)
            record = await repo.save_scoring_result(
                lead_id=_lead_id(features),
                tenant_id=tenant_id,
                signal_set_version=signal_set_version,
                score=_score_value(result),
                classification=_classification(result),
                breakdown=_breakdown(result),
                llm_adjusted=used_llm,
                provenance=provenance,
            )
            await session.commit()

        # 6) emit the AI-activity audit event (COMP-801-ST4). Outside the DB
        # transaction: an audit-sink failure must not roll back a valid score,
        # and the event references the persisted record_id.
        await self._emit_audit_event(
            tenant_id=tenant_id,
            lead_id=_lead_id(features),
            record_id=record.id,
            result=result,
            used_llm=used_llm,
            signal_set_version=signal_set_version,
            provenance=provenance,
        )

        return ScoreOutcome(
            result=result, record_id=record.id, from_llm_fallback=used_llm
        )

    async def invalidate_signal_set(self, tenant_id: str) -> None:
        """Drop a tenant's cached config after onboarding regenerates it."""
        await self._cache.invalidate(tenant_id)

    async def delete_lead_scoring_data(
        self, *, tenant_id: str, lead_id: str
    ) -> int:
        """COMP-303-ST5 — erase all scoring data for a lead (erasure request).

        Deletes every scoring result (and its embedded provenance) for the lead.
        This is the scoring-owned operation the erasure orchestration calls. The
        SignalSet cache is tenant-scoped, not lead-scoped, so it is untouched —
        no lead personal data lives in it. Returns the number of rows deleted.
        """
        async with self._session_factory() as session:
            repo = ScoringRepository(session)
            deleted = await repo.delete_results_for_lead(
                tenant_id=tenant_id, lead_id=lead_id
            )
            await session.commit()
        return deleted

    async def _emit_audit_event(
        self,
        *,
        tenant_id: str,
        lead_id: str,
        record_id: int,
        result: Any,
        used_llm: bool,
        signal_set_version: int,
        provenance: dict[str, Any],
    ) -> None:
        from services.audit import build_scoring_audit_event

        event = build_scoring_audit_event(
            tenant_id=tenant_id,
            lead_id=lead_id,
            record_id=record_id,
            score=_score_value(result),
            classification=_classification(result),
            used_llm=used_llm,
            model_version=provenance.get("model_version"),
            prompt_version=provenance.get("prompt_version"),
            signal_set_version=signal_set_version,
        )
        try:
            await self._audit_sink.emit(event)
        except Exception:  # never let auditing break scoring
            import logging

            logging.getLogger("audit.ai.scoring").exception(
                "Failed to emit scoring audit event for lead %s", lead_id
            )

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #
    async def _load_active_payload(self, tenant_id: str) -> dict[str, Any] | None:
        async def loader() -> dict[str, Any] | None:
            async with self._session_factory() as session:
                rec = await ScoringRepository(session).get_active_signal_set(
                    tenant_id=tenant_id
                )
                return rec.payload if rec is not None else None

        return await self._cache.get_or_load(tenant_id, loader)

    def _build_provenance(
        self,
        *,
        payload: dict[str, Any],
        signal_set_version: int,
        used_llm: bool,
        prompt_version: str | None,
        model_version: str | None,
        result: Any,
    ) -> dict[str, Any]:
        """COMP-1201: capture what produced this score, as a typed record.

        Folded into breakdown JSONB by the repository — no schema change. When
        the LLM fallback ran, the prompt/model version are required; they come
        either from the service's own rating_client (generic path) or off the
        scoring result itself (agent-owned fallback path) via _result_llm_meta.
        """
        from schemas.provenance import ScorerType, ScoringProvenance

        # the agent-owned fallback path surfaces versions on the result, not via
        # the service's rating_client — read them so ST4/ST5 are covered either way
        if used_llm and (prompt_version is None or model_version is None):
            r_prompt, r_model = _result_llm_meta(result)
            prompt_version = prompt_version or r_prompt
            model_version = model_version or r_model

        provenance = ScoringProvenance(
            scorer=ScorerType.LLM_FALLBACK if used_llm else ScorerType.DETERMINISTIC,
            signal_set_version=signal_set_version,
            signal_set_source=payload.get("source_pipeline"),
            signal_names=_signal_names(payload),
            confidence=_confidence_value(result),
            prompt_version=prompt_version if used_llm else None,
            model_version=model_version if used_llm else None,
        )
        return provenance.to_storage()

    def _build_confidence_capture(
        self, *, result: Any, used_llm: bool
    ) -> dict[str, Any]:
        """COMP-1203-ST4: capture the score's confidence as a traceable record.

        Reads the engine's derived confidence plus, when available, the signal
        counts behind it (so the value is recomputable). The engine is expected
        to surface total/skipped signal counts on the result; when it doesn't,
        the capture still records value + level + method, just without the
        recompute inputs.
        """
        from schemas.confidence import ConfidenceCapture, ConfidenceMethod

        value = _confidence_value(result)
        classification = _classification(result).lower()

        if classification == "blocked":
            method = ConfidenceMethod.SHORT_CIRCUIT
        else:
            method = ConfidenceMethod.SKIPPED_SIGNAL_RATIO

        capture = ConfidenceCapture.from_value(
            value,
            method=method,
            total_signals=_get(result, "total_signals"),
            skipped_signals=_get(result, "skipped_signals"),
            triggered_fallback=used_llm,
        )
        return capture.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Adapter helpers: tolerate either a Pydantic ScoringResult or a plain dict,   #
# so the service works in tests with simple fakes and in prod with real models #
# --------------------------------------------------------------------------- #
def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _confidence_value(result: Any) -> float:
    c = _get(result, "confidence")
    if isinstance(c, (int, float)):
        return float(c)
    # confidence may be a label ("high"/"medium"/"low"); map to a float
    return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(str(c).lower(), 1.0)


def _score_value(result: Any) -> float:
    return float(_get(result, "score", 0.0))


def _classification(result: Any) -> str:
    return str(_get(result, "classification", "cold"))


def _breakdown(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, dict):
        return dict(result)
    return {"value": str(result)}


def _lead_id(features: Any) -> str:
    return str(_get(features, "lead_id", "unknown"))


def _signal_names(payload: dict[str, Any]) -> list[str]:
    signals = payload.get("signals")
    if isinstance(signals, list):
        # signals may be names or dicts with a "name" key
        return [s["name"] if isinstance(s, dict) and "name" in s else s for s in signals]
    return []


def _result_llm_adjusted(result: Any) -> bool:
    """True when the scorer (agent) already applied an LLM fallback internally."""
    return bool(_get(result, "llm_adjusted", False))


def _result_llm_meta(result: Any) -> tuple[str | None, str | None]:
    """Pull prompt/model version off the result when the agent owns the fallback.

    Looks for explicit attributes first, then a nested provenance/meta dict, so
    it works whether the agent exposes them directly or tucks them into notes.
    """
    prompt = _get(result, "prompt_version")
    model = _get(result, "model_version")
    if prompt or model:
        return prompt, model
    meta = _get(result, "llm_meta") or {}
    if isinstance(meta, dict):
        return meta.get("prompt_version"), meta.get("model_version")
    return None, None
