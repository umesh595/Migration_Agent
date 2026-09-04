"""The canonical ArchitectureModel — everything the user sees during Discovery is a
render of this object. Chat history is never the source of truth (Doc 3 §2.2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class WorkloadType(StrEnum):
    WEB_SERVICE = "web_service"
    API_SERVICE = "api_service"
    BATCH_JOB = "batch_job"
    DATABASE = "database"
    MESSAGE_QUEUE = "message_queue"
    CACHE = "cache"
    ML_INFERENCE = "ml_inference"
    ML_TRAINING = "ml_training"
    DATA_PIPELINE = "data_pipeline"
    DATA_WAREHOUSE = "data_warehouse"
    STORAGE = "storage"
    LOAD_BALANCER = "load_balancer"
    CDN = "cdn"
    THIRD_PARTY_INTEGRATION = "third_party_integration"
    OTHER = "other"


class DependencyKind(StrEnum):
    SYNC_CALL = "sync_call"
    ASYNC_CALL = "async_call"
    DATA_READ = "data_read"
    DATA_WRITE = "data_write"
    EVENT_PUBLISH = "event_publish"
    EVENT_SUBSCRIBE = "event_subscribe"
    NETWORK_ROUTE = "network_route"
    OTHER = "other"


class Environment(StrEnum):
    ON_PREM = "on_prem"
    CLOUD = "cloud"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class Component(BaseModel):
    id: str = Field(description="Stable slug identifier, unique within the model, e.g. 'ml_inference'.")
    name: str
    workload_type: WorkloadType
    environment: Environment = Environment.UNKNOWN
    description: str = ""
    technology: str | None = Field(default=None, description="e.g. 'PostgreSQL 14', 'Django 4.2'.")
    owner_team: str | None = None
    criticality: str | None = Field(default=None, description="Free-text business criticality, e.g. 'tier-1'.")


class Dependency(BaseModel):
    id: str = Field(description="Stable id, e.g. 'ml_inference->postgres'.")
    source_id: str
    target_id: str
    kind: DependencyKind
    description: str = ""
    # Evidence Ledger (discovery_agent_dynamic_spec.md upgrade): a short, specific
    # provenance note — a close paraphrase/quote of the triggering user text
    # ("user said: 'payment service depends on Redis'") or how it was inferred
    # ("inferred: the described booking flow needs somewhere to persist bookings").
    # Empty only for dependencies added before this field existed. See
    # app.core.evidence for the deterministic, always-fresh "used_by" computation —
    # deliberately NOT a stored field here, since staleness would make it worse
    # than no answer at all.
    source: str = ""

    @model_validator(mode="after")
    def _no_self_loop(self) -> Dependency:
        if self.source_id == self.target_id:
            raise ValueError(f"dependency '{self.id}' cannot connect a component to itself")
        return self


class AssumptionStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Assumption(BaseModel):
    id: str
    text: str
    raised_by: str = Field(description="'llm', 'user', 'cloud_scan', or 'contradiction_detector'.")
    related_component_ids: list[str] = Field(default_factory=list)
    # Evidence Ledger tri-state (discovery_agent_dynamic_spec.md upgrade): OPEN is
    # an LLM guess awaiting confirmation (a gap, surfaced by GapAnalyzer);
    # CONFIRMED is a fact the user stated directly or explicitly agreed with;
    # REJECTED is a guess the user explicitly said was wrong (see
    # ConfirmAssumptionPatch.rejected) — kept in the ledger rather than deleted,
    # so the audit trail shows what was tried and corrected, not just the final answer.
    # patch_applier.py sets this explicitly per construction site rather than
    # deriving it from `raised_by`, since "user-raised implies confirmed" is true
    # for every current call site but is a property of how each site constructs
    # the assumption, not an invariant this schema should silently assume.
    status: AssumptionStatus = AssumptionStatus.OPEN
    # Distinct from `status`: status means "is this still a pending guess",
    # confidence means "how sure was the user when they said this". "no idea,
    # maybe just a db transaction" and "we use SELECT FOR UPDATE" both read as
    # a plain confirmed fact once flattened to a confirmed assumption's text
    # alone — without this, a hedge on a load-bearing detail (double-booking
    # prevention, payment idempotency) is indistinguishable from a confident
    # answer to any downstream gap/coverage check, and discovery moves on as
    # if the risk were actually addressed.
    confidence: str = Field(
        default="stated",
        description="'stated' (a plain confirmed fact), 'hedged' (the user signaled uncertainty — "
        "'maybe', 'I think', 'probably', 'not sure'), or 'unsure' (the user said they don't know).",
    )
    # Evidence Ledger: see Dependency.source above — same contract, same reason
    # for staying free-text and provenance-specific rather than a fixed enum.
    source: str = ""


class OpenQuestion(BaseModel):
    id: str
    text: str
    related_component_ids: list[str] = Field(default_factory=list)
    resolved: bool = False


class ModelStatus(StrEnum):
    DRAFT = "draft"
    ACCEPTED = "accepted"


class ArchitectureModel(BaseModel):
    """Canonical current-state architecture. Mutated only via validated patches
    (see app.schemas.patches) — never edited in place from free-text LLM output."""

    components: list[Component] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    status: ModelStatus = ModelStatus.DRAFT
    version: int = 1
    # Inferred live from the user's own vocabulary/specificity/hedging each
    # turn (ingest_patches sets PatchSet.user_technical_signal; see
    # apply_patches_node) — never asked as an onboarding question, never a
    # fixed keyword classifier. "technical" only ever moves forward from
    # "unknown"/"non_technical" within a session (a user can reveal more
    # fluency over time; one terse reply should never flip it back), so this
    # is a one-way upgrade, not a per-turn re-classification. Routes question
    # CONTENT (generate_questions), not question existence.
    user_technical_level: str = "unknown"

    def component_ids(self) -> set[str]:
        return {c.id for c in self.components}

    def get_component(self, component_id: str) -> Component | None:
        return next((c for c in self.components if c.id == component_id), None)

    def dependencies_for(self, component_id: str) -> list[Dependency]:
        return [d for d in self.dependencies if component_id in (d.source_id, d.target_id)]
