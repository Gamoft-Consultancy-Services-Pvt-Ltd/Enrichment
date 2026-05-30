# ADR 0001 — Held-lead release on tenant activation is event-based

- **Status:** Accepted
- **Date:** 2026-05-30

## Context

While a tenant is in `onboarding` status, scoring does not run for its leads —
incoming leads are held. When the tenant becomes `active`, the held leads must be
drained and scored in arrival order.

The status gate is exposed by `shared/tenant`, but the hold/drain mechanism
belongs to the `orchestration` module. We needed to decide how `orchestration`
learns that a tenant just became active: by `tenant_onboarding` calling it
directly, or by reacting to an event.

## Decision

Use an **event-based** approach. When `tenant_onboarding` completes the
transition `onboarding -> active`, it emits a `TenantActivated` event via
`shared/events`. The `orchestration` module subscribes to that event and drains
the tenant's held leads in arrival order.

`tenant_onboarding` never calls `orchestration` directly.

## Consequences

- Modules stay decoupled, consistent with the boundary rule "modules communicate
  by emitting/consuming events and calling public services, never internals."
- The activation flow is asynchronous: draining begins when the event is
  consumed, not synchronously inside the status transition.
- Adding future reactions to activation (e.g. notifications, analytics) means
  subscribing to the same event, with no change to `tenant_onboarding`.
