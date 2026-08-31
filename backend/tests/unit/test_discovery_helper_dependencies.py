from app.orchestration.nodes.discovery import apply_patches_node
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel, DependencyKind, Environment
from app.schemas.patches import AddComponentPatch, PatchSet


def test_discovery_infers_obvious_docx_and_cloudwatch_helper_dependencies():
    patch_set = PatchSet(
        narration="Added IntentIQ components.",
        patches=[
            AddComponentPatch(
                id="fastapi_backend",
                name="FastAPI Backend",
                workload_type="api_service",
                environment=Environment.CLOUD,
                criticality="tier-1",
            ),
            AddComponentPatch(
                id="sqs_worker",
                name="SQS Worker",
                workload_type="batch_job",
                environment=Environment.CLOUD,
                criticality="tier-1",
            ),
            AddComponentPatch(
                id="fastapi_ai_service",
                name="FastAPI AI Service",
                workload_type="api_service",
                environment=Environment.CLOUD,
                criticality="tier-1",
            ),
            AddComponentPatch(
                id="s3_storage",
                name="S3 Storage",
                workload_type="storage",
                environment=Environment.CLOUD,
                criticality="tier-1",
            ),
            AddComponentPatch(
                id="docx_ppt_generation",
                name="DOCX/PPT Generation Libraries",
                workload_type="other",
                environment=Environment.CLOUD,
                criticality="tier-2",
            ),
            AddComponentPatch(
                id="diagram_rendering_toolchain",
                name="Diagram Rendering Toolchain",
                workload_type="other",
                environment=Environment.CLOUD,
                criticality="tier-2",
            ),
            AddComponentPatch(
                id="cloudwatch",
                name="CloudWatch",
                workload_type="other",
                environment=Environment.CLOUD,
                criticality="tier-2",
            ),
        ],
    )

    result = apply_patches_node(
        {
            "model": ArchitectureModel(),
            "_patch_set": patch_set,
            "stage": Stage.DISCOVERY,
            "session_id": "test",
        }
    )

    dependencies = {
        (dependency.source_id, dependency.target_id, dependency.kind)
        for dependency in result["model"].dependencies
    }

    assert ("sqs_worker", "docx_ppt_generation", DependencyKind.SYNC_CALL) in dependencies
    assert ("fastapi_ai_service", "docx_ppt_generation", DependencyKind.SYNC_CALL) in dependencies
    assert ("docx_ppt_generation", "s3_storage", DependencyKind.DATA_READ) in dependencies
    assert ("docx_ppt_generation", "s3_storage", DependencyKind.DATA_WRITE) in dependencies
    assert ("fastapi_backend", "cloudwatch", DependencyKind.EVENT_PUBLISH) in dependencies
    assert ("sqs_worker", "cloudwatch", DependencyKind.EVENT_PUBLISH) in dependencies
    assert ("fastapi_ai_service", "cloudwatch", DependencyKind.EVENT_PUBLISH) in dependencies
    assert ("diagram_rendering_toolchain", "cloudwatch", DependencyKind.EVENT_PUBLISH) in dependencies

    inferred_results = [
        result for result in result["last_patch_results"] if result.reason == "high-confidence helper dependency inferred from discovered component roles"
    ]
    assert inferred_results
