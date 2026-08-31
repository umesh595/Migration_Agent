"""Structured-output contracts for LLM calls specifically (technique #10). These are
distinct from the canonical persisted schemas in app.schemas — an LLM call returns
one of these, and deterministic code (PlanAssembler, etc.) translates it into the
canonical artifact. Keeping the boundary explicit means a canonical schema change
doesn't silently change what we ask the model for, and vice versa."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.cost import CloudProvider, ServiceCategory
from app.schemas.migration_plan import EfficiencyBreakdown, EffortBreakdown, SevenR, ValidationCheck


class GeneratedQuestion(BaseModel):
    text: str
    related_gap_description: str = Field(description="Echo of the Gap.description this question addresses.")


class QuestionGenerationOutput(BaseModel):
    """Output of the gap→questions LLM call (Discovery loop)."""

    questions: list[GeneratedQuestion]
    narration: str = Field(description="One or two sentences framing why these questions matter, shown before the questions.")


class ArchitectureSufficiencyOutput(BaseModel):
    """Output of the discovery-loop 'have we asked enough yet' judgment call.

    GapAnalyzer's fixed categories (open questions, orphan components, missing
    environment/criticality) are deliberately narrow and mechanical — they go
    silent as soon as those specific checks are satisfied, even when the
    described system is still far too thin for a credible migration plan (e.g.
    two connected components with a protocol name and nothing else: no real
    tech stack, no scale, no data model, no auth story). This call is the
    senior-architect judgment layer that decides whether discovery should keep
    probing when the mechanical checklist has nothing left to flag.
    """

    sufficient_for_planning: bool = Field(
        description="True only if a senior migration architect would consider this enough real detail "
        "(not just component names) to produce a credible, defensible migration plan."
    )
    rationale: str = Field(description="One or two sentences: what's known, and if insufficient, what's genuinely still missing.")
    next_question: str | None = Field(
        default=None,
        description="Required when sufficient_for_planning is false. ONE specific, senior-architect-quality "
        "question about what's still missing for THIS system — never a generic 'tell me more', and never "
        "repeating anything already captured in the injected model (components, dependencies, assumptions, "
        "resolved open questions). Omit when sufficient_for_planning is true.",
    )


class MigrationContextElicitationOutput(BaseModel):
    """Output of the LLM call that turns free-text context answers into a structured
    MigrationContext during the interrupt at Planning start. Fields mirror
    app.schemas.migration_context.MigrationContext but as an LLM-facing draft —
    still validated/coerced into the canonical type by code before use."""

    source_environment: str
    target_environment: str
    target_platform_description: str
    downtime_tolerance: str
    maintenance_window_description: str | None = None
    constraints: list[str] = Field(default_factory=list)
    target_completion_description: str | None = None
    clarifying_questions: list[str] = Field(
        default_factory=list, description="Non-empty only if the user's answer was too ambiguous to structure confidently."
    )


class ComponentPlanLLMOutput(BaseModel):
    """Output of per-component planning (technique #6). The LLM receives the
    component with its wave_index ALREADY FIXED by GraphEngine and plans only HOW it
    moves — this schema has no field for order/timing relative to other components,
    which is a deliberate omission, not an accident."""

    component_id: str
    target_description: str = Field(description="What this component looks like/becomes in the target environment.")
    disposition: SevenR
    target_cloud_provider: CloudProvider = Field(
        description="The cloud provider target_description actually names — classify what you just wrote, "
        "don't leave it unknown when target_description names a concrete AWS/Azure/GCP service."
    )
    target_service_category: ServiceCategory = Field(
        description="The category of the SPECIFIC service named in target_description, e.g. a managed "
        "Postgres/MySQL service is managed_database, an object store is object_storage."
    )
    steps: list[str]
    validation_checks: list[ValidationCheck]
    rollback_notes: str
    estimated_effort: str | None = None
    effort_breakdown: EffortBreakdown | None = None
    efficiency_breakdown: EfficiencyBreakdown | None = None
    dependencies_considered: list[str] = Field(default_factory=list)


class CutoverReviewOutput(BaseModel):
    approach: str
    rationale: str | None = None
    steps: list[str]
    go_no_go_criteria: list[str]
    communication_plan: str


class RollbackPlanOutput(BaseModel):
    approach: str
    rationale: str | None = None
    triggers: list[str]
    steps: list[str]
    data_reconciliation_notes: str | None = None


class TargetArchitectureOutput(BaseModel):
    description: str = Field(description="Narrative description of the target architecture as a whole.")


class ReviewDiscussionOutput(BaseModel):
    """Grounded answer for review-stage questions that discuss the generated plan
    without asking to mutate the architecture model."""

    answer: str = Field(
        description=(
            "Answer shown to the user. It must cite concrete plan facts, affected components, tradeoffs, "
            "validation, and rollback where relevant."
        )
    )


class LLMFindingOutput(BaseModel):
    severity: str
    message: str
    related_component_ids: list[str] = Field(default_factory=list)


class SemanticReviewOutput(BaseModel):
    """Output of the LLM critic pass (technique #8) — runs AFTER the zero-token rules
    engine, only for judgment calls a rule can't encode (e.g. 'is this rollback plan
    actually operationally realistic given the stated downtime tolerance')."""

    findings: list[LLMFindingOutput]


class SemanticReviewJudgeOutput(BaseModel):
    """Output of the independent judge pass over the semantic critic's findings
    (accelerated from the PRD's v2-deferred 'LLM-as-judge quality scoring', Decision
    Q7 — see DECISIONS.md). A second, independently-prompted model call scores the
    critic's own output; it never re-scores the deterministic rules, which are
    already provably correct and need no judge.

    Scores are 0-100. The judge is explicitly told what NOT to reward (restating a
    rule finding in different words, vague boilerplate, fabricated specifics) so a
    critic that says nothing when there's nothing to say scores well, not poorly."""

    relevance_score: int = Field(
        ge=0, le=100,
        description="Does every finding raise a genuine judgment call, not something RULE-001..007 already covers?",
    )
    specificity_score: int = Field(
        ge=0, le=100, description="Are findings concrete and grounded in this plan's actual components/steps, not generic advice?"
    )
    actionability_score: int = Field(
        ge=0, le=100, description="Could a migration team act on each finding without further clarification?"
    )
    context_awareness_score: int = Field(
        ge=0, le=100, description="Do findings account for the stated migration context (downtime tolerance, constraints)?"
    )
    overall_score: int = Field(ge=0, le=100, description="Holistic score — not required to be the average of the above.")
    rationale: str = Field(description="One or two sentences justifying the overall score.")
    flagged_issues: list[str] = Field(
        default_factory=list,
        description="Specific problems found, e.g. 'finding 2 restates RULE-004' or 'finding 1 is generic boilerplate'.",
    )
