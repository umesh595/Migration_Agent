from app.core.patch_applier import apply_patch_set
from app.schemas.architecture import ArchitectureModel, Component, Dependency
from app.schemas.patches import (
    AddComponentPatch,
    AddDependencyPatch,
    PatchOutcome,
    PatchSet,
    RemoveComponentPatch,
    RemoveDependencyPatch,
    UpdateComponentPatch,
)


def _model_with_two_components() -> ArchitectureModel:
    return ArchitectureModel(
        components=[
            Component(id="ml_inference", name="ML Inference", workload_type="ml_inference"),
            Component(id="postgres", name="Postgres", workload_type="database"),
        ],
        dependencies=[
            Dependency(id="ml_inference->postgres:data_read", source_id="ml_inference", target_id="postgres", kind="data_read")
        ],
    )


def test_add_component_applies_and_bumps_version():
    model = ArchitectureModel()
    patch_set = PatchSet(
        patches=[AddComponentPatch(id="api", name="API", workload_type="api_service")],
        narration="added API",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert new_model.version == 2
    assert new_model.get_component("api") is not None
    assert results[0].outcome == PatchOutcome.APPLIED


def test_add_duplicate_component_is_rejected_not_applied():
    model = _model_with_two_components()
    patch_set = PatchSet(
        patches=[AddComponentPatch(id="postgres", name="Postgres 2", workload_type="database")],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert new_model.version == model.version  # untouched
    assert results[0].outcome == PatchOutcome.REJECTED
    assert "already exists" in results[0].reason


def test_llm_cannot_reference_nonexistent_component_in_dependency():
    model = _model_with_two_components()
    patch_set = PatchSet(
        patches=[AddDependencyPatch(source_id="ml_inference", target_id="nonexistent", kind="sync_call")],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert len(new_model.dependencies) == 1  # unchanged


def test_scenario_from_design_doc_remove_and_add_dependency():
    """The exact worked example from Doc 3 §3.2: 'the ML service doesn't access
    PostgreSQL directly — it reads from the warehouse.'"""

    model = _model_with_two_components()
    model.components.append(Component(id="warehouse", name="Warehouse", workload_type="data_warehouse"))

    patch_set = PatchSet(
        patches=[
            RemoveDependencyPatch(source_id="ml_inference", target_id="postgres"),
            AddDependencyPatch(source_id="ml_inference", target_id="warehouse", kind="data_read"),
        ],
        narration="Updated: ML inference reads from the warehouse, not Postgres directly.",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert all(r.outcome == PatchOutcome.APPLIED for r in results)
    dep_targets = {d.target_id for d in new_model.dependencies if d.source_id == "ml_inference"}
    assert dep_targets == {"warehouse"}
    assert new_model.version == model.version + 2


def test_remove_component_cascades_dependency_removal():
    model = _model_with_two_components()
    patch_set = PatchSet(patches=[RemoveComponentPatch(id="postgres")], narration="")
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.get_component("postgres") is None
    assert new_model.dependencies == []


def test_update_component_rejects_a_no_op_patch_that_sets_no_fields():
    model = _model_with_two_components()
    patch_set = PatchSet(patches=[UpdateComponentPatch(id="postgres")], narration="")
    _, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "nothing to change" in results[0].reason


def test_update_component_applies_only_the_fields_actually_set():
    model = _model_with_two_components()
    patch_set = PatchSet(
        patches=[UpdateComponentPatch(id="postgres", technology="PostgreSQL 16", criticality="tier-1")],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    updated = new_model.get_component("postgres")
    assert updated.technology == "PostgreSQL 16"
    assert updated.criticality == "tier-1"
    assert updated.name == "Postgres"  # untouched — wasn't set on the patch


def test_invalid_component_environment_is_rejected_without_crashing():
    model = ArchitectureModel()
    bad_patch = AddComponentPatch.model_construct(
        op="add_component",
        id="static_assets",
        name="Static Assets",
        workload_type="storage",
        environment="S3/CloudFront",
    )
    patch_set = PatchSet.model_construct(patches=[bad_patch], narration="")

    new_model, results = apply_patch_set(model, patch_set)

    assert new_model.components == []
    assert results[0].outcome == PatchOutcome.REJECTED
    assert "invalid environment" in results[0].reason


def test_add_dependency_self_loop_is_rejected_not_applied():
    """RemoveDependencyPatch validation must reject this before it ever reaches
    Dependency's own model_validator — otherwise the applier would raise an
    unhandled ValueError mid-patch-set instead of a clean rejection."""

    model = _model_with_two_components()
    patch_set = PatchSet(
        patches=[AddDependencyPatch(source_id="postgres", target_id="postgres", kind="data_read")],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "itself" in results[0].reason
    assert new_model.version == model.version


def test_remove_dependency_with_kind_removes_only_that_kind():
    model = _model_with_two_components()
    model.dependencies.append(
        Dependency(id="d2", source_id="ml_inference", target_id="postgres", kind="sync_call")
    )
    patch_set = PatchSet(
        patches=[RemoveDependencyPatch(source_id="ml_inference", target_id="postgres", kind="data_read")],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    remaining = [d.kind for d in new_model.dependencies if d.source_id == "ml_inference"]
    assert remaining == ["sync_call"]


def test_remove_dependency_without_kind_ambiguous_between_two_kinds_is_rejected():
    model = _model_with_two_components()
    model.dependencies.append(
        Dependency(id="d2", source_id="ml_inference", target_id="postgres", kind="sync_call")
    )
    patch_set = PatchSet(
        patches=[RemoveDependencyPatch(source_id="ml_inference", target_id="postgres")],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "multiple dependency kinds" in results[0].reason
    assert len(new_model.dependencies) == 2  # unchanged


def test_partial_batch_first_patch_valid_second_invalid_first_still_applied():
    model = _model_with_two_components()
    patch_set = PatchSet(
        patches=[
            AddComponentPatch(id="cache", name="Cache", workload_type="cache"),
            AddComponentPatch(id="postgres", name="dup", workload_type="database"),
        ],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    assert results[1].outcome == PatchOutcome.REJECTED
    assert new_model.get_component("cache") is not None
