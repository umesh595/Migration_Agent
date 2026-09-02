"""Live AWS read-only resource-inventory import (see DECISIONS.md's "PRD-bump
override" entry). Deliberately NOT shaped like app/integrations/catalog_provider.py's
ABC: that one wraps a single long-lived, app-wide provider instance constructed
once at startup from static config (app.state.catalog_provider) — this one is
constructed fresh per REQUEST from credentials the user supplies in that same
request, used once, and never persisted or cached (a session-scoped meter/
provider object living past the request would mean holding a user's AWS
secret in memory for the process's lifetime, which is exactly what "session-
scoped, never persisted" is supposed to prevent).

First pass covers EC2, RDS, Lambda, and S3 — the four resource types that
appear in nearly every real AWS footprint. ECS, VPC, and every other provider
(GCP/Azure/Oracle/Salesforce/codebase-connect) are explicit, later follow-ups
using this exact same pattern, not attempted here."""

from __future__ import annotations

import asyncio

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field

from app.schemas.architecture import Environment, WorkloadType
from app.schemas.patches import AddComponentPatch, Patch, PatchSet


class AWSCredentials(BaseModel):
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    region: str = "us-east-1"


class AWSResourceRecord(BaseModel):
    resource_id: str
    name: str
    kind: str
    detail: str = ""


class AWSFetchResult(BaseModel):
    resources: list[AWSResourceRecord] = Field(default_factory=list)


class AWSProviderError(Exception):
    """Raised whenever AWS cannot be reached, the credentials are rejected, or
    a call returns something outside its documented contract. Callers must
    treat this as 'no data' and never persist a partial result — mirrors
    CatalogProviderError's contract."""


def _fetch_sync(credentials: AWSCredentials) -> AWSFetchResult:
    """Synchronous — boto3 has no native asyncio support. Run via
    asyncio.to_thread from the async caller below rather than blocking the
    event loop directly."""

    session = boto3.Session(
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
        region_name=credentials.region,
    )
    resources: list[AWSResourceRecord] = []

    try:
        ec2 = session.client("ec2")
        for reservation in ec2.describe_instances().get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if instance.get("State", {}).get("Name") == "terminated":
                    continue
                name = next(
                    (t["Value"] for t in instance.get("Tags", []) if t["Key"] == "Name"),
                    instance["InstanceId"],
                )
                resources.append(
                    AWSResourceRecord(
                        resource_id=instance["InstanceId"],
                        name=name,
                        kind="ec2",
                        detail=instance.get("InstanceType", ""),
                    )
                )

        rds = session.client("rds")
        for db in rds.describe_db_instances().get("DBInstances", []):
            resources.append(
                AWSResourceRecord(
                    resource_id=db["DBInstanceIdentifier"],
                    name=db["DBInstanceIdentifier"],
                    kind="rds",
                    detail=db.get("Engine", ""),
                )
            )

        lambda_client = session.client("lambda")
        paginator = lambda_client.get_paginator("list_functions")
        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                resources.append(
                    AWSResourceRecord(
                        resource_id=fn["FunctionName"],
                        name=fn["FunctionName"],
                        kind="lambda",
                        detail=fn.get("Runtime", ""),
                    )
                )

        # S3 is region-agnostic at the list level — one call regardless of
        # `credentials.region`, unlike the other three clients above.
        s3 = session.client("s3")
        for bucket in s3.list_buckets().get("Buckets", []):
            resources.append(
                AWSResourceRecord(resource_id=bucket["Name"], name=bucket["Name"], kind="s3")
            )

    except (ClientError, BotoCoreError) as exc:
        raise AWSProviderError(f"AWS API error: {exc}") from exc

    return AWSFetchResult(resources=resources)


async def fetch_aws_inventory(credentials: AWSCredentials) -> AWSFetchResult:
    return await asyncio.to_thread(_fetch_sync, credentials)


_KIND_TO_WORKLOAD_TYPE: dict[str, WorkloadType] = {
    "ec2": WorkloadType.OTHER,
    "rds": WorkloadType.DATABASE,
    "lambda": WorkloadType.API_SERVICE,
    "s3": WorkloadType.STORAGE,
}

_KIND_TO_TECHNOLOGY_PREFIX: dict[str, str] = {
    "ec2": "Amazon EC2",
    "rds": "Amazon RDS",
    "lambda": "AWS Lambda",
    "s3": "Amazon S3",
}


def _aws_component_id(kind: str, resource_id: str) -> str:
    # Prefixed the same way mapper.py's external_component_id() is, for the
    # same reason: never collide with a user/LLM-authored id, and re-importing
    # the same resource is a no-op via validate_patch's existing
    # already-exists rejection rather than a new idempotency mechanism.
    return f"aws:{kind}:{resource_id}"


def map_aws_inventory_to_patch_set(result: AWSFetchResult) -> PatchSet:
    """Converts a live AWS inventory fetch into the same PatchSet shape the LLM
    ingestion node produces, so it flows through the identical
    validate_patch/apply_patch_set pipeline as every other model mutation.
    Pure function — no I/O, independently unit-testable."""

    patches: list[Patch] = []
    for resource in result.resources:
        technology = _KIND_TO_TECHNOLOGY_PREFIX.get(resource.kind, resource.kind.upper())
        patches.append(
            AddComponentPatch(
                id=_aws_component_id(resource.kind, resource.resource_id),
                name=resource.name,
                workload_type=_KIND_TO_WORKLOAD_TYPE.get(resource.kind, WorkloadType.OTHER),
                description=f"Imported from live AWS account ({resource.kind}: {resource.resource_id})",
                technology=f"{technology} ({resource.detail})" if resource.detail else technology,
                environment=Environment.CLOUD,
            )
        )

    if patches:
        by_kind: dict[str, int] = {}
        for resource in result.resources:
            by_kind[resource.kind] = by_kind.get(resource.kind, 0) + 1
        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(by_kind.items()))
        narration = f"Imported {len(patches)} component(s) from the live AWS account ({summary})."
    else:
        narration = "AWS import found no EC2, RDS, Lambda, or S3 resources in this account/region."

    return PatchSet(patches=patches, narration=narration)
