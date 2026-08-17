"""Golden fixture: a scripted discovery transcript plus the exact model it must
produce. The LLM boundary is mocked (DECISIONS.md) so this gate tests OUR logic —
patch validation, sequencing, rules, assembly — without flaking on model wording.

The scenario is deliberately messy in the ways the PoC brief describes real estates:
a correction mid-conversation, a cyclic dependency, and a component the user
initially forgets."""

from app.llm.schemas import (
    ComponentPlanLLMOutput,
    CutoverReviewOutput,
    GeneratedQuestion,
    QuestionGenerationOutput,
    RollbackPlanOutput,
    TargetArchitectureOutput,
)
from app.schemas.migration_plan import ValidationCheck
from app.schemas.patches import (
    AddComponentPatch,
    AddDependencyPatch,
    PatchSet,
    RemoveDependencyPatch,
)

TURNS = [
    "We run an e-commerce platform: a storefront web app, an orders API, and a Postgres database.",
    "The storefront doesn't hit Postgres directly — it goes through the orders API.",
    "There's also a recommendations service. It reads from Postgres, and the orders API calls it for related products.",
]

SCRIPTED_PATCH_SETS = [
    PatchSet(
        patches=[
            AddComponentPatch(id="storefront", name="Storefront Web App", workload_type="web_service", technology="React"),
            AddComponentPatch(id="orders_api", name="Orders API", workload_type="api_service", technology="Django 4.2"),
            AddComponentPatch(id="postgres", name="Postgres", workload_type="database", technology="PostgreSQL 14"),
            AddDependencyPatch(source_id="storefront", target_id="postgres", kind="data_read"),
            AddDependencyPatch(source_id="orders_api", target_id="postgres", kind="data_write"),
        ],
        narration="Captured the storefront, orders API, and Postgres.",
    ),
    PatchSet(
        patches=[
            RemoveDependencyPatch(source_id="storefront", target_id="postgres"),
            AddDependencyPatch(source_id="storefront", target_id="orders_api", kind="sync_call"),
        ],
        narration="Corrected: the storefront calls the orders API rather than reading Postgres directly.",
    ),
    PatchSet(
        patches=[
            AddComponentPatch(id="recommendations", name="Recommendations Service", workload_type="ml_inference"),
            AddDependencyPatch(source_id="recommendations", target_id="postgres", kind="data_read"),
            AddDependencyPatch(source_id="orders_api", target_id="recommendations", kind="sync_call"),
        ],
        narration="Added the recommendations service and its links.",
    ),
]

SCRIPTED_QUESTIONS = [
    QuestionGenerationOutput(
        questions=[GeneratedQuestion(text="Which environment does the Storefront run in?", related_gap_description="env")],
        narration="A few details would sharpen this.",
    ),
    QuestionGenerationOutput(
        questions=[GeneratedQuestion(text="How business-critical is the Orders API?", related_gap_description="criticality")],
        narration="Two more details.",
    ),
    QuestionGenerationOutput(
        questions=[GeneratedQuestion(text="What does the recommendations service run on?", related_gap_description="tech")],
        narration="Last few.",
    ),
]

# The exact model state the three turns must produce.
EXPECTED_COMPONENT_IDS = {"storefront", "orders_api", "postgres", "recommendations"}
EXPECTED_DEPENDENCIES = {
    ("orders_api", "postgres"),
    ("storefront", "orders_api"),
    ("recommendations", "postgres"),
    ("orders_api", "recommendations"),
}
# postgres has no outgoing deps -> wave 0. recommendations depends on postgres -> wave 1.
# orders_api depends on both -> wave 2. storefront depends on orders_api -> wave 3.
EXPECTED_WAVE_ORDER = [["postgres"], ["recommendations"], ["orders_api"], ["storefront"]]


def component_plan_outputs() -> list[ComponentPlanLLMOutput]:
    return [
        ComponentPlanLLMOutput(
            component_id=cid,
            target_description=f"{cid} on managed cloud infrastructure",
            disposition="replatform",
            steps=[f"provision target for {cid}", f"migrate {cid} traffic"],
            validation_checks=[ValidationCheck(description=f"verify {cid} responds correctly", check_type="smoke_test")],
            rollback_notes=f"restore {cid} to source environment and revert DNS",
            estimated_effort="3-5 days",
            dependencies_considered=[],
        )
        for cid in ["postgres", "recommendations", "orders_api", "storefront"]
    ]


CUTOVER = CutoverReviewOutput(
    approach="phased-by-wave with parallel run",
    steps=["cut over wave 0", "validate", "proceed wave by wave"],
    go_no_go_criteria=["all smoke tests green", "error rate below 0.1% for 30 minutes"],
    communication_plan="status page updates at each wave boundary",
)

ROLLBACK = RollbackPlanOutput(
    approach="per-wave rollback to source environment",
    triggers=["error rate above 1%", "data parity check failure"],
    steps=["revert DNS", "resync data from source", "confirm source healthy"],
    data_reconciliation_notes="replay writes captured in the CDC log during the parallel-run window",
)

TARGET_ARCHITECTURE = TargetArchitectureOutput(
    description="Managed cloud deployment: containerized services on Kubernetes with a managed Postgres instance."
)
