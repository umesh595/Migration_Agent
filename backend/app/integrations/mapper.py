"""Pure mapping: external catalog records -> PatchSet. No I/O, no side effects —
keeps the CatalogProvider's HTTP/OAuth concerns fully separate from the
decision of how an external record becomes a Patch."""

from __future__ import annotations

from app.integrations.catalog_provider import CatalogFetchResult
from app.schemas.architecture import DependencyKind, Environment, WorkloadType
from app.schemas.patches import AddComponentPatch, AddDependencyPatch, Patch, PatchSet

_WORKLOAD_TYPE_HINTS: dict[str, WorkloadType] = {
    "web": WorkloadType.WEB_SERVICE,
    "web_service": WorkloadType.WEB_SERVICE,
    "api": WorkloadType.API_SERVICE,
    "api_service": WorkloadType.API_SERVICE,
    "batch": WorkloadType.BATCH_JOB,
    "batch_job": WorkloadType.BATCH_JOB,
    "database": WorkloadType.DATABASE,
    "db": WorkloadType.DATABASE,
    "queue": WorkloadType.MESSAGE_QUEUE,
    "message_queue": WorkloadType.MESSAGE_QUEUE,
    "cache": WorkloadType.CACHE,
    "storage": WorkloadType.STORAGE,
    "load_balancer": WorkloadType.LOAD_BALANCER,
    "cdn": WorkloadType.CDN,
}

_DEPENDENCY_KIND_HINTS: dict[str, DependencyKind] = {
    "sync": DependencyKind.SYNC_CALL,
    "sync_call": DependencyKind.SYNC_CALL,
    "async": DependencyKind.ASYNC_CALL,
    "async_call": DependencyKind.ASYNC_CALL,
    "read": DependencyKind.DATA_READ,
    "data_read": DependencyKind.DATA_READ,
    "write": DependencyKind.DATA_WRITE,
    "data_write": DependencyKind.DATA_WRITE,
    "publish": DependencyKind.EVENT_PUBLISH,
    "event_publish": DependencyKind.EVENT_PUBLISH,
    "subscribe": DependencyKind.EVENT_SUBSCRIBE,
    "event_subscribe": DependencyKind.EVENT_SUBSCRIBE,
    "network": DependencyKind.NETWORK_ROUTE,
    "network_route": DependencyKind.NETWORK_ROUTE,
}

_ENVIRONMENT_HINTS: dict[str, Environment] = {
    "cloud": Environment.CLOUD,
    "on_prem": Environment.ON_PREM,
    "on-prem": Environment.ON_PREM,
    "onprem": Environment.ON_PREM,
    "hybrid": Environment.HYBRID,
}


def external_component_id(external_id: str) -> str:
    """Prefixed so an imported component can never collide with a user- or
    LLM-authored component id that happens to share the same raw external
    identifier, and so re-importing the same external_id always maps back to
    the same component id — the property apply_patch_set's own
    already-exists rejection relies on for making re-import a no-op rather
    than a duplicate (see app/api/routers/integrations.py)."""

    return f"ext:{external_id}"


def map_catalog_result_to_patch_set(result: CatalogFetchResult) -> PatchSet:
    """Converts an external catalog fetch into the same PatchSet shape the LLM
    ingestion node produces (app/schemas/patches.py), so it flows through the
    identical validate_patch/apply_patch_set pipeline as every other model
    mutation. This function never touches an ArchitectureModel directly —
    that boundary is enforced by the caller, not by this function."""

    patches: list[Patch] = []

    for component in result.components:
        patches.append(
            AddComponentPatch(
                id=external_component_id(component.external_id),
                name=component.name,
                workload_type=_WORKLOAD_TYPE_HINTS.get(
                    component.workload_type_hint.lower(), WorkloadType.THIRD_PARTY_INTEGRATION
                ),
                description=f"Imported from enterprise catalog (external_id={component.external_id})",
                technology=component.technology,
                environment=_ENVIRONMENT_HINTS.get(component.environment_hint.lower(), Environment.UNKNOWN),
            )
        )

    for dependency in result.dependencies:
        patches.append(
            AddDependencyPatch(
                source_id=external_component_id(dependency.source_external_id),
                target_id=external_component_id(dependency.target_external_id),
                kind=_DEPENDENCY_KIND_HINTS.get(dependency.kind_hint.lower(), DependencyKind.OTHER),
                description="Imported from enterprise catalog",
            )
        )

    if patches:
        narration = (
            f"Imported {len(result.components)} component(s) and {len(result.dependencies)} "
            "dependency/dependencies from the enterprise catalog."
        )
    else:
        narration = "Enterprise catalog import returned no matching components."

    return PatchSet(patches=patches, narration=narration)
