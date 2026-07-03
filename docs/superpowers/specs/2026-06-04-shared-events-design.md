# shared/events — design

**Date:** 2026-06-04
**Status:** Approved (design); implementation pending
**Module:** `shared/events` (cross-cutting domain in `shared/`)

## Purpose

`shared/events` defines the **typed vocabulary of facts** that modules use to
communicate across boundaries. An *event* is a record that something meaningful
happened in one module, published so other modules can react without the
originating module knowing or caring who listens. This is what lets the
dependency rule hold — "no module imports another module; they communicate only
via `shared/events` and public services" — so the event types live in `shared/`,
consulted by many modules.

Every event in this system is **tenant-scoped** and immutable: a published fact
does not change.

### Scope for this iteration — schemas only

We are building the event **contracts only**. This module defines the `Event`
envelope, the concrete event types, and the supporting enums — and **nothing
else**. There is deliberately:

- **no publish function**,
- **no subscribe / handler registry**,
- **no bus or delivery mechanism** (in-process or cross-process).

Those land later, together with the first real consumer (`modules/orchestration`,
which drains held leads on `TenantActivated` — see ADR 0001). Defining the
message *shapes* now is cheap, fully unit-testable, and pins down *what* flows
before we decide *how* it is delivered (an in-process call vs. a Redis/ARQ
queue). This follows the project's recurring pattern: prefer the simpler option
now, defer the heavier delivery machinery until a consumer needs it (YAGNI).

Because there is no delivery yet, this module touches no DB, no Redis, and no
network. It is pure pydantic and is covered entirely by unit tests.

## What is *not* an event (and why)

To keep the catalog honest, we explicitly exclude:

- **Authorization** ("can this request do this?") — inherently synchronous; it
  must return allow/deny inline on the request path. It is a FastAPI
  dependency/guard, never a message.
- **Login succeeded / failed** — legitimate *future* event facts, but no
  consumer exists yet (`auth/` and `shared/audit` are not built). Deferred until
  one does.
- **Prompt version change** — handled by a Postgres `NOTIFY` channel for
  per-process cache flushing (ADR 0002), deliberately *not* the event bus.
- **The onboarding agent chain** (Business Profile → Persona → ICP → Signal →
  prompt) — all internal to `tenant_onboarding`; no boundary crossed.
- **"Notification sent"** — sending a notification is an *action* the
  notification module takes in *reaction* to `LeadScored`, not an event others
  consume.
- **`TenantSuspended` / `TenantChurned`** — those status transitions are not yet
  implemented (YAGNI-deferred), so no events for them now.

The guiding test: **an event with no consumer should not exist**, and an event
exists only where one module hands off to a *different* module.

## File layout

Matches `shared/tenant`: a single schema file, an empty `__init__.py`, and
consumers import the public surface directly.

```
shared/events/
  __init__.py        # empty (consistent with shared/tenant)
  schemas.py         # the Event base, the two enums, the four concrete events
```

Consumers import directly, e.g.:

```python
from shared.events.schemas import TenantActivated, LeadScored, LeadBucket
```

## Public surface (the boundary)

`schemas.py` is the entire public surface — the single source of truth for the
event envelope, the event types, and the `LeadSource` / `LeadBucket` enums.
Scoring and lead_ingestion import those enums *from here* (they cannot live in
`modules/`, since `shared/` must not import from `modules/`).

## The envelope — `Event` base

A frozen pydantic `BaseModel` that every event inherits. It carries the four
fields shared by every event:

| Field         | Type       | Source                                                                 |
|---------------|------------|------------------------------------------------------------------------|
| `event_id`    | `UUID`     | auto — `default_factory=uuid4`                                         |
| `event_type`  | `str`      | per-subclass `Literal` default (e.g. `Literal["TenantActivated"]`) — a stable string for logging/routing, rename-safe |
| `occurred_at` | `datetime` | auto — `default_factory=lambda: datetime.now(UTC)`, tz-aware UTC      |
| `tenant_id`   | `UUID`     | caller-supplied — every event is tenant-scoped                        |

- `model_config = ConfigDict(frozen=True)` — events are immutable once created.
- `Event` is a base only; concrete events subclass it. It is not instantiated
  directly.

## The enums

Both are `StrEnum`, matching the precedent set by `TenantStatus` /
`BusinessType` in `shared/tenant/schemas.py`.

- **`LeadSource`** — the four ingestion sources:
  `GOOGLE_SHEETS`, `EMAIL`, `WHATSAPP`, `INSTAGRAM`.
- **`LeadBucket`** — the scoring outcome: `HOT`, `WARM`, `COLD`.

## The four events

Payload style is **thin + routing keys**: carry the entity id, plus the one or
two fields a consumer needs to *route or filter* without a database read. For
full details, a consumer calls the source module's public service. Envelope
fields (`event_id`, `event_type`, `occurred_at`, `tenant_id`) are implied on all
four.

| Event             | Extra payload beyond envelope                          | Rationale                                                                 |
|-------------------|--------------------------------------------------------|---------------------------------------------------------------------------|
| `TenantActivated` | *(none)*                                               | orchestration drains held leads by `tenant_id`, already in the envelope   |
| `LeadReceived`    | `lead_id: UUID`, `source: LeadSource`                  | id to fetch details; `source` as a routing / reporting key                |
| `LeadEnriched`    | `lead_id: UUID`                                        | signals enrichment is done; the consumer loads enriched data from the DB  |
| `LeadScored`      | `lead_id: UUID`, `score: float` (0–100), `bucket: LeadBucket` | `bucket` lets notification spot HOT with no DB read; `score` is the final composite |

- `LeadScored.score` is a `float` constrained to the inclusive range `0–100`
  (the final composite score; default thresholds are HOT ≥ 80, WARM ≥ 55, else
  COLD).
- Each concrete event sets its own `event_type` `Literal` default matching its
  class name.

## Testing (TDD, all unit — no DB or network)

New file `tests/unit/test_event_schemas.py`, in the style of
`tests/unit/test_tenant_schemas.py`:

- **Enum membership** is exactly the expected set, for both `LeadSource` and
  `LeadBucket`.
- Each event **constructs** with its required fields; required ids
  (`tenant_id`, `lead_id`) missing → `ValidationError`.
- **Envelope auto-fields** populate: `event_id` is a `UUID`, `occurred_at` is
  tz-aware UTC, and both differ across two separately-constructed instances.
- Each event carries the **correct `event_type`** string.
- Events are **frozen**: mutating any field raises `ValidationError`.
- **`LeadScored.score`** rejects out-of-range values (e.g. `150`, `-1`); `bucket`
  and `source` reject invalid enum members.

## Consequences & follow-ups

- The event catalog is now the agreed cross-module vocabulary: when
  `modules/orchestration`, `tenant_onboarding`, `lead_ingestion`, `enrichment`,
  `scoring`, and `notification` are built, they target these stable shapes.
- **Open design question (out of scope here):** whether the
  `enrichment → scoring` hand-off uses the `LeadEnriched` event or a direct
  service call orchestrated by `modules/orchestration`. We define the
  `LeadEnriched` *shape* now without committing to its delivery.
- **Deferred:** the publish/subscribe seam and the delivery mechanism (the
  in-process vs. Redis/ARQ decision), to be designed with the first consumer.
- **Deferred:** auth/login events, tenant suspend/churn events, and any
  envelope extensions (`correlation_id`, `schema_version`) until a real need
  appears.

## Reference

- ADR 0001 — held-lead release on tenant activation is event-based
  (`TenantActivated`).
- ADR 0002 — prompt-version cache invalidation uses Postgres `NOTIFY`, not the
  event bus.
- `docs/architecture.md` — the dependency rule and module boundaries.
- `shared/tenant` — the precedent for `shared/` module structure and enums.
