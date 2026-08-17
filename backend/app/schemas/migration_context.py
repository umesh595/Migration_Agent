"""Elicited once per session, after Gate 1, before sequencing runs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.architecture import Environment


class DowntimeTolerance(StrEnum):
    ZERO_DOWNTIME = "zero_downtime"
    MAINTENANCE_WINDOW = "maintenance_window"
    FLEXIBLE = "flexible"


class MigrationContext(BaseModel):
    source_environment: Environment
    target_environment: Environment
    target_platform_description: str = Field(description="e.g. 'AWS, containerized on EKS'.")
    downtime_tolerance: DowntimeTolerance
    maintenance_window_description: str | None = None
    constraints: list[str] = Field(default_factory=list, description="Compliance, budget, timeline, team constraints.")
    target_completion_description: str | None = None
