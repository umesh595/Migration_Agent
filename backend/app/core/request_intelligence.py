"""Deterministic user-message classification.

The LLM still does the nuanced extraction and prose, but this layer gives every
stage the same grounded hint about what kind of request it is handling. That is
the difference between "please be smart" prompting and a product behavior the
code can test.
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


_REPLATFORM_TERMS = (
    "java",
    "spring boot",
    "node",
    "node.js",
    "go ",
    "golang",
    ".net",
    "dotnet",
    "python",
    "fastapi",
    "django",
    "rails",
)

_UNSCOPED_CAPABILITY_TERMS = (
    "stripe",
    "payment",
    "billing",
    "invoice",
    "subscription",
)

_TARGET_TERMS = (
    "target",
    "modernized",
    "migrate to",
    "move to",
    "run ",
    "host ",
    "retain ",
    "ecs",
    "fargate",
    "eks",
    "kubernetes",
    "rds",
    "cloudfront",
    "cloud run",
)

_MIGRATION_CONTEXT_TERMS = (
    "source is",
    "source environment",
    "downtime",
    "maintenance window",
    "zero downtime",
    "no data loss",
    "rpo",
    "rto",
)

_REVIEW_TERMS = (
    "why",
    "explain",
    "compare",
    "review",
    "check",
    "better than",
    "instead of",
    "effort",
    "cost",
    "efficiency",
    "risk",
    "validation",
    "rollback",
)

_SOURCE_CORRECTION_TERMS = (
    "forgot",
    "source architecture",
    "current source",
    "current architecture",
    "actually",
    "correction",
)

_CONFIRMATION_TERMS = (
    "yes",
    "confirmed",
    "firm",
    "revise",
    "keep",
    "remove",
    "do not",
    "don't",
    "standalone",
    "no connections",
    "no dependencies",
    "exploratory",
)

_PROCEED_TERMS = (
    "just give it",
    "give it",
    "just proceed",
    "proceed with a draft",
    "proceed with the draft",
    "proceed with draft",
    "just draft it",
    "draft it anyway",
    "just go ahead",
    "go ahead and",
    "use your best judg",
    "your best guess",
    "make reasonable assumptions",
    "make assumptions",
    "i don't have more details",
    "i dont have more details",
    "don't have more details",
    "no more details",
    "figure it out",
    "you decide",
    "whatever you think",
    "best guess",
    "do migration plan",
    "do the migration plan",
    "just do it",
)

_ARCHITECTURE_SIGNAL_TERMS = (
    "frontend",
    "front end",
    "ui",
    "mobile",
    "backend",
    "back end",
    "api",
    "service",
    "worker",
    "database",
    "db",
    "postgres",
    "mysql",
    "sql server",
    "oracle",
    "mongodb",
    "redis",
    "cache",
    "queue",
    "kafka",
    "sqs",
    "pubsub",
    "pub/sub",
    "mqtt",
    "datalake",
    "data lake",
    "warehouse",
    "s3",
    "gcs",
    "cloud storage",
    "payment gateway",
    "auth",
    "sso",
    "identity",
    "notification",
    "email",
    "scheduler",
    "cron",
    "etl",
    "looker",
)

_BUSINESS_APP_TERMS = (
    "app",
    "application",
    "system",
    "platform",
    "website",
    "portal",
    "tracker",
    "booking",
    "accepting",
    "tracks",
    "track",
    "manage",
    "manages",
)


def classify_user_request(user_message: str, *, after_gate_1: bool = False, review_stage: bool = False) -> RequestImpact:
    text = " ".join(user_message.lower().split())

    if _contains_any(text, _REVIEW_TERMS) and review_stage and not _starts_with_change(text):
        return RequestImpact(
            intent=RequestIntent.REVIEW_EXPLANATION,
            confidence="high",
            should_mutate_source=False,
            requires_confirmation=False,
            rationale="The user is asking to inspect or explain the generated plan, not to change the model.",
            impact_dimensions=["effort", "cost", "risk", "validation", "rollback"],
        )

    if _contains_any(text, _SOURCE_CORRECTION_TERMS) and after_gate_1:
        return RequestImpact(
            intent=RequestIntent.SOURCE_CORRECTION,
            confidence="high",
            should_mutate_source=False,
            requires_confirmation=True,
            rationale=(
                "The user is correcting the accepted source architecture after Gate 1, so the "
                "app must confirm whether to revise the baseline."
            ),
            impact_dimensions=["effort", "cost", "sequencing", "validation", "rollback"],
        )

    if not after_gate_1 and _looks_like_sparse_intake(text):
        return RequestImpact(
            intent=RequestIntent.SPARSE_INTAKE,
            confidence="high",
            should_mutate_source=False,
            requires_confirmation=False,
            rationale=(
                "The user gave a thin business/application description, not enough deployable architecture "
                "detail to safely create components and dependencies."
            ),
            impact_dimensions=[
                "source_architecture",
                "tech_stack",
                "data",
                "auth",
                "integrations",
                "scale",
                "target",
            ],
        )

    if _contains_any(text, _TARGET_TERMS) and (_contains_any(text, _MIGRATION_CONTEXT_TERMS) or "target" in text):
        return RequestImpact(
            intent=RequestIntent.TARGET_PLANNING,
            confidence="high",
            should_mutate_source=False,
            requires_confirmation=False,
            rationale="The user is providing migration target/context, not correcting the source architecture.",
            impact_dimensions=["target_service_choice", "cost", "operations", "cutover"],
        )

    if _contains_any(text, _REPLATFORM_TERMS) and _contains_any(text, ("change", "replace", "switch", "from ", " to ")):
        return RequestImpact(
            intent=RequestIntent.HIGH_IMPACT_REPLATFORM,
            confidence="high",
            should_mutate_source=False,
            requires_confirmation=True,
            rationale="The user is proposing a technology/runtime replacement that changes delivery scope and risk.",
            impact_dimensions=["effort", "cost", "team_skills", "testing", "rollback", "dependencies"],
        )

    if _contains_any(text, _UNSCOPED_CAPABILITY_TERMS) and _contains_any(text, ("add", "include", "also")):
        return RequestImpact(
            intent=RequestIntent.UNSCOPED_CAPABILITY,
            confidence="medium",
            should_mutate_source=False,
            requires_confirmation=True,
            rationale=(
                "The user may be proposing a new business capability that needs justification "
                "before it becomes architecture."
            ),
            impact_dimensions=["scope", "cost", "security", "compliance", "integration"],
        )

    if _contains_any(text, _PROCEED_TERMS):
        return RequestImpact(
            intent=RequestIntent.PROCEED_WITH_ASSUMPTIONS,
            confidence="high",
            should_mutate_source=True,
            requires_confirmation=False,
            rationale=(
                "The user explicitly asked to proceed despite incomplete information, so the app should "
                "draft with clearly labeled assumptions instead of asking another clarifying question."
            ),
            impact_dimensions=["completeness", "assumptions"],
        )

    if len(text.split()) <= 12 and _contains_any(text, _CONFIRMATION_TERMS):
        return RequestImpact(
            intent=RequestIntent.TERSE_CONFIRMATION,
            confidence="medium",
            should_mutate_source=False,
            requires_confirmation=False,
            rationale="The user appears to be answering a previous question tersely.",
            impact_dimensions=[],
        )

    return RequestImpact(
        intent=RequestIntent.CURRENT_FACT,
        confidence="medium",
        should_mutate_source=not after_gate_1,
        requires_confirmation=False,
        rationale="The message appears to describe or refine architecture facts.",
        impact_dimensions=[],
    )


def render_request_impact_for_prompt(impact: RequestImpact) -> str:
    return impact.model_dump_json(indent=2)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _starts_with_change(text: str) -> bool:
    return text.startswith(("add ", "remove ", "delete ", "change ", "replace ", "switch ", "use ", "move "))


def _looks_like_sparse_intake(text: str) -> bool:
    words = text.split()
    if len(words) > 24:
        return False
    if not _contains_any(text, _BUSINESS_APP_TERMS):
        return False

    architecture_signal_count = sum(1 for term in _ARCHITECTURE_SIGNAL_TERMS if term in text)
    provider_only_signal = any(provider in text for provider in ("gcp", "aws", "azure", "cloud"))

    # A provider name alone ("on GCP") says where something runs, not what the
    # architecture is. One vague term like "system" or "booking" is still intake.
    if architecture_signal_count == 0:
        return True
    if architecture_signal_count == 1 and provider_only_signal:
        non_provider_signals = [
            term
            for term in _ARCHITECTURE_SIGNAL_TERMS
            if term in text and term not in {"gcp", "aws", "azure", "cloud"}
        ]
        return len(non_provider_signals) <= 1
    return architecture_signal_count <= 1 and len(words) <= 12
