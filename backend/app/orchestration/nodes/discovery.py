"""Discovery subgraph nodes. LLM nodes are marked; every other node is deterministic.

Flow (Doc 3 §3.1):
  ingest (LLM) -> apply_patches (code) -> gap_analysis (code) -> generate_questions (LLM) -> interrupt
"""

from __future__ import annotations

import logging
import re

from app.core.gap_analyzer import Gap, GapCategory, top_gaps
from app.core.patch_applier import apply_patch_set
from app.core.request_intelligence import RequestIntent, render_request_impact_for_prompt
from app.llm.base import ModelTier, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.prompts.registry import get_prompt
from app.llm.schemas import QuestionGenerationOutput
from app.llm.state_injection import render_gaps_for_prompt, render_model_for_prompt
from app.observability.tracing import trace_node
from app.orchestration.state import GraphState, Stage
from app.schemas.architecture import ArchitectureModel, DependencyKind, Environment, WorkloadType
from app.schemas.patches import (
    AddAssumptionPatch,
    AddComponentPatch,
    AddDependencyPatch,
    ConfirmAssumptionPatch,
    PatchOutcome,
    PatchSet,
    ResolveOpenQuestionPatch,
    UpdateComponentPatch,
)

logger = logging.getLogger(__name__)


async def ingest_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: user text -> proposed patches. Closed-world: sees only the current model
    plus this message."""

    prompt = get_prompt("ingest_patches")
    impact = state.get("request_impact")
    impact_section = (
        f"DETERMINISTIC REQUEST CLASSIFICATION:\n{render_request_impact_for_prompt(impact)}\n\n"
        if impact is not None
        else ""
    )
    user_prompt = (
        f"{impact_section}CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"PREVIOUS AGENT MESSAGE, IF THE USER IS ANSWERING IT:\n{state.get('previous_agent_message') or '(none)'}\n\n"
        f"USER MESSAGE:\n{state['user_message']}"
    )

    try:
        # STRONG tier, not CHEAP: extracting dependency edges that are only implied
        # by prose spread across separate sections (a many-sources-to-one-sink
        # observability sentence, an artifact type in a workflow step matching a
        # separately-listed component) requires cross-section compositional
        # reasoning CHEAP-tier models don't reliably do — verified directly against
        # both tiers on a real multi-section architecture document before this
        # change (see ingest_patches prompt's dependency-extraction rules).
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=PatchSet,
            meter=meter,
            node_name="discovery.ingest",
        )
    except StructuredOutputError as exc:
        # State untouched — this is the documented failure branch (Doc 3 §3.2).
        logger.error("ingest failed for session %s: %s", state.get("session_id"), exc)
        return {
            "error": "I couldn't process that message reliably. Could you rephrase it?",
            "last_patch_results": [],
        }

    patch_set = resolve_dependency_open_questions_from_short_answer(
        state["model"],
        state.get("user_message", ""),
        response.parsed,
        previous_agent_message=state.get("previous_agent_message"),
    )
    patch_set = resolve_environment_open_questions_from_short_answer(
        state["model"], state.get("user_message", ""), patch_set, state.get("request_impact")
    )
    patch_set = resolve_sparse_intake_open_question_from_target_context_answer(
        state["model"], state.get("user_message", ""), state.get("request_impact"), patch_set
    )
    patch_set = draft_minimal_architecture_when_user_says_proceed(
        state["model"], state.get("request_impact"), patch_set
    )
    patch_set = capture_unpatched_factual_answer_as_assumption(
        state["model"], state.get("user_message", ""), state.get("request_impact"), patch_set
    )
    patch_set = auto_confirm_directly_stated_assumptions(state["model"], state.get("request_impact"), patch_set)
    return {"_patch_set": patch_set, "error": None}


def auto_confirm_directly_stated_assumptions(
    model: ArchitectureModel, request_impact: object, patch_set: PatchSet
) -> PatchSet:
    """Generalizes the auto-confirm pattern already used for target-context
    answers to every CURRENT_FACT turn (the classification for "the message
    describes/refines architecture facts" — i.e. the user directly telling us
    about their own system, not a target-state preference or a terse
    confirmation of something else). The ingest prompt separately routes
    genuine LLM guesses (like a role-based criticality default) around
    add_assumption entirely — narrated and self-standing, never a pending
    question — so by the time add_assumption is used on a CURRENT_FACT turn,
    it is almost always the LLM paraphrasing something the user just said
    back, not an open guess. Leaving that unconfirmed turns it into a
    recurring UNCONFIRMED_ASSUMPTION gap that asks the user to confirm their
    own direct statement, verbatim, on every later turn regardless of topic —
    exactly the "why is it asking the same thing again" complaint. Auto-
    confirming closes that gap the same turn it was opened.
    """

    if getattr(request_impact, "intent", None) != RequestIntent.CURRENT_FACT:
        return patch_set

    already_confirmed_ids = {
        patch.assumption_id for patch in patch_set.patches if isinstance(patch, ConfirmAssumptionPatch)
    }
    base_count = len(model.assumptions)
    confirms: list[ConfirmAssumptionPatch] = []
    seen_adds = 0
    for patch in patch_set.patches:
        if not isinstance(patch, AddAssumptionPatch):
            continue
        seen_adds += 1
        predicted_id = f"A{base_count + seen_adds}"
        if predicted_id not in already_confirmed_ids:
            confirms.append(ConfirmAssumptionPatch(assumption_id=predicted_id))

    if not confirms:
        return patch_set
    return PatchSet(patches=[*patch_set.patches, *confirms], narration=patch_set.narration)


def capture_unpatched_factual_answer_as_assumption(
    model: ArchitectureModel, user_message: str, request_impact: object, patch_set: PatchSet
) -> PatchSet:
    """Safety net for the most common cause of a repeated BASIC_APP_REQUIREMENTS
    question: the LLM narrates a fact (PII fields, async behavior, scale,
    compliance posture) — narration reads as if the answer was captured — but
    never emits a patch recording it. gap_analyzer only scans actual model
    data (components/dependencies/assumptions/resolved questions), never
    narration text, so an unpatched answer leaves the same gap looking
    unanswered and the identical question repeats next turn. If a substantive
    factual message produced literally no model-affecting patch, record it
    verbatim as an assumption rather than let it evaporate with the turn.
    """

    if getattr(request_impact, "intent", None) not in (RequestIntent.CURRENT_FACT, None):
        return patch_set
    if "?" in user_message or len(user_message.split()) < 6:
        return patch_set

    has_model_patch = any(
        isinstance(
            patch,
            (
                AddComponentPatch,
                UpdateComponentPatch,
                AddDependencyPatch,
                AddAssumptionPatch,
                ConfirmAssumptionPatch,
                ResolveOpenQuestionPatch,
            ),
        )
        for patch in patch_set.patches
    )
    if has_model_patch:
        return patch_set

    return PatchSet(
        patches=[
            *patch_set.patches,
            AddAssumptionPatch(
                text=f"User-provided detail not otherwise captured: {user_message.strip()}",
                related_component_ids=[component.id for component in model.components],
            ),
        ],
        narration=patch_set.narration,
    )


def draft_minimal_architecture_when_user_says_proceed(
    model: ArchitectureModel, request_impact: object, patch_set: PatchSet
) -> PatchSet:
    """Deterministic floor for "just give it" / "proceed with a draft" / "I don't
    have more details" commands.

    request_impact.should_mutate_source is True for PROCEED_WITH_ASSUMPTIONS
    specifically so the ingest prompt is free to draft standard components under
    assumptions instead of asking yet another clarifying question. But if the LLM
    still doesn't add anything — the same failure mode that produced an endless
    question loop for TARGET_PLANNING/TERSE_CONFIRMATION answers before this —
    this guarantees forward progress instead of leaving the user stuck on the
    exact question they just told the app to stop asking.
    """

    if getattr(request_impact, "intent", None) != RequestIntent.PROCEED_WITH_ASSUMPTIONS:
        return patch_set
    if len(model.components) > 1 or model.dependencies:
        return patch_set
    if any(isinstance(patch, AddComponentPatch) for patch in patch_set.patches):
        return patch_set

    additions: list = []
    app_id = model.components[0].id if model.components else "application"
    if not model.components:
        additions.append(
            AddComponentPatch(
                id=app_id,
                name="Application",
                workload_type=WorkloadType.WEB_SERVICE,
                description="Placeholder for the application described so far — split into real "
                "components once more detail is known.",
            )
        )
    additions.append(
        AddComponentPatch(
            id="primary_database",
            name="Primary Database",
            workload_type=WorkloadType.DATABASE,
            description="Assumed primary data store, not yet confirmed by the user.",
        )
    )
    additions.append(
        AddDependencyPatch(source_id=app_id, target_id="primary_database", kind=DependencyKind.DATA_READ)
    )
    additions.append(
        AddAssumptionPatch(
            text="Drafted a minimal placeholder architecture (application + primary database) at the "
            "user's explicit request to proceed without full details. Replace with real components as "
            "soon as they're known.",
            related_component_ids=[app_id, "primary_database"],
        )
    )
    narration = patch_set.narration or (
        "Drafted a minimal starting architecture since you asked to proceed. The components below are "
        "placeholders — refine them once more detail is available."
    )
    return PatchSet(patches=[*patch_set.patches, *additions], narration=narration)


_GREENFIELD_ASSUMPTION_MARKER = "greenfield build with no existing deployed system yet"


def resolve_sparse_intake_open_question_from_target_context_answer(
    model: ArchitectureModel, user_message: str, request_impact: object, patch_set: PatchSet
) -> PatchSet:
    """Deterministic backup for target/downtime/scale answers given while the
    model is still sparse (pre-detail, <=1 component, no dependencies).

    Two distinct problems, both stemming from the same root cause (the ingest
    prompt is told should_mutate_source=False for TARGET_PLANNING messages so it
    can't hallucinate source components out of target-state chatter, but that
    hint alone routinely makes the LLM withhold everything else too):
      1. If the model happens to already have a persisted OpenQuestion, it never
         gets resolve_open_question'd even though this message answers it.
      2. The greenfield/target facts this message gives are never recorded
         DURABLY on the model — so as soon as the conversation moves on to
         unrelated topics (workflows, dependencies), the current-hosting
         question resurfaces on every later turn, because
         _adapt_gaps_to_latest_user_message only ever looks at the LATEST
         message, with no memory that greenfield was already established.
    This records the target/greenfield summary as an auto-confirmed assumption
    (not just add_assumption, which would leave it sitting unconfirmed and
    itself become a new repeated "is that correct?" question) so later turns
    can recognize the fact is already settled via model_has_confirmed_greenfield_fact.
    """

    if getattr(request_impact, "intent", None) != RequestIntent.TARGET_PLANNING:
        return patch_set
    if len(model.components) > 1 or model.dependencies:
        return patch_set
    if model_has_confirmed_greenfield_fact(model):
        return patch_set

    summary = _summarize_target_context_answer(user_message)
    if summary is None:
        return patch_set

    already_resolved = {
        patch.question_id for patch in patch_set.patches if isinstance(patch, ResolveOpenQuestionPatch)
    }
    resolve_patches = [
        ResolveOpenQuestionPatch(question_id=question.id, resolution_text=summary)
        for question in model.open_questions
        if not question.resolved and question.id not in already_resolved
    ]

    # Predict the id add_assumption will be given so confirm_assumption in the
    # SAME patch set can reference it — apply_patch_set applies patches in
    # order against a running model snapshot, so this is valid as long as the
    # count accounts for every add_assumption patch already queued this turn
    # (the LLM's own output plus any earlier deterministic backup).
    pending_assumption_adds = sum(1 for patch in patch_set.patches if isinstance(patch, AddAssumptionPatch))
    predicted_id = f"A{len(model.assumptions) + pending_assumption_adds + 1}"
    assumption_patches = [
        AddAssumptionPatch(text=summary, related_component_ids=[component.id for component in model.components]),
        ConfirmAssumptionPatch(assumption_id=predicted_id),
    ]

    narration = (
        patch_set.narration
        or "Recorded the migration target context you gave; the source model itself stays unchanged."
    )
    return PatchSet(patches=[*patch_set.patches, *resolve_patches, *assumption_patches], narration=narration)


def model_has_confirmed_greenfield_fact(model: ArchitectureModel) -> bool:
    return any(
        assumption.resolved and _GREENFIELD_ASSUMPTION_MARKER in assumption.text
        for assumption in model.assumptions
    )


def _summarize_target_context_answer(user_message: str) -> str | None:
    text = " ".join(user_message.lower().split())
    facts: list[str] = []

    if any(term in text for term in ("aws", "amazon web services")):
        facts.append("target platform is AWS")
    elif "azure" in text:
        facts.append("target platform is Azure")
    elif any(term in text for term in ("gcp", "google cloud")):
        facts.append("target platform is GCP")

    if "zero downtime" in text or "no downtime" in text:
        facts.append("downtime tolerance is zero/near-zero")
    else:
        downtime_match = re.search(r"\d+\s*(?:hour|hr|min|minute)s?", text)
        if downtime_match and "downtime" in text:
            facts.append(f"downtime tolerance is about {downtime_match.group(0)}")

    if any(term in text for term in ("large scale", "large-scale", "high scale", "high traffic")):
        facts.append("expected scale is large")
    elif any(term in text for term in ("small scale", "small-scale", "low traffic")):
        facts.append("expected scale is small")

    if _looks_like_greenfield_build_context(user_message):
        facts.append("this is a greenfield build with no existing deployed system yet")

    if not facts:
        return None
    return "User-provided migration context: " + "; ".join(facts) + "."


def resolve_dependency_open_questions_from_short_answer(
    model: ArchitectureModel,
    user_message: str,
    patch_set: PatchSet,
    *,
    previous_agent_message: str | None = None,
) -> PatchSet:
    """Deterministic backup for terse answers like "standalone" or "no
    connections" to a dependency/open-question prompt. LLMs sometimes narrate
    those answers but forget the actual resolve_open_question patch, which makes
    the app ask the same question again."""

    normalized = " ".join(user_message.lower().split())
    affirmative_answer = normalized in {"yes", "y", "yeah", "yep", "correct", "yes correct", "that's correct"}
    if affirmative_answer and previous_agent_message:
        patch_set = _resolve_affirmed_dependency_hypothesis(model, previous_agent_message, patch_set)

    clear_standalone_answer = (
        normalized in {"no", "none", "nope", "standalone", "no connections", "no dependencies"}
        or "no connections" in normalized
        or "no dependencies" in normalized
        or "standalone" in normalized
        or "truly operate independently" in normalized
        or "do not interact" in normalized
    )
    if not clear_standalone_answer:
        return patch_set

    dependency_terms = ("dependenc", "connection", "interact", "standalone", "orphan", "captured")
    dependency_questions = [
        question
        for question in model.open_questions
        if not question.resolved and any(term in question.text.lower() for term in dependency_terms)
    ]
    if not dependency_questions:
        return patch_set

    already_resolved = {
        patch.question_id for patch in patch_set.patches if isinstance(patch, ResolveOpenQuestionPatch)
    }
    additions = [
        ResolveOpenQuestionPatch(
            question_id=question.id,
            resolution_text=f"User confirmed this dependency question has no missing connections: {user_message.strip()}",
        )
        for question in dependency_questions
        if question.id not in already_resolved
    ]
    if not additions:
        return patch_set

    narration = patch_set.narration
    if not narration:
        narration = "Confirmed there are no missing dependencies for the referenced standalone components."
    return PatchSet(patches=[*patch_set.patches, *additions], narration=narration)


def _resolve_affirmed_dependency_hypothesis(
    model: ArchitectureModel, previous_agent_message: str, patch_set: PatchSet
) -> PatchSet:
    previous = " ".join(previous_agent_message.lower().split())
    if not any(term in previous for term in ("expect", "confirm", "connection", "dependencies", "interact")):
        return patch_set

    existing = {
        (dependency.source_id, dependency.target_id, dependency.kind)
        for dependency in model.dependencies
    }
    already_in_patch_set = {
        (patch.source_id, patch.target_id, patch.kind)
        for patch in patch_set.patches
        if isinstance(patch, AddDependencyPatch)
    }

    def find_component(*needles: str) -> str | None:
        for component in model.components:
            text = f"{component.id} {component.name} {component.description} {component.technology or ''}".lower()
            if all(needle in text for needle in needles):
                return component.id
        return None

    desired: list[AddDependencyPatch] = []
    iot = find_component("iot") or find_component("telemetry")
    backend = find_component("fastapi") or find_component("backend") or find_component("api")
    database = find_component("postgres") or find_component("database") or find_component("db")

    if iot and backend and iot != backend and any(term in previous for term in ("iot", "telemetry", "mqtt")):
        desired.append(
            AddDependencyPatch(
                source_id=iot,
                target_id=backend,
                kind=DependencyKind.ASYNC_CALL,
                description="User confirmed the IoT telemetry path sends MQTT/events to the backend for processing.",
            )
        )

    if (
        backend
        and database
        and backend != database
        and any(term in previous for term in ("postgres", "database", "data storage", "store"))
    ):
        desired.extend(
            [
                AddDependencyPatch(
                    source_id=backend,
                    target_id=database,
                    kind=DependencyKind.DATA_WRITE,
                    description="User confirmed the backend writes processed/application data to the database.",
                ),
                AddDependencyPatch(
                    source_id=backend,
                    target_id=database,
                    kind=DependencyKind.DATA_READ,
                    description="User confirmed the backend reads application data from the database.",
                ),
            ]
        )

    missing = [
        patch
        for patch in desired
        if (patch.source_id, patch.target_id, patch.kind) not in existing
        and (patch.source_id, patch.target_id, patch.kind) not in already_in_patch_set
    ]
    if not missing:
        return patch_set

    narration = patch_set.narration or "Confirmed the previously suggested dependency flow and captured those connections."
    return PatchSet(patches=[*patch_set.patches, *missing], narration=narration)


def resolve_environment_open_questions_from_short_answer(
    model: ArchitectureModel, user_message: str, patch_set: PatchSet, request_impact: object = None
) -> PatchSet:
    """Deterministic backup for terse environment answers.

    Provider words such as "GCP" and "AWS" are not valid Environment enum values,
    so the LLM can acknowledge them in narration while leaving every component as
    environment=unknown. That makes the gap analyzer ask the same hosting question
    again. This helper converts those provider answers into the canonical
    environment enum and resolves the matching question/assumption.

    Must NOT fire on a TARGET_PLANNING message ("want to move to AWS"): a bare
    provider-name mention there describes the TARGET, not the current/source
    hosting, and this function has no way to tell those apart from the word
    alone. Firing anyway previously stamped a wrong "the current hosting
    environment is AWS" assumption onto a greenfield build whose real answer was
    "nothing exists yet" — resolve_sparse_intake_open_question_from_target_context_answer
    is the function that correctly handles target-context answers instead.
    """

    if getattr(request_impact, "intent", None) == RequestIntent.TARGET_PLANNING:
        return patch_set

    normalized = " ".join(user_message.lower().replace("-", " ").split())
    # "it's on a normal server right now, just want to move it to GCP" doesn't
    # trip the TARGET_PLANNING classifier (no recognized migration-context
    # term alongside the target phrase), so the guard above alone lets this
    # slip through and stamp the TARGET provider as the CURRENT environment —
    # the same class of bug the TARGET_PLANNING guard exists to prevent, just
    # for a phrasing that classifier doesn't catch. A provider name preceded
    # by "move/moving/migrate/migrating to" is describing where it's going,
    # never where it is now.
    if any(
        phrase in normalized
        for phrase in ("move to", "moving to", "migrate to", "migrating to", "want to move", "plan to move")
    ):
        return patch_set
    detected = _detect_environment_answer(normalized)
    if detected is None and normalized in {"yes", "y", "yeah", "yep", "correct", "yes correct", "that's correct"}:
        context_text = " ".join(
            [
                *(question.text for question in model.open_questions if not question.resolved),
                *(assumption.text for assumption in model.assumptions if not assumption.resolved),
            ]
        ).lower()
        detected = _detect_environment_answer(context_text)
        if detected is None and "cloud" in context_text:
            detected = (Environment.CLOUD, "cloud")
    if detected is None:
        return patch_set

    environment, provider_label = detected
    environment_terms = (
        "environment",
        "hosting",
        "hosted",
        "source",
        "current",
        "on prem",
        "on premises",
        "cloud",
        "hybrid",
        "gcp",
        "google cloud",
        "aws",
        "azure",
    )
    matching_questions = [
        question
        for question in model.open_questions
        if not question.resolved and any(term in question.text.lower().replace("-", " ") for term in environment_terms)
    ]
    matching_assumptions = [
        assumption
        for assumption in model.assumptions
        if assumption.raised_by == "llm"
        and not assumption.resolved
        and any(term in assumption.text.lower().replace("-", " ") for term in environment_terms)
    ]

    target_component_ids = {
        component_id
        for question in matching_questions
        for component_id in question.related_component_ids
    } | {
        component_id
        for assumption in matching_assumptions
        for component_id in assumption.related_component_ids
    }
    if not target_component_ids:
        target_component_ids = {component.id for component in model.components if component.environment == Environment.UNKNOWN}

    already_updated = {
        patch.id
        for patch in patch_set.patches
        if isinstance(patch, UpdateComponentPatch) and patch.environment is not None
    }
    update_patches = [
        UpdateComponentPatch(id=component.id, environment=environment)
        for component in model.components
        if component.id in target_component_ids
        and component.environment == Environment.UNKNOWN
        and component.id not in already_updated
    ]

    already_resolved = {
        patch.question_id for patch in patch_set.patches if isinstance(patch, ResolveOpenQuestionPatch)
    }
    resolve_patches = [
        ResolveOpenQuestionPatch(
            question_id=question.id,
            resolution_text=f"User confirmed the current hosting environment is {provider_label}.",
        )
        for question in matching_questions
        if question.id not in already_resolved
    ]

    already_confirmed = {
        patch.assumption_id for patch in patch_set.patches if isinstance(patch, ConfirmAssumptionPatch)
    }
    confirm_patches = [
        ConfirmAssumptionPatch(assumption_id=assumption.id)
        for assumption in matching_assumptions
        if assumption.id not in already_confirmed
    ]

    existing_user_environment_assumption = any(
        assumption.raised_by == "user"
        and "hosting environment" in assumption.text.lower()
        and provider_label.lower() in assumption.text.lower()
        for assumption in model.assumptions
    )
    has_environment_resolution = bool(resolve_patches or confirm_patches)
    assumption_patch = []
    if update_patches and not existing_user_environment_assumption and not has_environment_resolution:
        assumption_patch.append(
            AddAssumptionPatch(
                text=f"The current hosting environment is {provider_label}.",
                related_component_ids=[patch.id for patch in update_patches],
            )
        )

    additions = [*resolve_patches, *confirm_patches, *update_patches, *assumption_patch]
    if not additions:
        return patch_set

    narration = patch_set.narration or f"Confirmed the current hosting environment as {provider_label}."
    return PatchSet(patches=[*patch_set.patches, *additions], narration=narration)


def _detect_environment_answer(normalized: str) -> tuple[Environment, str] | None:
    if any(term in normalized for term in ("google cloud", "gcp", "google cloud platform")):
        return Environment.CLOUD, "GCP"
    if any(term in normalized for term in ("aws", "amazon web services")):
        return Environment.CLOUD, "AWS"
    if "azure" in normalized:
        return Environment.CLOUD, "Azure"
    if any(term in normalized for term in ("on premises", "on premise", "on prem", "onprem", "data center", "datacenter")):
        return Environment.ON_PREM, "on-premises"
    if "hybrid" in normalized:
        return Environment.HYBRID, "hybrid"
    if normalized in {"cloud", "in cloud", "on cloud", "cloud based", "cloud-based", "yes cloud"}:
        return Environment.CLOUD, "cloud"
    return None


def apply_patches_node(state: GraphState) -> dict:
    """Deterministic: validate, apply, audit, version++."""

    patch_set: PatchSet | None = state.get("_patch_set")
    if patch_set is None:
        return {"last_patch_results": []}

    new_model, results = apply_patch_set(
        state["model"],
        patch_set,
        require_structural_confirmation=state.get("stage") == Stage.REVIEW,
    )
    if state.get("stage") == Stage.DISCOVERY:
        new_model, inferred_results = _apply_obvious_helper_dependencies(new_model)
        results = [*results, *inferred_results]
    trace_node(
        node_name="discovery.apply_patches",
        session_id=state.get("session_id", ""),
        metadata={
            "patches_applied": sum(1 for r in results if r.outcome == "applied"),
            "patches_rejected": sum(1 for r in results if r.outcome == "rejected"),
            "model_version": new_model.version,
        },
    )
    return {
        "model": new_model,
        "last_patch_results": results,
        "narration": patch_set.narration,
        "_patch_set": None,
    }


def _apply_obvious_helper_dependencies(model: ArchitectureModel):
    """Add high-confidence helper/tool edges the LLM can miss in long docs."""

    by_id = {component.id: component for component in model.components}
    normalized = {component.id: f"{component.id} {component.name}".lower() for component in model.components}

    def find(*needles: str) -> str | None:
        return next(
            (
                component_id
                for component_id, text in normalized.items()
                if all(needle in text for needle in needles)
            ),
            None,
        )

    docx = next(
        (
            component_id
            for component_id, text in normalized.items()
            if ("docx" in text or "ppt" in text or "pptx" in text)
            and ("generation" in text or "export" in text or "library" in text)
        ),
        None,
    )
    cloudwatch = find("cloudwatch")
    s3 = find("s3")
    worker = find("worker")
    ai_service = find("ai", "service")
    backend = find("backend")
    diagram = find("diagram")

    desired: list[AddDependencyPatch] = []
    if docx:
        for source_id in [worker, ai_service]:
            if source_id and source_id in by_id:
                desired.append(
                    AddDependencyPatch(
                        source_id=source_id,
                        target_id=docx,
                        kind=DependencyKind.SYNC_CALL,
                        description="Document export flow invokes DOCX/PPT generation libraries.",
                    )
                )
        if s3 and s3 in by_id:
            desired.extend(
                [
                    AddDependencyPatch(
                        source_id=docx,
                        target_id=s3,
                        kind=DependencyKind.DATA_READ,
                        description="DOCX/PPT generation reads proposal context and generated content from S3.",
                    ),
                    AddDependencyPatch(
                        source_id=docx,
                        target_id=s3,
                        kind=DependencyKind.DATA_WRITE,
                        description="DOCX/PPT generation writes completed export files back to S3.",
                    ),
                ]
            )

    if cloudwatch:
        for source_id in [backend, worker, ai_service, docx, diagram]:
            if source_id and source_id in by_id:
                desired.append(
                    AddDependencyPatch(
                        source_id=source_id,
                        target_id=cloudwatch,
                        kind=DependencyKind.EVENT_PUBLISH,
                        description="Component emits operational logs and errors to CloudWatch.",
                    )
                )

    existing = {(d.source_id, d.target_id, d.kind) for d in model.dependencies}
    missing = [
        patch
        for patch in desired
        if (patch.source_id, patch.target_id, patch.kind) not in existing and patch.source_id != patch.target_id
    ]
    if not missing:
        return model, []

    patch_set = PatchSet(
        patches=missing,
        narration="Applied high-confidence helper dependencies inferred from the component roles.",
    )
    new_model, results = apply_patch_set(model, patch_set)
    for result in results:
        if result.outcome == PatchOutcome.APPLIED:
            result.reason = "high-confidence helper dependency inferred from discovered component roles"
    return new_model, results


def gap_analysis_node(state: GraphState) -> dict:
    """Deterministic: recompute unknowns from the UPDATED model."""

    gaps = top_gaps(state["model"], n=3)
    gaps = _adapt_gaps_to_latest_user_message(gaps, state)
    trace_node(
        node_name="discovery.gap_analysis",
        session_id=state.get("session_id", ""),
        metadata={"gap_count": len(gaps)},
    )
    return {"_gaps": gaps}


def _adapt_gaps_to_latest_user_message(gaps: list[Gap], state: GraphState) -> list[Gap]:
    """Prevent form-like repeated questions when the latest user message has
    changed the conversation shape.

    If the user says there is no current system yet and asks to build/move to a
    target cloud, the app should not ask where the current app is hosted. There
    is no current hosting. The useful next question is product architecture
    intake: workflows, data, auth, integrations, reporting, compliance, scale.

    Checks the model's own confirmed assumptions in addition to the latest
    message: greenfield status stated once (e.g. turn 2) must keep suppressing
    the current-hosting question on turn 5 even though turn 5's own message has
    moved on to unrelated topics (dependencies, workflows) and says nothing
    about greenfield itself — a per-message-only check re-asks "what's your
    current hosting?" the moment the conversation looks away from that fact.
    """

    is_greenfield = _looks_like_greenfield_build_context(
        state.get("user_message", ""),
        getattr(state.get("request_impact"), "intent", None),
    ) or model_has_confirmed_greenfield_fact(state["model"])
    if not is_greenfield:
        return gaps

    adapted: list[Gap] = []
    for gap in gaps:
        if gap.category == GapCategory.MISSING_ENVIRONMENT:
            continue
        if gap.category == GapCategory.SPARSE_ARCHITECTURE_CONTEXT:
            adapted.append(
                gap.model_copy(
                    update={
                        "description": (
                            "The user says there is no current deployed architecture and wants to build for "
                            "a target cloud at large scale. Do not ask where the current app is hosted. Ask "
                            "one consultant-style product architecture question covering the core workflows "
                            "or modules, data entities and retention, authentication and roles, integrations, "
                            "reporting/export needs, compliance/security constraints, and expected traffic or "
                            "data volume."
                        )
                    }
                )
            )
        else:
            adapted.append(gap)
    return adapted


def _looks_like_greenfield_build_context(user_message: str, intent: object = None) -> bool:
    text = " ".join(user_message.lower().split())
    no_current_signal = any(
        phrase in text
        for phrase in (
            "nothing for now",
            "nothing exists",
            "not built",
            "not build",
            "needed to build",
            "need to build",
            "building it",
            "build it",
            "no current",
            "from scratch",
            "greenfield",
        )
    )
    target_signal = any(
        phrase in text
        for phrase in ("aws", "azure", "gcp", "target", "move to", "migrate to", "cloud")
    )
    return no_current_signal and (target_signal or intent == RequestIntent.TARGET_PLANNING)


async def generate_questions_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: computed gaps -> contextual questions. Never invents its own gaps."""

    gaps = state.get("_gaps", [])
    if not gaps:
        return {
            "pending_questions": [],
            "stage": Stage.DISCOVERY,
            "_gaps": None,
        }

    prompt = get_prompt("generate_questions")
    user_prompt = (
        f"CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"COMPUTED GAPS (ask about these, and only these):\n{render_gaps_for_prompt(gaps)}"
    )

    try:
        # STRONG tier: proposing a plausible caller for an orphan component (rather
        # than asking a blank "does this connect to anything?") needs the same
        # cross-section reasoning over the injected model that ingestion needs —
        # this call is small (a handful of gaps, not a full document), so the
        # marginal cost of matching ingestion's tier here is low.
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=QuestionGenerationOutput,
            meter=meter,
            node_name="discovery.generate_questions",
        )
    except StructuredOutputError:
        # Degrade to the raw computed gap text rather than failing the turn — the
        # gaps are real either way, they just read less naturally.
        return {
            "pending_questions": [g.description for g in gaps],
            "stage": Stage.DISCOVERY,
            "_gaps": None,
        }

    return {
        "pending_questions": [q.text for q in response.parsed.questions],
        "narration": f"{state.get('narration', '')}\n\n{response.parsed.narration}".strip(),
        "stage": Stage.DISCOVERY,
        "_gaps": None,
    }
