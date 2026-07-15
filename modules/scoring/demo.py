"""
Epic 6 demo — deterministic scoring of VentureDesk CRM (B2B) leads.

Demonstrates the full deterministic slice:
  Epic 3 outputs (Signal[], scoring_weights_final)
      └── signal_set_adapter (LEAD-50)  -> validated SignalSet (LEAD-44)
  Epic 5 enriched payload
      └── LeadFeatures (LEAD-45)
  ScoringEngine (LEAD-51) + SignalEvaluator (LEAD-49)
      └── ScoringResult with full reasoning trace (LEAD-46)

Run:  python demo.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from engine import ScoringEngine
from schemas.lead_features import LeadFeatures
from signal_set_adapter import build_signal_set

# ---------------------------------------------------------------------------
# Epic 3 outputs for VentureDesk (matches Scoring_Strategy.docx, section 4).
# Per-dimension signal weights sum to 1.0; the adapter maps them onto the
# scoring_weights_final budgets (Fit 30 / Intent 30 / Context 20 /
# Behaviour 15 / Engagement 5).
# ---------------------------------------------------------------------------

SCORING_WEIGHTS_FINAL = {
    "fit": 30, "intent": 30, "context": 20, "behaviour": 15, "engagement": 5,
}

THRESHOLDS = {"hot_min": 75, "warm_min": 40, "cold_max": 39}

EPIC3_SIGNALS = [
    # ---- Fit (budget 30) ---------------------------------------------------
    {"id": "fit_industry_icp", "dimension": "fit", "weight": 8 / 30,
     "observation": "Industry is in the ICP industry list",
     "condition": {"field": "company.industry", "operator": "in",
                   "value": ["saas", "fintech", "martech"]}},
    {"id": "fit_employee_band", "dimension": "fit", "weight": 8 / 30,
     "observation": "Employee count within 50-500 ICP band",
     "condition": {"field": "company.employee_count", "operator": "between",
                   "value": 50, "value2": 500}},
    {"id": "fit_tech_stack", "dimension": "fit", "weight": 6 / 30,
     "observation": "Uses a complementary tool in the tech stack",
     "condition": {"field": "company.tech_stack", "operator": "contains",
                   "value": "mailchimp"}},
    {"id": "fit_geography", "dimension": "fit", "weight": 4 / 30,
     "observation": "Headquartered in a target geography",
     "condition": {"field": "company.country", "operator": "in",
                   "value": ["IN", "US", "GB", "SG"]}},
    {"id": "fit_revenue_band", "dimension": "fit", "weight": 4 / 30,
     "observation": "Estimated revenue within ICP band",
     "condition": {"field": "company.revenue_usd_m", "operator": "between",
                   "value": 1, "value2": 50}},
    # ---- Intent (budget 30) ------------------------------------------------
    {"id": "intent_demo_request", "dimension": "intent", "weight": 10 / 30,
     "observation": "Submitted a demo request form",
     "condition": {"field": "events.demo_requested_at", "operator": "exists"}},
    {"id": "intent_pricing_visits", "dimension": "intent", "weight": 8 / 30,
     "observation": "Visited the pricing page 2+ times",
     "condition": {"field": "web.pricing_page_visits", "operator": "gte", "value": 2}},
    {"id": "intent_competitor_content", "dimension": "intent", "weight": 6 / 30,
     "observation": "Consumed competitor-comparison content",
     "condition": {"field": "web.competitor_pages_viewed", "operator": "gt", "value": 0}},
    {"id": "intent_trial_started", "dimension": "intent", "weight": 6 / 30,
     "observation": "Started a free trial",
     "condition": {"field": "events.trial_started_at", "operator": "exists"}},
    # ---- Context (budget 20) -----------------------------------------------
    {"id": "ctx_recent_funding", "dimension": "context", "weight": 7 / 20,
     "observation": "Raised funding in the last 12 months",
     "condition": {"field": "company.months_since_funding", "operator": "lte", "value": 12}},
    {"id": "ctx_hiring_sales", "dimension": "context", "weight": 6 / 20,
     "observation": "Actively hiring sales / RevOps roles",
     "condition": {"field": "company.open_sales_roles", "operator": "gte", "value": 1}},
    {"id": "ctx_leadership_change", "dimension": "context", "weight": 4 / 20,
     "observation": "New revenue leader in the last 6 months",
     "condition": {"field": "company.new_revenue_leader", "operator": "eq", "value": True}},
    {"id": "ctx_expansion_news", "dimension": "context", "weight": 3 / 20,
     "observation": "Announced expansion or new product line",
     "condition": {"field": "news.expansion_announced", "operator": "eq", "value": True}},
    # ---- Behaviour (budget 15) ----------------------------------------------
    {"id": "beh_email_reply", "dimension": "behaviour", "weight": 6 / 15,
     "observation": "Replied to an outreach email",
     "condition": {"field": "events.replied_to_outreach", "operator": "eq", "value": True}},
    {"id": "beh_webinar_attended", "dimension": "behaviour", "weight": 5 / 15,
     "observation": "Attended a webinar",
     "condition": {"field": "events.webinars_attended", "operator": "gte", "value": 1}},
    {"id": "beh_case_study", "dimension": "behaviour", "weight": 4 / 15,
     "observation": "Downloaded a case study",
     "condition": {"field": "events.case_studies_downloaded", "operator": "gte", "value": 1}},
    # ---- Engagement (budget 5) ----------------------------------------------
    {"id": "eng_site_sessions", "dimension": "engagement", "weight": 3 / 5,
     "observation": "3+ site sessions in 14 days",
     "condition": {"field": "web.sessions_14d", "operator": "gte", "value": 3}},
    {"id": "eng_email_opens", "dimension": "engagement", "weight": 2 / 5,
     "observation": "3+ marketing email opens",
     "condition": {"field": "marketing.email_opens", "operator": "gte", "value": 3}},
    # ---- Negative signals -----------------------------------------------------
    {"id": "neg_competitor", "negative": True, "hard_block": True, "points": -100,
     "observation": "Lead works at a direct competitor",
     "condition": {"field": "company.is_competitor", "operator": "eq", "value": True}},
    {"id": "neg_disposable_email", "negative": True, "hard_block": True, "points": -100,
     "observation": "Disposable email domain",
     "condition": {"field": "contact.email_disposable", "operator": "eq", "value": True}},
    {"id": "neg_unsubscribed", "negative": True, "points": -10,
     "observation": "Unsubscribed from all marketing channels",
     "condition": {"field": "marketing.unsubscribed_all", "operator": "eq", "value": True}},
    {"id": "neg_role_mismatch", "negative": True, "points": -5,
     "observation": "Contact role has no purchasing involvement",
     "condition": {"field": "contact.seniority", "operator": "in",
                   "value": ["intern", "student"]}},
]


# Four illustrative leads (Epic 5 enriched payloads)

HOT_LEAD = LeadFeatures(
    lead_id="L-1001", tenant_id="venturedesk",
    fields={
        "company": {"industry": "saas", "employee_count": 180,
                    "tech_stack": ["mailchimp", "zendesk"], "country": "IN",
                    "revenue_usd_m": 12, "months_since_funding": 5,
                    "open_sales_roles": 3, "is_competitor": False},
        "events": {"demo_requested_at": "2026-06-08T10:00:00Z",
                   "replied_to_outreach": True, "webinars_attended": 1},
        "web": {"pricing_page_visits": 4, "competitor_pages_viewed": 2,
                "sessions_14d": 6},
        "marketing": {"email_opens": 5},
        "contact": {"seniority": "vp", "email_disposable": False},
    },
    # Epic 5 pre-computed one signal — engine must prefer this path:
    signal_values={"intent": {"intent_trial_started": False}},
)

SPARSE_LEAD = LeadFeatures(
    lead_id="L-1002", tenant_id="venturedesk",
    fields={
        "events": {"demo_requested_at": "2026-06-10T09:00:00Z"},
        "web": {"pricing_page_visits": 3},
    },
    # almost nothing enriched: most conditions hit missing fields.
    # NOTE missing-field conditions evaluate to False (did not fire), they
    # are not "skipped" — skipped means unevaluable (no value, no condition).
)

BLOCKED_LEAD = LeadFeatures(
    lead_id="L-1003", tenant_id="venturedesk",
    fields={
        "company": {"industry": "saas", "employee_count": 300,
                    "is_competitor": True},
        "web": {"pricing_page_visits": 9},
    },
    field_confidence={"company.is_competitor": 0.95},
)

WEAK_BLOCK_LEAD = LeadFeatures(
    lead_id="L-1004", tenant_id="venturedesk",
    fields={
        "company": {"industry": "fintech", "employee_count": 90,
                    "country": "US", "is_competitor": True},
        "web": {"pricing_page_visits": 2, "sessions_14d": 4},
        "marketing": {"email_opens": 4},
    },
    # competitor flag came from a low-confidence source -> block must be skipped
    field_confidence={"company.is_competitor": 0.4},
)


def main() -> None:
    signal_set = build_signal_set(
        EPIC3_SIGNALS, SCORING_WEIGHTS_FINAL, THRESHOLDS,
        tenant_id="venturedesk", version=1,
    )
    print(f"SignalSet built via adapter: {len(signal_set.signals)} signals, "
          f"{len(signal_set.negative_signals)} negative signals, "
          f"budgets {signal_set.weights.as_dict()}\n")

    engine = ScoringEngine()
    for lead in (HOT_LEAD, SPARSE_LEAD, BLOCKED_LEAD, WEAK_BLOCK_LEAD):
        result = engine.score(lead, signal_set)
        print(result.explain())
        print("-" * 78)

    # determinism check, live:
    r1 = engine.score(HOT_LEAD, signal_set)
    r2 = engine.score(HOT_LEAD, signal_set)
    assert r1.model_dump(exclude={"scored_at"}) == r2.model_dump(exclude={"scored_at"})
    print("Determinism check: identical inputs -> identical results ")


if __name__ == "__main__":
    main()
