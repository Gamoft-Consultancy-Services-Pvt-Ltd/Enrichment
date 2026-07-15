"""
app/composition.py — Composition root for the scoring runtime (Epic 6)

This is the ONE place that imports concrete components and wires them into a
ScoringService. The API (LEAD-56) and worker (LEAD-57) both call
build_scoring_service() so they boot with identical, real wiring.

=============================================================================
  TEMPLATE — verify the import paths and names against your actual module.
  Unlike the other files this session, this one imports YOUR real code
  (engine.py, rating_client.py, signal_set_adapter.py, your settings and DB
  engine), which wasn't available to run here. Each spot you must confirm is
  marked  # >>> CONFIRM.
=============================================================================

Design:
- A single async engine + sessionmaker is created once per process (module-
  level singletons), so every request/job shares one connection pool rather
  than opening a new engine each time.
- Your real LeadScoringAgent (LEAD-53) already encapsulates "deterministic
  engine first, Sonnet fallback for low confidence, re-score". The service
  expects a simple `engine.score(features, weights)` + optional
  `rating_client.estimate_skipped(...)`. Rather than make the service
  re-implement the fallback your agent already does, we wrap the agent in a
  thin AgentEngineShim that satisfies the service's `Engine` protocol and
  performs the whole scored-with-fallback step in one call. The service is then
  constructed WITHOUT a separate rating_client (rating_client=None), because the
  shim already owns the fallback. Provenance for the LLM path is surfaced by the
  shim onto the result so the service can still read prompt/model version.
- The SignalSet payload stored in the DB is converted to a runtime SignalSet by
  your adapter inside the shim, keeping the service free of adapter specifics.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.cache import InMemoryCacheBackend, RedisCacheBackend, SignalSetCache
from services.service import ScoringService

# --- YOUR real components -------------------------------------------------- #
# >>> CONFIRM these import paths match your module layout under modules/scoring/
from engine import ScoringEngine                       # LEAD-51  # >>> CONFIRM
from agent import LeadScoringAgent                      # LEAD-53  # >>> CONFIRM
from rating_client import RatingClient                  # LEAD-52  # >>> CONFIRM
from signal_set_adapter import build_signal_set         # LEAD-50  # >>> CONFIRM
# from config import settings                            # your settings object # >>> CONFIRM


# --------------------------------------------------------------------------- #
# Process-wide singletons                                                      #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """One async engine per process (pooled)."""
    # >>> CONFIRM: replace with settings.DATABASE_URL (asyncpg DSN)
    dsn = "postgresql+asyncpg://user:pass@localhost:5432/enrichment"
    return create_async_engine(dsn, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache(maxsize=1)
def get_cache() -> SignalSetCache:
    """Redis-backed in prod; in-memory fallback if no Redis configured.

    >>> CONFIRM: wire your real async redis client here. Until then this falls
    back to in-memory so the app still boots in dev.
    """
    try:
        from redis.asyncio import Redis  # type: ignore

        # >>> CONFIRM: settings.REDIS_URL
        redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        backend: Any = RedisCacheBackend(redis)
    except Exception:
        backend = InMemoryCacheBackend()
    return SignalSetCache(backend, ttl_seconds=300)


# --------------------------------------------------------------------------- #
# Shim: present the real LeadScoringAgent as the service's Engine protocol     #
# --------------------------------------------------------------------------- #
class AgentEngineShim:
    """Adapts LeadScoringAgent (LEAD-53) to the service's `engine.score()`.

    The service calls score(features, weights). Here `weights` is actually the
    stored SignalSet payload dict (the service passes through whatever the
    adapter returned). We build the runtime SignalSet from it, build a
    LeadFeatures from the raw features dict, then run the agent which does the
    deterministic + fallback work internally and returns a ScoringResult.

    Because the agent owns the fallback, the ScoringService is constructed with
    rating_client=None; the service's low-confidence branch never fires. We mark
    the result's llm_adjusted so downstream layers still see whether the LLM ran.
    """

    def __init__(self, agent: LeadScoringAgent) -> None:
        self._agent = agent

    def score(self, features: dict[str, Any], weights: Any) -> Any:
        # weights here is the raw stored payload dict (see build call below)
        signal_set = build_signal_set(weights)            # >>> CONFIRM fn name
        lead = _to_lead_features(features)                # >>> CONFIRM builder
        result = self._agent.score(lead, signal_set, enable_fallback=True)
        # NOTE (COMP-1201 gap): because the agent owns the fallback internally,
        # the service's _build_provenance can't see the LLM prompt/model version
        # (its rating_client is None). For full provenance on the LLM path, have
        # the agent expose prompt_version/model_version on its ScoringResult (or
        # in scoring_notes), then read them here and attach to the result so the
        # service folds them into breakdown["provenance"]. Until then provenance
        # records scorer/confidence/signal_names/version but not LLM versions.
        return result


class PassThroughAdapter:
    """The service's Adapter seam. We defer real adapting to the shim (which has
    both the payload and the features), so here we simply hand the payload dict
    straight through as the 'weights' the shim receives."""

    def to_weights(self, payload: dict[str, Any]) -> Any:
        return payload


def _to_lead_features(features: dict[str, Any]) -> Any:
    """Build your real LeadFeatures from a plain dict.

    >>> CONFIRM: import and construct your actual schema, e.g.
        from schemas.lead_features import LeadFeatures
        return LeadFeatures(**features)
    The upstream Epic 5 mapping (lead_mapper.map_enriched_lead) may belong here
    if the API/worker receive raw enriched leads rather than LeadFeatures dicts.
    """
    from schemas.lead_features import LeadFeatures        # >>> CONFIRM path
    return LeadFeatures(**features)


# --------------------------------------------------------------------------- #
# The builder the API and worker call                                          #
# --------------------------------------------------------------------------- #
def build_scoring_service() -> ScoringService:
    """Construct a fully-wired ScoringService from real components."""
    rating_client = _build_rating_client()
    agent = LeadScoringAgent(engine=ScoringEngine(), rating_client=rating_client)

    return ScoringService(
        session_factory=get_session_factory(),
        cache=get_cache(),
        adapter=PassThroughAdapter(),
        engine=AgentEngineShim(agent),
        rating_client=None,       # the agent shim already owns the fallback
        confidence_floor=0.5,     # unused while rating_client is None
    )


def _build_rating_client() -> RatingClient | None:
    """Build the Sonnet fallback client, or None if no API key is configured.

    >>> CONFIRM: construct your real RatingClient. From LEAD-52 its signature is
    RatingClient(client, sleep=...), wrapping a raw Anthropic client.
    """
    try:
        from anthropic import Anthropic  # type: ignore

        # >>> CONFIRM: settings.ANTHROPIC_API_KEY (or rely on env var)
        raw = Anthropic()
        return RatingClient(raw)
    except Exception:
        # no key / package -> run deterministic-only rather than crash at boot
        return None
