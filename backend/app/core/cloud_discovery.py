"""Cloud-discovery-first (discovery_agent_dynamic_spec.md §2): before asking
the user a purely technical/structural question (what does this run on,
what's the database engine, is it hosted in the cloud), check whether a
live, read-only scan of a CONNECTED cloud account can answer it instead — an
infrastructure fact is not a business fact, and no amount of better phrasing
makes "what database does the Order domain use" answerable by someone who
didn't build the system.

Matching is deterministic, not LLM-driven — the same "LLM proposes business/
architecture facts, code decides mechanical matching" boundary this codebase
already draws for dependency inference and criticality defaults. A live cloud
resource's identity is either a plausible name match for an existing
component or it isn't; that's a mechanical question, not a judgment call, and
keeping it deterministic makes it fast, free (no LLM call to fill an
infrastructure fact), and fully unit-testable without a mocked LLM.
"""

from __future__ import annotations

import re

from app.integrations.aws_provider import _KIND_TO_TECHNOLOGY_PREFIX, AWSFetchResult, AWSResourceRecord
from app.schemas.architecture import ArchitectureModel, Assumption, Component, Environment, WorkloadType

_STOPWORDS = {"the", "a", "an", "and", "or", "for", "of", "to"}
_STRIPPED_SUFFIXES = ("domain", "service", "database", "db", "api", "backend", "frontend", "server", "system")

# A word-overlap match alone isn't enough — "Orders Service" (a compute
# component) and "orders-prod-db" (an RDS instance) share the word "orders"
# but are not the same resource. Each AWS resource kind is only a plausible
# match for workload types it could actually be; a name match against a
# kind-incompatible resource is treated as no match at all, not a low-
# confidence one, since there's no partial-credit reading of "this database
# is actually your API service."
_DATA_WORKLOAD_TYPES = {WorkloadType.DATABASE, WorkloadType.CACHE, WorkloadType.DATA_WAREHOUSE, WorkloadType.STORAGE}
_COMPUTE_WORKLOAD_TYPES = {
    WorkloadType.WEB_SERVICE,
    WorkloadType.API_SERVICE,
    WorkloadType.BATCH_JOB,
    WorkloadType.ML_INFERENCE,
    WorkloadType.ML_TRAINING,
    WorkloadType.DATA_PIPELINE,
    WorkloadType.LOAD_BALANCER,
    WorkloadType.THIRD_PARTY_INTEGRATION,
    WorkloadType.OTHER,
}
_KIND_COMPATIBLE_WORKLOAD_TYPES: dict[str, set[WorkloadType]] = {
    "rds": _DATA_WORKLOAD_TYPES,
    "s3": {WorkloadType.STORAGE, WorkloadType.DATA_WAREHOUSE, WorkloadType.DATA_PIPELINE, WorkloadType.CDN, WorkloadType.OTHER},
    "lambda": _COMPUTE_WORKLOAD_TYPES,
    # "ec2" is deliberately absent: general-purpose compute can plausibly back
    # almost any workload type (a self-managed database on EC2, a web
    # service, a batch job), so it stays unrestricted.
}


def _significant_words(name: str) -> set[str]:
    words = re.split(r"[^a-z0-9]+", name.lower())
    return {w for w in words if w and w not in _STOPWORDS and w not in _STRIPPED_SUFFIXES and len(w) >= 3}


def _is_kind_compatible(resource_kind: str, workload_type: WorkloadType) -> bool:
    allowed = _KIND_COMPATIBLE_WORKLOAD_TYPES.get(resource_kind)
    return allowed is None or workload_type in allowed


def _best_match(component: Component, resources: list[AWSResourceRecord]) -> AWSResourceRecord | None:
    component_words = _significant_words(f"{component.name} {component.id}")
    if not component_words:
        return None

    best: tuple[int, AWSResourceRecord] | None = None
    for resource in resources:
        if not _is_kind_compatible(resource.kind, component.workload_type):
            continue
        resource_words = _significant_words(resource.name)
        overlap = len(component_words & resource_words)
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, resource)
    return best[1] if best else None


def apply_cloud_discovery(model: ArchitectureModel, inventory: AWSFetchResult) -> tuple[ArchitectureModel, list[str]]:
    """Fills GAPS only — a component with `environment` already set or
    `technology` already stated is never touched, matching "never silently
    replace a stated fact" (this is a recognition/fill mechanism, not a
    correction one; a wrong existing fact is fixed by the user correcting it
    conversationally, same as any other stated-fact correction).

    Returns the updated model plus one short narration note per match, for
    the caller to fold into the turn's narration — this is the "lightweight
    recognition check" the spec calls for ("we found these N services — does
    this look complete?"), not a blocking confirmation question."""

    if not inventory.resources:
        return model, []

    updated = model.model_copy(deep=True)
    notes: list[str] = []

    for component in updated.components:
        needs_environment = component.environment == Environment.UNKNOWN
        needs_technology = not (component.technology or "").strip()
        if not (needs_environment or needs_technology):
            continue

        match = _best_match(component, inventory.resources)
        if match is None:
            continue

        technology_label = _KIND_TO_TECHNOLOGY_PREFIX.get(match.kind, match.kind.upper())
        if needs_technology:
            component.technology = f"{technology_label} ({match.detail})" if match.detail else technology_label
        if needs_environment:
            component.environment = Environment.CLOUD

        updated.assumptions.append(
            Assumption(
                id=f"A{len(updated.assumptions) + 1}",
                text=(
                    f'Cross-referenced with your connected AWS account: "{component.name}" matches a live '
                    f"{match.kind.upper()} resource ({match.resource_id}) - technology/environment set "
                    "automatically from that match, not asked as a question."
                ),
                raised_by="cloud_scan",
                related_component_ids=[component.id],
                resolved=True,  # a live cloud fact is not a pending confirmation (spec §2)
                confidence="stated",
            )
        )
        notes.append(f'"{component.name}" -> {match.kind.upper()} `{match.resource_id}`')

    return updated, notes
