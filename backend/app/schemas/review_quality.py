"""Review-quality scoring — the canonical, persisted form of a judge pass over the
LLM semantic critic's output. Distinct from app.llm.schemas.SemanticReviewJudgeOutput
(the raw LLM call contract) the same way ComponentPlan is distinct from
ComponentPlanLLMOutput: this is what gets stored and served, not what the model
returns verbatim.

PRD Decision Q7 deferred 'LLM-as-judge quality scoring' to v2; this module exists
because that decision was explicitly overridden for this build (see DECISIONS.md)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewQualityScore(BaseModel):
    iteration: int = Field(description="Refine-loop iteration this score was taken at, 0-indexed.")
    evaluated_finding_count: int = Field(description="Number of LLM-source findings the judge evaluated this iteration.")
    relevance_score: int = Field(ge=0, le=100)
    specificity_score: int = Field(ge=0, le=100)
    actionability_score: int = Field(ge=0, le=100)
    context_awareness_score: int = Field(ge=0, le=100)
    overall_score: int = Field(ge=0, le=100)
    rationale: str
    flagged_issues: list[str] = Field(default_factory=list)
