"""Guard: the enrichment pipeline job is registered with the ARQ worker."""

from workers.jobs.orchestration import run_lead_pipeline
from workers.worker import WorkerSettings


def test_run_lead_pipeline_is_registered() -> None:
    assert run_lead_pipeline in WorkerSettings.functions
