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
    metadata: dict[str, str] = Field(default_factory=dict)


class Dependency(BaseModel):
    id: str = Field(description="Stable id, e.g. 'ml_inference->postgres'.")
    source_id: str
    target_id: str
    kind: DependencyKind
    description: str = ""

    @model_validator(mode="after")
    def _no_self_loop(self) -> Dependency:
        if self.source_id == self.target_id:
            raise ValueError(f"dependency '{self.id}' cannot connect a component to itself")
        return self


class Assumption(BaseModel):
    id: str
    text: str
    raised_by: str = Field(description="'llm' or 'user'.")
    related_component_ids: list[str] = Field(default_factory=list)


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

    def component_ids(self) -> set[str]:
        return {c.id for c in self.components}

    def get_component(self, component_id: str) -> Component | None:
        return next((c for c in self.components if c.id == component_id), None)

    def dependencies_for(self, component_id: str) -> list[Dependency]:
        return [d for d in self.dependencies if component_id in (d.source_id, d.target_id)]
