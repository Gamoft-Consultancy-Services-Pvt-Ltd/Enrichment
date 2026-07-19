"""Public surface of the scoring module.

run_scoring is a function from (config, enrichment, lead) to a ScoringResult.
It takes no DB session — the caller loads the ACTIVE tenant_config and persists
the outcome. Callers import from here and nothing else in this module.
"""

from modules.lead_ingestion.db.models import Lead
from modules.scoring.judge import judge_signals
from modules.scoring.schemas import ScoringResult
from modules.scoring.scoring_engine import compute_score
from shared.events.schemas import EnrichmentResult
from shared.tenant_config.schemas import TenantConfigRead

__all__ = ["ScoringResult", "run_scoring"]


async def run_scoring(
    config: TenantConfigRead, enrichment: EnrichmentResult, lead: Lead
) -> ScoringResult:
    """Judge every signal with the LLM, then score deterministically."""
    judgments = await judge_signals(config, enrichment, lead)
    return compute_score(config, judgments)
