"""Computes what's still unknown about the model. Question generation (LLM) works
from this list, not from free-association — 'questions come from computed unknowns,
not LLM imagination' (technique #4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.schemas.architecture import ArchitectureModel, Environment


class GapCategory(StrEnum):
    OPEN_QUESTION = "open_question"
    SPARSE_ARCHITECTURE_CONTEXT = "sparse_architecture_context"
    BASIC_APP_REQUIREMENTS = "basic_app_requirements"
    ORPHAN_COMPONENT = "orphan_component"
    MISSING_ENVIRONMENT = "missing_environment"
    MISSING_CRITICALITY = "missing_criticality"
    UNCONFIRMED_ASSUMPTION = "unconfirmed_assumption"


_PRIORITY = {
    GapCategory.OPEN_QUESTION: 100,
    GapCategory.SPARSE_ARCHITECTURE_CONTEXT: 95,
    GapCategory.BASIC_APP_REQUIREMENTS: 90,
    GapCategory.ORPHAN_COMPONENT: 80,
    GapCategory.MISSING_ENVIRONMENT: 60,
    GapCategory.UNCONFIRMED_ASSUMPTION: 50,
    GapCategory.MISSING_CRITICALITY: 20,
}


_BASIC_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "user access channel (web, mobile, admin portal, API-only, device/IoT entry point)": (
        "frontend",
        "front end",
        "web app",
        "web ui",
        "react",
        "angular",
        "vue",
        "mobile",
        "ios",
        "android",
        "admin portal",
        "portal",
        "ui",
        "iot device",
        "device",
        "api-only",
        "api only",
    ),
    "backend/API or processing layer": (
        "backend",
        "api",
        "fastapi",
        "spring",
        "node",
        "django",
        "service",
        "worker",
        "processor",
        "processing",
    ),
    "primary data store and important data entities": (
        "database",
        "db",
        "postgres",
        "mysql",
        "sql server",
        "oracle",
        "mongodb",
        "dynamodb",
        "data store",
        "datastore",
        "datalake",
        "data lake",
        "warehouse",
        "employee",
        "booking",
        "records",
        "telemetry",
    ),
    "authentication, authorization, or user roles": (
        "auth",
        "authentication",
        "authorization",
        "login",
        "sso",
        "identity",
        "cognito",
        "role",
        "roles",
        "rbac",
    ),
    "external integrations, including payments if relevant": (
        "integration",
        "external",
        "third party",
        "payment",
        "gateway",
        "stripe",
        "hris",
        "erp",
        "crm",
        "sso",
    ),
    "async messaging, events, jobs, or protocols": (
        "queue",
        "kafka",
        "sqs",
        "pubsub",
        "pub/sub",
        "mqtt",
        "event",
        "events",
        "async",
        "worker",
        "job",
        "cron",
        "scheduler",
    ),
    "reporting, analytics, dashboards, or exports": (
        "report",
        "reports",
        "reporting",
        "analytics",
        "dashboard",
        "dashboards",
        "looker",
        "export",
        "csv",
        "pdf",
        "docx",
        "ppt",
    ),
    "security, audit, monitoring, compliance, retention, or PII constraints": (
        "security",
        "audit",
        "monitoring",
        "logs",
        "logging",
        "cloudwatch",
        "encryption",
        "pii",
        "compliance",
        "soc2",
        "hipaa",
        "gdpr",
        "retention",
        "years",
    ),
    "scale, traffic, data volume, or age of existing data": (
        "scale",
        "users",
        "traffic",
        "requests",
        "records",
        "rows",
        "millions",
        "miliions",
        "million",
        "volume",
        "4 years",
        "years",
        "peak",
    ),
}


class Gap(BaseModel):
    category: GapCategory
    description: str
    related_component_ids: list[str] = []
    priority: int


def _model_text(model: ArchitectureModel) -> str:
    parts: list[str] = []
    for component in model.components:
        parts.extend(
            [
                component.id,
                component.name,
                component.workload_type,
                component.description,
                component.technology or "",
                component.criticality or "",
                component.environment,
            ]
        )
    for dependency in model.dependencies:
        parts.extend([dependency.source_id, dependency.target_id, dependency.kind, dependency.description])
    for assumption in model.assumptions:
        parts.append(assumption.text)
    for question in model.open_questions:
        if question.resolved:
            parts.append(question.text)
    return " ".join(str(part).lower() for part in parts if part)


def _covered_basic_requirements(model: ArchitectureModel) -> set[str]:
    text = _model_text(model)
    covered: set[str] = set()
    for requirement, terms in _BASIC_REQUIREMENTS.items():
        if any(term in text for term in terms):
            covered.add(requirement)
    return covered


def _format_names(names: list[str], limit: int = 6) -> str:
    """'these 13 components' beats 13 near-identical sentences — a senior migration
    architect asks about a category of unknown once, grouped, not once per row."""

    shown = ", ".join(f"'{n}'" for n in names[:limit])
    if len(names) > limit:
        shown += f", and {len(names) - limit} more"
    return shown


def analyze_gaps(model: ArchitectureModel) -> list[Gap]:
    gaps: list[Gap] = []

    for question in model.open_questions:
        if not question.resolved:
            gaps.append(
                Gap(
                    category=GapCategory.OPEN_QUESTION,
                    description=question.text,
                    related_component_ids=question.related_component_ids,
                    priority=_PRIORITY[GapCategory.OPEN_QUESTION],
                )
            )

    connected_ids = {d.source_id for d in model.dependencies} | {d.target_id for d in model.dependencies}
    is_multi_component = len(model.components) > 1

    if len(model.components) <= 1 and not model.dependencies:
        component_names = [c.name for c in model.components] or ["the application"]
        gaps.append(
            Gap(
                category=GapCategory.SPARSE_ARCHITECTURE_CONTEXT,
                description=(
                    "The input describes the business purpose but not enough architecture to produce a "
                    "credible migration model. Ask a compact consultant-style intake question covering the "
                    "basic application facts a non-technical user may know: user access channel "
                    "(web/mobile/admin), backend/API shape, database or data store, authentication/roles, "
                    "payments if relevant, integrations, notifications, reporting/exports, files/storage, "
                    "monitoring/audit needs, current hosting/source environment if anything already exists, "
                    "target cloud or desired outcome, rough scale/data volume, and downtime tolerance. "
                    "Make clear that rough answers are fine and that unknown items can stay unknown. "
                    f"Known so far: {_format_names(component_names)}."
                ),
                related_component_ids=[c.id for c in model.components],
                priority=_PRIORITY[GapCategory.SPARSE_ARCHITECTURE_CONTEXT],
            )
        )
    elif model.components:
        covered = _covered_basic_requirements(model)
        missing_requirements = [
            requirement for requirement in _BASIC_REQUIREMENTS if requirement not in covered
        ]
        if missing_requirements:
            gaps.append(
                Gap(
                    category=GapCategory.BASIC_APP_REQUIREMENTS,
                    description=(
                        "Discovery is not complete yet. Ask one grouped follow-up covering these missing "
                        "basic application requirements before allowing the current architecture to feel "
                        "finished: "
                        + "; ".join(missing_requirements)
                        + ". The user can answer with details or explicitly say none/not applicable for any item."
                    ),
                    related_component_ids=[component.id for component in model.components],
                    priority=_PRIORITY[GapCategory.BASIC_APP_REQUIREMENTS],
                )
            )

    orphans = [c for c in model.components if is_multi_component and c.id not in connected_ids]
    missing_environment = [c for c in model.components if c.environment == Environment.UNKNOWN]
    missing_criticality = [c for c in model.components if not c.criticality]

    # One gap per CATEGORY across every affected component, never one gap per
    # component — the mechanical "how critical is X?" repeated for every row is
    # exactly the schema-validator behavior this function must not produce.
    if orphans:
        verb = "has" if len(orphans) == 1 else "have"
        gaps.append(
            Gap(
                category=GapCategory.ORPHAN_COMPONENT,
                description=f"{_format_names([c.name for c in orphans])} {verb} no known dependencies in or "
                "out — is that accurate, or are there connections we haven't captured yet?",
                related_component_ids=[c.id for c in orphans],
                priority=_PRIORITY[GapCategory.ORPHAN_COMPONENT],
            )
        )

    if missing_environment:
        # Framed as a system-level question, not a per-component checklist: in the
        # overwhelmingly common case every affected component belongs to the one
        # system being discovered and shares one hosting model — "confirm the
        # environment for A, B, C, D, E" reads like a form even when it's grouped
        # into a single sentence. Component names are still attached below (for
        # code/audit use, e.g. narrowing which components a correction applies to)
        # but generate_questions is told to lead with the system-level framing.
        gaps.append(
            Gap(
                category=GapCategory.MISSING_ENVIRONMENT,
                description="This system's current hosting model (on-prem, cloud, hybrid) is unstated — ask "
                "about it once, at the system level, rather than per component, unless the message already "
                f"gives contradictory signals for specific ones. Affects: {_format_names([c.name for c in missing_environment])}",
                related_component_ids=[c.id for c in missing_environment],
                priority=_PRIORITY[GapCategory.MISSING_ENVIRONMENT],
            )
        )

    if missing_criticality:
        gaps.append(
            Gap(
                category=GapCategory.MISSING_CRITICALITY,
                description="Business criticality is still unconfirmed for: "
                f"{_format_names([c.name for c in missing_criticality])}. Infer a reasonable tier from each "
                "component's role (user-facing/auth/API/core data store/queue/AI runtime usually tier-1; "
                "observability/export/reporting helpers usually tier-2) and ask for confirmation in one "
                "grouped question rather than one question per component.",
                related_component_ids=[c.id for c in missing_criticality],
                priority=_PRIORITY[GapCategory.MISSING_CRITICALITY],
            )
        )

    for assumption in model.assumptions:
        if assumption.raised_by == "llm" and not assumption.resolved:
            gaps.append(
                Gap(
                    category=GapCategory.UNCONFIRMED_ASSUMPTION,
                    description=f"Assuming: {assumption.text} — is that correct? "
                    f"(assumption id: {assumption.id})",
                    related_component_ids=assumption.related_component_ids,
                    priority=_PRIORITY[GapCategory.UNCONFIRMED_ASSUMPTION],
                )
            )

    return sorted(gaps, key=lambda g: g.priority, reverse=True)


def top_gaps(model: ArchitectureModel, n: int = 3) -> list[Gap]:
    return analyze_gaps(model)[:n]
