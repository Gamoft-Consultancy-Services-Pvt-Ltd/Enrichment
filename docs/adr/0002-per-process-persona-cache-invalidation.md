# ADR 0002 — PersonaObject cache invalidation uses per-process NOTIFY listeners

- **Status:** Accepted
- **Date:** 2026-05-30

## Context

The PersonaObject is held in an in-process `cachetools` TTLCache (15-minute TTL,
key = `tenant_id + prompt_template_version`). When a prompt version changes, this
cache must be flushed immediately rather than waiting out the TTL.

The application runs as **two separate processes** — the FastAPI web app and the
ARQ worker — and each process has its own memory, so each holds its own copy of
the cache. The flush signal must reach every process.

## Decision

Each process **independently** subscribes to the Postgres `NOTIFY` channel that
fires on a prompt version change, and flushes its own in-process cache when the
signal arrives. There is no shared coordination layer.

The heavier alternative — a shared coordinator such as a Redis version flag that
every process consults — is **deferred** until we run many copies of the web app
or otherwise outgrow per-process listeners.

## Consequences

- Simple: no new infrastructure beyond the Postgres NOTIFY channel.
- Every process that caches PersonaObjects (web app, worker) must wire up its own
  listener. Forgetting one means that process silently serves stale personas.
- A reactivated prior prompt version takes effect within one cache TTL cycle in
  the worst case (if a NOTIFY is missed), which satisfies the rollback
  requirement.
- Revisit if horizontal scaling of the web app makes per-process listeners
  unwieldy (see deferred Redis-flag option).
