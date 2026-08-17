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


class Finding(BaseModel):
    id: str
    source: FindingSource
    rule_id: str | None = Field(default=None, description="Set when source == RULE, e.g. 'RULE-001'.")
    severity: FindingSeverity
    message: str
    related_component_ids: list[str] = Field(default_factory=list)
    resolution_status: ResolutionStatus = ResolutionStatus.OPEN


class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    iteration: int = 0

    def has_blocking_errors(self) -> bool:
        return any(f.severity == FindingSeverity.ERROR and f.resolution_status == ResolutionStatus.OPEN for f in self.findings)
