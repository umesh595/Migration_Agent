"""apply_cloud_discovery: matches live AWS resources to existing components by
name, filling technology/environment gaps deterministically instead of asking
the user a question no non-technical (or even technical-but-didn't-build-it)
user could reliably answer (discovery_agent_dynamic_spec.md §2)."""

from __future__ import annotations

from app.core.cloud_discovery import apply_cloud_discovery
from app.integrations.aws_provider import AWSFetchResult, AWSResourceRecord
from app.schemas.architecture import ArchitectureModel, AssumptionStatus, Component, Environment, WorkloadType


def test_name_overlap_alone_is_not_enough_kind_must_fit_workload_type():
    """A compute component ("Orders Service") sharing a name word with an RDS
    instance ("orders-prod-db") must NOT inherit "Amazon RDS" as its
    technology — a database resource is never a plausible match for an
    API/web-service-shaped component, no matter how the names overlap
    (live-observed failure: this exact pairing false-matched before the
    kind/workload_type compatibility check was added)."""

    model = _model(
        id="orders_service",
        name="Orders Service",
        workload_type=WorkloadType.API_SERVICE,
        environment=Environment.UNKNOWN,
    )
    inventory = AWSFetchResult(
        resources=[AWSResourceRecord(resource_id="db-orders-prod", name="orders-prod-db", kind="rds", detail="postgres")]
    )

    updated, notes = apply_cloud_discovery(model, inventory)

    assert updated.components[0].environment == Environment.UNKNOWN
    assert updated.components[0].technology is None
    assert notes == []


def _model(**component_kwargs) -> ArchitectureModel:
    defaults = {"id": "orders_db", "name": "Orders Database", "workload_type": WorkloadType.DATABASE}
    defaults.update(component_kwargs)
    return ArchitectureModel(components=[Component(**defaults)])


def test_matching_resource_fills_technology_and_environment():
    model = _model(environment=Environment.UNKNOWN, technology=None)
    inventory = AWSFetchResult(
        resources=[AWSResourceRecord(resource_id="db-orders-prod", name="orders-prod-db", kind="rds", detail="postgres")]
    )

    updated, notes = apply_cloud_discovery(model, inventory)

    component = updated.components[0]
    assert component.environment == Environment.CLOUD
    assert "RDS" in component.technology
    assert "postgres" in component.technology
    assert len(notes) == 1
    assert "Orders Database" in notes[0]

    cloud_assumptions = [a for a in updated.assumptions if a.raised_by == "cloud_scan"]
    assert len(cloud_assumptions) == 1
    assert cloud_assumptions[0].status == AssumptionStatus.CONFIRMED  # never a pending confirmation


def test_no_match_leaves_model_untouched():
    model = _model(environment=Environment.UNKNOWN, technology=None)
    inventory = AWSFetchResult(
        resources=[AWSResourceRecord(resource_id="fn-notify", name="send-notification-email", kind="lambda")]
    )

    updated, notes = apply_cloud_discovery(model, inventory)

    assert updated.components[0].environment == Environment.UNKNOWN
    assert updated.components[0].technology is None
    assert notes == []
    assert updated.assumptions == []


def test_already_stated_fact_is_never_overwritten():
    """A component the user already described (technology/environment set) is
    a stated fact — cloud discovery fills gaps, it never silently corrects
    something the user already told the system, even if a same-named AWS
    resource exists and looks like a match."""

    model = _model(environment=Environment.ON_PREM, technology="Self-managed PostgreSQL on a VM")
    inventory = AWSFetchResult(
        resources=[AWSResourceRecord(resource_id="db-orders-prod", name="orders-database", kind="rds", detail="postgres")]
    )

    updated, notes = apply_cloud_discovery(model, inventory)

    assert updated.components[0].environment == Environment.ON_PREM
    assert updated.components[0].technology == "Self-managed PostgreSQL on a VM"
    assert notes == []


def test_empty_inventory_is_a_noop():
    model = _model(environment=Environment.UNKNOWN)
    updated, notes = apply_cloud_discovery(model, AWSFetchResult(resources=[]))
    assert updated == model
    assert notes == []


def test_match_requires_a_real_shared_word_not_just_common_suffixes():
    """"database"/"service"/"domain" etc. are stripped before matching so two
    completely unrelated components don't "match" just because both happen to
    be named *-database or *-service."""

    model = _model(id="tickets_domain", name="Tickets Domain", environment=Environment.UNKNOWN)
    inventory = AWSFetchResult(
        resources=[AWSResourceRecord(resource_id="db-misc", name="misc-domain-database", kind="rds")]
    )

    updated, notes = apply_cloud_discovery(model, inventory)

    assert updated.components[0].environment == Environment.UNKNOWN
    assert notes == []
