"""Review findings — from the deterministic RULE-001..007 engine (zero tokens) and
from the LLM semantic critic (technique #8)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingSource(StrEnum):
    RULE = "rule"
    LLM = "llm"


class ResolutionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED_AS_RISK = "accepted_as_risk"

    @classmethod
    def values(cls) -> set[str]:
        return {v.value for v in cls}


class Finding(BaseModel):
    id: str
    source: FindingSource
    rule_id: str | None = Field(default=None, description="Set when source == RULE, e.g. 'RULE-001'.")
    severity: FindingSeverity
    message: str
    related_component_ids: list[str] = Field(default_factory=list)
    resolution_status: ResolutionStatus = ResolutionStatus.OPEN
    # Evidence-backed review upgrade: a finding is a claim, and a claim needs a
    # citation — "rollback is weak" tells a reviewer nothing they can act on;
    # "rollback is weak because Orders DB is tier-1 and downtime tolerance is
    # zero-downtime, but the plan has no replica sync or rollback trigger" does.
    # All three are empty only for findings from before this field existed.
    violated_requirement: str = Field(
        default="", description="The specific stated fact/requirement this violates, quoting the model/context."
    )
    suggested_fix: str = Field(default="", description="A concrete, actionable fix — not generic advice.")
    risk_if_ignored: str = Field(default="", description="What concretely goes wrong in production if left unaddressed.")
