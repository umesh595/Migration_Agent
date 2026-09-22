"""Elicited once per session, after Gate 1, before sequencing runs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.architecture import Environment


class DowntimeTolerance(StrEnum):
    ZERO_DOWNTIME = "zero_downtime"
    MAINTENANCE_WINDOW = "maintenance_window"
    FLEXIBLE = "flexible"


class MigrationStrategyPreference(StrEnum):
    """A structural axis every migration has, regardless of domain — unlike
    the business-content questions this system otherwise derives dynamically
    per system, whether the user wants fastest-with-minimal-redesign or is
    open to real transformation is a project-management fact, not a business
    domain fact. It conditions per-component disposition choice, target
    architecture framing, and cutover pacing — see PLAN_COMPONENT and
    TARGET_ARCHITECTURE prompts."""

    LIFT_AND_SHIFT = "lift_and_shift"
    RE_ARCHITECT = "re_architect"
    UNDECIDED = "undecided"


class MigrationContext(BaseModel):
    source_environment: Environment
    target_environment: Environment
    target_platform_description: str = Field(description="e.g. 'AWS, containerized on EKS'.")
    downtime_tolerance: DowntimeTolerance
    maintenance_window_description: str | None = None
    constraints: list[str] = Field(default_factory=list, description="Compliance, budget, timeline, team constraints.")
    target_completion_description: str | None = None
    strategy_preference: MigrationStrategyPreference = Field(
        default=MigrationStrategyPreference.UNDECIDED,
        description="Fastest/minimal-redesign vs. treat this as a chance to modernize vs. not yet stated. "
        "UNDECIDED is a real, actionable value — it means every downstream stage must surface this fork "
        "explicitly rather than silently picking a side.",
    )
