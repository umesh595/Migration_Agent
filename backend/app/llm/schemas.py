"""Structured-output contracts for LLM calls specifically (technique #10). These are
distinct from the canonical persisted schemas in app.schemas — an LLM call returns
one of these, and deterministic code (PlanAssembler, etc.) translates it into the
canonical artifact. Keeping the boundary explicit means a canonical schema change
doesn't silently change what we ask the model for, and vice versa."""

from __future__ import annotations

from typing import Literal

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


class RequirementCoverageVerdict(BaseModel):
    category: str = Field(
        description="A specific requirement area grounded in what THIS system actually does — e.g. 'seat "
        "locking during checkout' for a booking system, 'device provisioning' for an IoT platform, not a "
        "generic template label unless it genuinely applies generically to this system."
    )
    status: Literal["covered", "not_applicable", "hedged_or_uncertain", "unknown", "escalate_as_risk"] = Field(
        description="'covered' if the user states this plainly and confidently — casual hedge WORDS around a "
        "clear core claim ('no compliance framework that i know of, so none i guess') still count as covered/"
        "not_applicable; the CLAIM itself is unambiguous even if the phrasing is casual. 'not_applicable' if "
        "the user explicitly said this doesn't apply. 'hedged_or_uncertain' ONLY when the substance itself is "
        "uncertain — the user doesn't know if their answer actually satisfies the need (e.g. 'maybe just a db "
        "transaction' — they don't know if that's sufficient). 'unknown' if genuinely unaddressed either way. "
        "'escalate_as_risk': this exact concern already appears in the injected model as a hedged/unsure "
        "assumption from an earlier turn, and this turn's message does NOT give a genuinely more confident "
        "answer than before — do not ask about it a second time; escalate it instead (see "
        "recommended_mitigation)."
    )
    high_impact: bool = Field(
        description="True if getting this wrong or leaving it vague would cause a real production problem "
        "for THIS system (e.g. double-booking, a payment charged twice, silent data loss) — false for "
        "cosmetic or nice-to-have areas. A hedged high_impact item is exactly the case that must not be "
        "silently treated as done."
    )
    evidence: str = Field(
        default="",
        description="What in the model/conversation supports this verdict — for 'hedged_or_uncertain' or "
        "'escalate_as_risk', quote the hedge itself (e.g. \"no idea how seat locking works, maybe just a db "
        "transaction\"). Empty when status is 'unknown'.",
    )
    recommended_mitigation: str = Field(
        default="",
        description="Required when status is 'escalate_as_risk': a concrete, specific technical "
        "recommendation grounded in what a senior architect would actually suggest for THIS category on THIS "
        "system (e.g. 'implement row-level locking or a unique constraint on (show_id, seat_id) to prevent "
        "double-booking' — not a generic 'consider best practices'). Empty for every other status.",
    )


class RequirementCoverageOutput(BaseModel):
    """Output of the discovery-loop requirement-coverage classification call
    (the generator half of a generator/critic pair — see
    RequirementCoverageCriticOutput). Replaces a fixed keyword-matched
    category checklist with domain-aware LLM judgment: a keyword scan can't
    tell "we removed SSO" from "we have SSO", can't handle paraphrase, and
    can only ever check categories a human anticipated in advance. This call
    classifies coverage WITH evidence, and is free to propose requirement
    categories specific to this system's domain that a fixed list would
    never anticipate.
    """

    requirements: list[RequirementCoverageVerdict] = Field(
        description="Every requirement area worth tracking for THIS system — seeded from common baseline "
        "categories (auth/roles, external integrations, async messaging/events, reporting/analytics, "
        "security/compliance/PII, scale/traffic) but free to drop any that are clearly irrelevant to this "
        "kind of system and add domain-specific ones that matter more."
    )


class RequirementCoverageCriticOutput(BaseModel):
    """Second, independent opinion on the generator's own verdicts (technique
    #8's rules->critic->judge pattern, applied a third place this session:
    ingestion, then this). Re-reads the SAME conversation the generator saw
    and checks three specific failure modes: something marked 'covered' that
    was actually just a hedge on the substance (not just casual phrasing of a
    clear answer), a high-impact category this system class would obviously
    need that the generator didn't even consider, and a hedge that has
    already been asked about once before and should now escalate instead of
    repeating the same question a third time.
    """

    corrected_requirements: list[RequirementCoverageVerdict] = Field(
        description="The final verdict list: start from the generator's verdicts, downgrade any wrongly "
        "marked 'covered' that were actually hedged/uncertain given the conversation (but do NOT downgrade a "
        "clear claim just because it's phrased casually — 'none i guess' is still not_applicable), upgrade "
        "any hedge that was already asked about once before (visible as a hedged/unsure assumption in the "
        "injected model) to 'escalate_as_risk' with a concrete recommended_mitigation, and add any genuinely "
        "high-impact category this system class needs that the generator missed entirely. Keep everything "
        "the generator got right unchanged."
    )
    corrections_made: list[str] = Field(
        default_factory=list,
        description="One entry per change from the generator's original verdicts, in plain language (e.g. "
        "'seat locking was marked covered but the user said \"no idea, maybe just a db transaction\" — "
        "downgraded to hedged_or_uncertain', or 'double-booking prevention was already hedged last turn and "
        "still is — escalated as risk instead of re-asking'). Empty if the generator's verdicts needed no "
        "changes.",
    )


class IngestCompletenessCriticOutput(BaseModel):
    """Output of the discovery-loop ingest completeness critic (technique #8's
    rules->critic->judge pattern, applied to discovery ingestion instead of
    plan review). ingest_node's own patch proposal is trusted once and never
    independently checked — this is the second opinion: given the same user
    message and the patches about to be applied, does the resulting model
    actually capture everything stated? The most common way discovery repeats
    a question is a fact the user gave landing only in narration (shown once,
    discarded) rather than as a patch (durable, what gap analysis reads).
    """

    fully_captured: bool = Field(
        description="True only if every fact the user's message states or clearly implies will be durably "
        "reflected in the model after the proposed patches apply — not merely mentioned in narration."
    )
    missed_facts: list[str] = Field(
        default_factory=list,
        description="Concrete facts from the user's message that will NOT be reflected in the resulting "
        "model (e.g. 'PII fields: name, email, phone', 'roughly 50k users', 'no dedicated job queue'). "
        "Empty when fully_captured is true.",
    )
    invented_facts: list[str] = Field(
        default_factory=list,
        description="Facts the proposed patches introduce that the user's message does not state or clearly, "
        "reasonably imply — fabrications, not legitimate inferences.",
    )
    rationale: str = Field(description="One or two sentences justifying the verdict.")


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
