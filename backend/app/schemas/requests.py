"""What kind of request a user's message is.

The INTERPRETATION (which of these a given message is) is made by the LLM that
already reads that message — never by keyword matching. Natural language does not
decompose into a finite list of trigger words: a keyword classifier only recognizes
the technologies, phrasings and languages someone thought to enumerate, so it
silently mis-handles every system and every wording outside that list.

The POLICY attached to an intent (may this mutate the accepted source model? does it
need confirmation first?) stays deterministic — see core/request_intelligence.py.
That split is the same one the whole architecture rests on: the model judges meaning,
code decides what is allowed to happen as a result.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RequestIntent(StrEnum):
    SPARSE_INTAKE = "sparse_intake"
    CURRENT_FACT = "current_fact"
    SOURCE_CORRECTION = "source_correction"
    TARGET_PLANNING = "target_planning"
    HIGH_IMPACT_REPLATFORM = "high_impact_replatform"
    UNSCOPED_CAPABILITY = "unscoped_capability"
    REVIEW_EXPLANATION = "review_explanation"
    TERSE_CONFIRMATION = "terse_confirmation"
    PROCEED_WITH_ASSUMPTIONS = "proceed_with_assumptions"
    UNKNOWN = "unknown"


class RequestImpact(BaseModel):
    intent: RequestIntent
    confidence: str = Field(description="low, medium, or high")
    should_mutate_source: bool
    requires_confirmation: bool
    rationale: str
    impact_dimensions: list[str] = Field(default_factory=list)
