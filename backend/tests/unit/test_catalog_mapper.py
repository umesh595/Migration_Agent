from app.core.patch_applier import apply_patch_set
from app.integrations.catalog_provider import CatalogComponentRecord, CatalogDependencyRecord, CatalogFetchResult
from app.integrations.mapper import external_component_id, map_catalog_result_to_patch_set
from app.schemas.architecture import ArchitectureModel, DependencyKind, Environment, WorkloadType
from app.schemas.patches import AddComponentPatch, AddDependencyPatch, PatchOutcome


def test_empty_fetch_result_maps_to_empty_patch_set_with_explanatory_narration():
    patch_set = map_catalog_result_to_patch_set(CatalogFetchResult())

    assert patch_set.patches == []
    assert "no matching components" in patch_set.narration


def test_known_workload_type_hint_maps_to_matching_enum_member():
    result = CatalogFetchResult(
        components=[
            CatalogComponentRecord(external_id="svc-1", name="Orders API", workload_type_hint="api_service")
        ]
    )
    patch_set = map_catalog_result_to_patch_set(result)

    assert len(patch_set.patches) == 1
    patch = patch_set.patches[0]
    assert isinstance(patch, AddComponentPatch)
    assert patch.workload_type == WorkloadType.API_SERVICE


def test_unknown_workload_type_hint_falls_back_to_third_party_integration():
    """An external system's vendor-specific type string that doesn't match any
    known hint must not be silently dropped or crash the mapping -- it should
    fall back to the one WorkloadType this project already reserves for exactly
    this situation."""

    result = CatalogFetchResult(
        components=[
            CatalogComponentRecord(external_id="svc-2", name="Legacy Mainframe Job", workload_type_hint="cobol_batch_xyz")
        ]
    )
    patch_set = map_catalog_result_to_patch_set(result)

    assert patch_set.patches[0].workload_type == WorkloadType.THIRD_PARTY_INTEGRATION


def test_unknown_environment_hint_falls_back_to_unknown_not_a_crash():
    result = CatalogFetchResult(
        components=[CatalogComponentRecord(external_id="svc-3", name="X", workload_type_hint="", environment_hint="mars")]
    )
    patch_set = map_catalog_result_to_patch_set(result)

    assert patch_set.patches[0].environment == Environment.UNKNOWN


def test_dependency_kind_hint_maps_and_ids_are_prefixed_consistently():
    result = CatalogFetchResult(
        components=[
            CatalogComponentRecord(external_id="a", name="A", workload_type_hint="api"),
            CatalogComponentRecord(external_id="b", name="B", workload_type_hint="database"),
        ],
        dependencies=[CatalogDependencyRecord(source_external_id="a", target_external_id="b", kind_hint="data_write")],
    )
    patch_set = map_catalog_result_to_patch_set(result)

    dep_patch = next(p for p in patch_set.patches if isinstance(p, AddDependencyPatch))
    assert dep_patch.kind == DependencyKind.DATA_WRITE
    assert dep_patch.source_id == external_component_id("a") == "ext:a"
    assert dep_patch.target_id == external_component_id("b") == "ext:b"


def test_mapped_patch_set_flows_through_the_real_validation_pipeline():
    """The point of mapping to a PatchSet instead of writing components
    directly: apply_patch_set/validate_patch must accept it exactly like an
    LLM-produced PatchSet, with no special-casing for its origin."""

    result = CatalogFetchResult(
        components=[
            CatalogComponentRecord(external_id="crm-account", name="CRM Account Service", workload_type_hint="api_service")
        ]
    )
    patch_set = map_catalog_result_to_patch_set(result)

    new_model, results = apply_patch_set(ArchitectureModel(), patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.get_component("ext:crm-account") is not None
    assert new_model.version == 2


def test_reimporting_the_same_external_id_is_rejected_not_duplicated():
    """Re-running an import for a record already present must not create a
    second component -- validate_patch's existing 'id already exists' rule
    is what makes catalog import idempotent, with no separate dedup logic
    needed in the integration code itself."""

    result = CatalogFetchResult(
        components=[CatalogComponentRecord(external_id="crm-account", name="CRM Account Service", workload_type_hint="api")]
    )
    patch_set = map_catalog_result_to_patch_set(result)

    model_after_first_import, _ = apply_patch_set(ArchitectureModel(), patch_set)
    model_after_second_import, second_results = apply_patch_set(model_after_first_import, patch_set)

    assert second_results[0].outcome == PatchOutcome.REJECTED
    assert "already exists" in second_results[0].reason
    assert model_after_second_import.version == model_after_first_import.version
    assert len(model_after_second_import.components) == 1
