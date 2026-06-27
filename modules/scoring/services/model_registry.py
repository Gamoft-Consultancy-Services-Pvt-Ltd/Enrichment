"""
COMP-1102-ST2 / ST3 — Model & prompt version registration (Epic 6, Domain 11)

Scoring's slice of the AI Model Registry. The registry SCHEMA and storage
(COMP-1102-ST1) are platform-level (Epic 3/5/6 shared). Scoring's contribution
is to DECLARE the AI assets it uses so the registry can inventory them:
- ST2: the model version(s) scoring invokes (the Sonnet fallback model).
- ST3: the prompt version(s) scoring uses (the fallback estimation prompt).

Scoring uses exactly one AI model in one place: the RatingClient fallback that
estimates skipped signals for low-confidence leads. The deterministic engine is
not an AI model and is not registered here (it has no model/prompt version).

This module exposes a single function the platform registry calls at startup or
on demand to pull scoring's declared AI assets. Keeping it as a pure declaration
(rather than scoring writing to the registry directly) preserves the boundary:
the registry owns storage; scoring owns the truth about what it runs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    """ST2 — a model scoring invokes."""

    name: str
    version: str
    provider: str
    purpose: str


@dataclass(frozen=True, slots=True)
class RegisteredPrompt:
    """ST3 — a prompt scoring uses."""

    name: str
    version: str
    purpose: str


@dataclass(frozen=True, slots=True)
class ScoringAIAssets:
    """The complete set of AI assets scoring declares to the registry."""

    component: str = "scoring"
    models: tuple[RegisteredModel, ...] = field(default_factory=tuple)
    prompts: tuple[RegisteredPrompt, ...] = field(default_factory=tuple)

    def to_registry_payload(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "models": [asdict(m) for m in self.models],
            "prompts": [asdict(p) for p in self.prompts],
        }


# Default values. >>> CONFIRM model_version matches the model string the real
# RatingClient calls, and prompt_version matches the version your fallback prompt
# is tagged with (the same values that flow into provenance ST4/ST5).
DEFAULT_FALLBACK_MODEL = "claude-sonnet-4-6"
DEFAULT_FALLBACK_PROMPT_VERSION = "signal-v3"


def declare_scoring_ai_assets(
    *,
    model_version: str = DEFAULT_FALLBACK_MODEL,
    prompt_version: str = DEFAULT_FALLBACK_PROMPT_VERSION,
) -> ScoringAIAssets:
    """Return scoring's AI assets for the model registry (COMP-1102-ST2/ST3).

    Called by the platform registry to inventory what scoring runs. The versions
    default to the runtime's current fallback configuration but are injectable so
    the registry can pin a specific deployed version.
    """
    return ScoringAIAssets(
        models=(
            RegisteredModel(
                name="rating-client-fallback",
                version=model_version,
                provider="anthropic",
                purpose="Estimate skipped signals for low-confidence leads",
            ),
        ),
        prompts=(
            RegisteredPrompt(
                name="skipped-signal-estimation",
                version=prompt_version,
                purpose="Prompt the fallback model to estimate would-fire signals",
            ),
        ),
    )
