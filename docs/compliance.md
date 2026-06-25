# Domain 3 Data Lifecycle Compliance — Lead Ingestion Implementation Plan

## Context

Three GDPR/DPDPA compliance stories from Domain3_Data_Lifecycle_Management_JIRA.xlsx are mapped
to Epic 4 (lead ingestion module). All three concern the lifecycle of personal data that our module
owns: collection boundaries (COMP-301), how long we keep it (COMP-302), and deleting it on request
(COMP-303). The 17 sub-tasks in scope are divided across 3 sprints; each sprint must reach its gate
(unit + integration + typecheck + lint fully green) before the next sprint begins.

Out of scope here: enrichment minimization rules (ST6 of COMP-301 → Epic 5 contract only),
scoring/reporting deletion (COMP-303-ST5/ST6 → Epic 6/7), and backup erasure policy (COMP-303-ST7
→ policy document, not code).

---

## Sprint A — w

### Goal
Enforce GDPR Art.5(1)(c) at the intake boundary: classify every field we collect, detect
sensitive PII (Aadhaar/PAN/credit card) arriving via CSV/extra_fields, and strip it before
the Lead row is written.

### New files

```
modules/lead_ingestion/data_policy.py
```

This single file owns all of COMP-301's in-module obligations:

**ST1 + ST2 — Classification model + required attribute list**
```python
class FieldSensitivity(StrEnum):
    PII_DIRECT   = "PII_DIRECT"    # full_name, phone, email
    PII_INDIRECT = "PII_INDIRECT"  # location
    PII_POSSIBLE = "PII_POSSIBLE"  # raw_event_json, extra_fields
    OPERATIONAL  = "OPERATIONAL"   # pipeline_stage, source_channel, id, created_at

LEAD_FIELD_CLASSIFICATIONS: dict[str, FieldSensitivity] = {
    "full_name": FieldSensitivity.PII_DIRECT,
    "phone":     FieldSensitivity.PII_DIRECT,
    "email":     FieldSensitivity.PII_DIRECT,
    "location":  FieldSensitivity.PII_INDIRECT,
    "raw_event_json": FieldSensitivity.PII_POSSIBLE,
    "extra_fields":   FieldSensitivity.PII_POSSIBLE,
    "pipeline_stage": FieldSensitivity.OPERATIONAL,
    "source_channel": FieldSensitivity.OPERATIONAL,
}

# ST4 — Collection justification per field
COLLECTION_JUSTIFICATION: dict[str, str] = {
    "full_name": "Lead identity matching and tenant CRM display",
    "phone":     "Primary dedup key (E.164); tenant outreach channel",
    "email":     "Secondary dedup key; tenant outreach channel",
    "location":  "Geographic ICP scoring signal",
    "raw_event_json": "Source-of-truth audit trail; required for idempotent redelivery",
    "extra_fields":   "Tenant-defined fields; stored after sensitive field stripping",
}
```

**ST3 + ST5 — Sensitive data detection + minimization validation**
```python
# Regex patterns — Indian context
_AADHAAR = re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b')
_PAN     = re.compile(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b')
_CC      = re.compile(r'\b(?:\d[ -]?){13,19}\b')      # rough; Luhn validated
_IFSC    = re.compile(r'\b[A-Z]{4}0[A-Z0-9]{6}\b')
_PASSPORT = re.compile(r'\b[A-Z][1-9][0-9]{7}\b')

def detect_sensitive_fields(data: dict[str, Any]) -> dict[str, str]:
    """Return {field_name: detection_type} for any extra_field values that
    contain regulated identifiers. Values are NOT logged."""
    ...

def strip_sensitive_fields(
    extra_fields: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Remove sensitive keys from extra_fields in place.
    Returns (clean_dict, {removed_key: detection_type}).
    The removed values are NEVER stored or logged — only the key names."""
    ...
```

### Modified files

**`modules/lead_ingestion/normaliser.py`**
- In `normalise_file_row()`: after building `extra_fields`, call
  `strip_sensitive_fields(extra_fields)`. Log flagged key names (not values)
  via structlog. Pass clean dict to NormalisedChannelEvent.

**`modules/lead_ingestion/pipeline.py`**
- No change to pipeline flow. Stripping happens at normalisation time (before the
  event reaches pipeline). This is intentional — strip at the boundary, not inside
  the DB-write path.

**`modules/lead_ingestion/service.py`**
- Export: `strip_sensitive_fields`, `detect_sensitive_fields`, `LEAD_FIELD_CLASSIFICATIONS`

### Tests

`tests/unit/lead_ingestion/test_data_policy.py`
- Aadhaar/PAN/CC/IFSC/Passport in values → detected and stripped
- Clean values → nothing stripped, dict unchanged
- Multiple sensitive fields in one row → all stripped
- Empty dict → no-op

`tests/integration/lead_ingestion/test_minimization.py`
- CSV with columns `pan_number`, `budget`, `name` → `pan_number` absent from
  `Lead.extra_fields`; `budget` present; lead created successfully
- Verify structured log warning emitted (capsys)

### Sprint A Gate
```bash
make test-unit        # includes test_data_policy.py
make test-integration # includes test_minimization.py
make typecheck
make lint
```

---

## Sprint B — Retention Management (COMP-302: ST1, ST2, ST3, ST4, ST7, ST8)

### Goal
Enforce GDPR Art.5(1)(e): leads older than the configured retention window are
anonymised in place (PII nulled, JSONB redacted). An ARQ daily cron job runs the sweep.
Every anonymisation is audit-logged in a new `retention_event_logs` table.

### New files

**`modules/lead_ingestion/retention.py`**

ST1 — Retention schedule read from Settings (see below).
ST2 — Policy engine:
```python
async def get_leads_due_for_anonymisation(
    session: AsyncSession, *, retention_days: int
) -> Sequence[Lead]:
    """Return all captured leads whose created_at < now() - retention_days.
    Excludes already-anonymised (pipeline_stage='anonymised') and erased leads."""

async def get_event_logs_due_for_anonymisation(
    session: AsyncSession, *, retention_days: int
) -> Sequence[IntakeEventLog]:
    """Separate retention window for event logs (shorter: 180 days default)."""
```

ST3 — Anonymise workflow (in place, not separate table — preserves FK integrity):
```python
async def anonymise_lead(session: AsyncSession, lead: Lead) -> None:
    """Null out PII columns; replace JSONB with tombstone marker; set stage.
    Writes a RetentionEventLog row."""
    lead.full_name = None
    lead.phone     = None
    lead.email     = None
    lead.location  = None
    lead.raw_event_json = {"anonymised": True, "anonymised_at": now_utc().isoformat()}
    lead.extra_fields   = None
    lead.pipeline_stage = "anonymised"
    # Also anonymise linked touchpoints
    ...
    session.add(RetentionEventLog(lead_id=lead.id, ...))
```

ST4 — Entry point called by cron job:
```python
async def run_retention_sweep(session: AsyncSession) -> dict[str, int]:
    """Anonymise all overdue leads. Returns count summary."""
```

**`workers/jobs/lead_retention.py`** — ARQ cron wrapper (same pattern as `instagram_refresh.py`):
```python
async def run_lead_retention_sweep(ctx: dict[str, object]) -> None:
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        await run_retention_sweep(session)
```

### New model (ST7 — Retention Audit Logs)

Add `RetentionEventLog` to **`modules/lead_ingestion/db/models.py`**:
```python
class RetentionEventLog(Base):
    __tablename__ = "retention_event_logs"
    id:           Mapped[uuid.UUID]  # PK
    lead_id:      Mapped[uuid.UUID]  # FK → leads (nullable=True for log-only records)
    tenant_id:    Mapped[uuid.UUID]  # FK → tenants
    action:       Mapped[str]        # 'anonymised'
    performed_at: Mapped[datetime]   # server_default=func.now()
    retention_days_applied: Mapped[int]
```

### Modified files

**`core/config.py`** — ST1 (retention schedule):
```python
lead_data_retention_days:      int = 730   # leads + touchpoints
intake_log_retention_days:     int = 180   # intake_event_logs
```

**`workers/worker.py`**:
```python
from workers.jobs.lead_retention import run_lead_retention_sweep
cron_jobs = [
    cron(run_instagram_token_refresh, hour=2, minute=0),
    cron(run_lead_retention_sweep,   hour=3, minute=0),   # 03:00 UTC daily
]
```

**`tests/integration/conftest.py`** — import `RetentionEventLog` so truncation covers it.

### New migration
```bash
alembic revision --autogenerate -m "add_retention_event_logs"
```

### Tests

`tests/unit/lead_ingestion/test_retention.py`
- `get_leads_due_for_anonymisation()`: lead 731 days old → returned; 729 days old → not returned
- `anonymise_lead()`: PII columns null; stage = 'anonymised'; touchpoint raw_event_json redacted
- Already-anonymised lead → not returned by policy engine (no double-processing)

`tests/integration/lead_ingestion/test_retention_sweep.py`
- Seed: 2 leads at `created_at = now() - 731 days`, 1 at `now() - 100 days`
- Run `run_retention_sweep(session)`
- Assert: 2 leads have `pipeline_stage='anonymised'`, PII nulled
- Assert: 2 `RetentionEventLog` rows written
- Assert: 1 recent lead untouched

### Sprint B Gate
```bash
make test-unit        # includes test_retention.py
make test-integration # includes test_retention_sweep.py
make typecheck
make lint
```

---

## Sprint C — Right to Erasure (COMP-303: ST1, ST2, ST3, ST4, ST8)

### Goal
Implement GDPR Art.17 within the lead ingestion module: a tenant admin can submit an
erasure request by contact identity (phone or email). All matching lead data is
anonymised immediately (stronger than retention anonymisation — no pipeline_stage bypass,
includes in-flight ARQ resilience via existing idempotency guard). A `LeadErasureRequested`
event is emitted for downstream epics to cascade their cleanup.

### New files

**`modules/lead_ingestion/erasure.py`** — ST2 + ST3:
```python
async def erase_lead_by_identity(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    phone: str | None = None,
    email: str | None = None,
) -> tuple[list[UUID], LeadErasureRequested | None]:
    """Find and erase all leads matching the given identity within the tenant.

    Uses the same match order as deduplicator (phone first, then email).
    Anonymises: Lead PII columns, raw_event_json, extra_fields, LeadTouchpoint
    raw_event_json, IntakeEventLog raw_event_json (preserves structure for audit).
    Writes ErasureEventLog row. Returns (erased_lead_ids, LeadErasureRequested | None).

    ARQ in-flight safety: existing idempotency guard (ON CONFLICT DO NOTHING on
    platform_event_id) means any queued job for an erased lead's event will short-circuit
    and return the already-erased lead. No explicit job cancellation needed.
    """
```

### New model (ST1 — Erasure audit trail)

Add `ErasureEventLog` to **`modules/lead_ingestion/db/models.py`**:
```python
class ErasureEventLog(Base):
    __tablename__ = "erasure_event_logs"
    id:                Mapped[uuid.UUID]   # PK
    tenant_id:         Mapped[uuid.UUID]   # FK → tenants
    erased_lead_ids:   Mapped[list[str]]   # JSONB — list of UUID strings
    identifier_type:   Mapped[str]         # 'phone' | 'email'
    identifier_hash:   Mapped[str]         # SHA-256 of identifier — NOT the value itself
    performed_at:      Mapped[datetime]    # server_default=func.now()
    requested_by:      Mapped[str]         # auth0_sub of requesting user
```

### Modified files

**`shared/events/schemas.py`** — ST4 (new event for downstream cascade):
```python
class LeadErasureRequested(Event):
    """lead_ingestion completed erasure; downstream modules must clean derived data."""
    event_type: Literal["LeadErasureRequested"] = "LeadErasureRequested"
    erased_lead_ids: list[UUID]
    identifier_type: str          # 'phone' | 'email'
    identifier_hash: str          # SHA-256(identifier) — no PII in event payload
```

**`api/lead_ingestion.py`** — ST1 (Erasure Request endpoint):
```python
@router.post("/erasure-request", status_code=200)
async def erasure_request(
    body: ErasureRequestBody,       # {identifier_type, identifier}
    current_user: User = Depends(get_current_user),   # tenant role required
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Accept a GDPR erasure request. Erases synchronously, returns lead IDs."""
    ...
    erased_ids, event = await erase_lead_by_identity(
        session, current_user.tenant_id,
        phone=..., email=...,
    )
    return {"erased_lead_count": len(erased_ids), "event": event.model_dump() if event else None}
```

**`modules/lead_ingestion/service.py`**:
- Export: `erase_lead_by_identity`

**`tests/integration/conftest.py`** — import `ErasureEventLog` for truncation.

### New migration
```bash
alembic revision --autogenerate -m "add_erasure_event_logs"
```

### Tests

`tests/unit/lead_ingestion/test_erasure.py`
- `erase_lead_by_identity()` with mocked session:
  - Found lead → PII nulled, stage = 'erased', ErasureEventLog written
  - Not found → returns ([], None)
  - identifier_hash is SHA-256 of value, not the value itself

`tests/integration/lead_ingestion/test_erasure_golden_path.py`
- Seed a lead with phone + email
- POST `/channels/erasure-request` with phone identifier
- Assert: `Lead.phone = None`, `Lead.email = None`, `Lead.full_name = None`
- Assert: `Lead.raw_event_json = {"erased": True, ...}`
- Assert: `LeadTouchpoint.raw_event_json` also redacted
- Assert: `ErasureEventLog` row exists with correct `identifier_hash`
- Assert: `LeadErasureRequested` event returned in response body

`tests/integration/lead_ingestion/test_erasure_idempotency.py`
- Erase a lead → then re-deliver the original webhook event
- Assert: pipeline returns erased lead, does NOT create a new lead, does NOT overwrite
  the erased data (idempotency guard fires before any write)

### Sprint C Gate
```bash
make ci   # full suite — lint + typecheck + 302+ tests
grep -r "import anthropic" modules/ | wc -l  # must be 0
pytest tests/unit/lead_ingestion/ tests/integration/lead_ingestion/ -v
```

---

## Cross-Sprint Constraints

| Rule | Applies |
|---|---|
| Architecture: modules/ → shared/ only | `LeadErasureRequested` added to `shared/events/schemas.py` (correct layer) |
| No `import anthropic` | N/A — no LLM used in any of these sprints |
| HMAC/constant-time | No new comparisons — no change needed |
| Never store sensitive values in logs | `strip_sensitive_fields` logs key names only, never values; `ErasureEventLog` stores SHA-256 hash only |
| Calibration rule | Erasure bypasses pipeline entirely; minimization stripping does not block leads |
| TDD — tests first | Each sprint: write failing test → watch it fail → implement → green |

## File change summary

| Sprint | New files | Modified files | New migration |
|---|---|---|---|
| A | `data_policy.py` | `normaliser.py`, `service.py` | None |
| B | `retention.py`, `jobs/lead_retention.py` | `db/models.py` (+RetentionEventLog), `core/config.py`, `workers/worker.py`, `service.py`, integration `conftest.py` | `add_retention_event_logs` |
| C | `erasure.py` | `db/models.py` (+ErasureEventLog), `shared/events/schemas.py`, `api/lead_ingestion.py`, `service.py`, integration `conftest.py` | `add_erasure_event_logs` |
