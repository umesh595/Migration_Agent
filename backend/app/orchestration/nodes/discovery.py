"""Discovery subgraph nodes. LLM nodes are marked; every other node is deterministic.

Flow (Doc 3 §3.1):
  ingest (LLM) -> apply_patches (code) -> gap_analysis (code) -> generate_questions (LLM) -> interrupt
"""

from __future__ import annotations

import json
import logging
import re

from langgraph.types import interrupt

from app.config import get_settings
from app.core.cloud_discovery import apply_cloud_discovery
from app.core.gap_analyzer import Gap, GapCategory, analyze_gaps
from app.core.patch_applier import apply_patch_set
from app.core.patch_validator import patches_requiring_confirmation
from app.core.request_intelligence import RequestIntent, render_request_impact_for_prompt
from app.integrations import aws_session_cache
from app.integrations.aws_provider import AWSProviderError, fetch_aws_inventory
from app.llm.base import ModelTier, ProviderQuotaExceededError, ProviderRequestError, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.prompts.registry import get_prompt
from app.llm.schemas import (
    IngestCompletenessCriticOutput,
    QuestionGenerationOutput,
    RequirementCoverageCriticOutput,
    RequirementCoverageOutput,
    RequirementCoverageVerdict,
)
from app.llm.state_injection import (
    render_gaps_for_prompt,
    render_generator_reasoning_for_prompt,
    render_model_for_prompt,
)
from app.observability.tracing import trace_node
from app.orchestration.state import GraphState, Stage
from app.schemas.architecture import ArchitectureModel, AssumptionStatus, DependencyKind, Environment, WorkloadType
from app.schemas.patches import (
    AddAssumptionPatch,
    AddComponentPatch,
    AddDependencyPatch,
    AddOpenQuestionPatch,
    ConfirmAssumptionPatch,
    PatchOutcome,
    PatchSet,
    ResolveOpenQuestionPatch,
    UpdateComponentPatch,
)

logger = logging.getLogger(__name__)

def _requirement_gap_priority(verdict: RequirementCoverageVerdict) -> int:
    """Adaptive Question Ranking (risk-weighted): priority is DERIVED from the
    verdict's own holistic risk_score (business risk, migration impact,
    dependency uncertainty, security/privacy impact, planning-blocker level —
    see RequirementCoverageVerdict.risk_score), not a flat per-category
    constant. A genuinely high-risk hedge on THIS system now competes for the
    next question slot on its actual merits instead of every requirement gap
    being interchangeable; a low-risk unknown can sink below a structural gap
    like ORPHAN_COMPONENT (80, gap_analyzer._PRIORITY) instead of always
    beating it. Bounded below OPEN_QUESTION/a CONTRADICTION open question
    (100) and just above MISSING_ENVIRONMENT (60) at the low end, so a
    contradiction or an explicit unresolved question always wins, and even a
    low-risk requirement gap still outranks pure form-filling gaps."""

    priority = 61 + round(verdict.risk_score * 0.36)  # risk=0 -> 61, risk=100 -> 97
    if verdict.status == "hedged_or_uncertain":
        # Already-raised uncertainty is worth nudging ahead of an equally-risky
        # but never-yet-discussed unknown — the user signaled it themselves.
        priority = min(priority + 3, 99)
    return priority


async def ingest_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: user text -> proposed patches. Closed-world: sees only the current model
    plus this message."""

    settings = get_settings()
    fast_turn = settings.discovery_fast_mode and len(state.get("user_message", "")) < settings.discovery_full_prompt_min_chars
    prompt = get_prompt("ingest_patches_fast" if fast_turn else "ingest_patches")
    impact = state.get("request_impact")
    impact_section = (
        f"DETERMINISTIC REQUEST CLASSIFICATION:\n{render_request_impact_for_prompt(impact)}\n\n"
        if impact is not None
        else ""
    )
    if fast_turn:
        # The canonical model carries durable history. For a short conversational
        # reply, the previous question is the only transcript context needed to
        # resolve a terse answer such as "yes" without resending the whole chat.
        user_prompt = (
            f"{impact_section}CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
            f"PREVIOUS AGENT MESSAGE:\n{state.get('previous_agent_message') or '(none)'}\n\n"
            f"USER MESSAGE:\n{state['user_message']}"
        )
    else:
        user_prompt = (
            f"{impact_section}CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
            f"USER MESSAGE HISTORY FOR THIS SESSION:\n{state.get('conversation_context') or '(none)'}\n\n"
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
            tier=ModelTier.CHEAP if fast_turn else ModelTier.STRONG,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=PatchSet,
            meter=meter,
            node_name="discovery.ingest",
        )
    except (ProviderRequestError, ProviderQuotaExceededError) as exc:
        logger.warning("discovery provider unavailable for session %s: %s", state.get("session_id"), exc)
        # A deadline exceeded is NOT the same situation as the provider being
        # genuinely down — the model may well have been about to answer. Say
        # so plainly rather than implying an outage; "unavailable" reads as
        # broken, which just makes a slow-but-healthy backend look worse than
        # it is and pushes the user toward an immediate retry that repeats the
        # exact same slow call from scratch.
        notice = (
            "That took longer than expected, so I stopped waiting rather than leave you without a response — "
            "the AI service itself is still up. Sending the same message again usually goes through; if it "
            "happens repeatedly, the message may just need to be shorter."
            if "exceeded its" in str(exc) and "response deadline" in str(exc)
            else "The AI service is unavailable right now. Please retry shortly."
        )
        return {
            "model": state["model"],
            "_patch_set": None,
            "_gaps": None,
            "pending_questions": [],
            "question_details": [],
            "last_patch_results": [],
            "narration": notice,
            "error": notice,
        }
    except StructuredOutputError as exc:
        # State untouched — this is the documented failure branch (Doc 3 §3.2).
        if getattr(impact, "intent", None) == RequestIntent.SPARSE_INTAKE:
            logger.info(
                "sparse intake produced no structured patches for session %s; continuing to intake questions",
                state.get("session_id"),
            )
            return {
                "_patch_set": PatchSet(patches=[], narration=""),
                "error": None,
                "last_patch_results": [],
            }
        logger.error("ingest failed for session %s: %s", state.get("session_id"), exc)
        fallback_model = _capture_raw_message_after_total_ingest_failure(state["model"], state.get("user_message", ""))
        degradation_notice = (
            "I couldn't fully process that message through the usual extraction step, so I've recorded "
            "what you said as a raw note for now — you may want to restate the key facts more explicitly "
            "once this is working normally again."
        )
        return {
            "model": fallback_model,
            # Set on BOTH keys deliberately: error is what a genuinely stuck
            # turn shows (see apply_patches_node), but generate_questions_node
            # clears error back to None once it produces a usable follow-up —
            # reasonably, since downstream nodes may still recover something
            # useful (as the requirement-coverage classifier does, reading the
            # raw note this same fallback just captured). narration is never
            # cleared, only appended to by later nodes, so it's the one that
            # reliably survives to reach the user either way.
            "error": degradation_notice,
            "narration": degradation_notice,
            "last_patch_results": [],
            "_patch_set": None,
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
    if not fast_turn:
        patch_set = await critique_ingest_completeness(
            state["model"],
            state.get("user_message", ""),
            patch_set,
            gateway,
            meter,
            session_id=state.get("session_id", ""),
            generator_reasoning=response.reasoning,
        )
    patch_set = auto_confirm_directly_stated_assumptions(state["model"], state.get("request_impact"), patch_set)
    return {"_patch_set": patch_set, "error": None}


def _capture_raw_message_after_total_ingest_failure(model: ArchitectureModel, user_message: str) -> ArchitectureModel:
    """Deterministic last resort for when the ingest LLM call fails outright —
    e.g. a provider-side request-size or quota error, not a malformed
    response. "Never persist partial output" (Doc 3 §3.2) protects against
    trusting a bad LLM response; it says nothing about a message the LLM
    never got to see at all. Leaving the model silently untouched here means
    gap analysis can never tell "the user hasn't answered yet" apart from
    "extraction is broken" — both look like zero new detail, so the exact
    same question repeats forever with no sign anything is wrong. Recording
    the user's own words verbatim, clearly labeled as unparsed and low-
    confidence, breaks that loop and keeps the information from being lost
    outright, at the cost of it needing a manual look later.
    """

    if len(user_message.split()) < 6:
        return model

    patch_set = PatchSet(
        patches=[
            AddAssumptionPatch(
                text=f'UNPARSED — automatic extraction failed for this message; recorded as-is for manual '
                f'review: "{user_message.strip()}"',
                related_component_ids=[component.id for component in model.components],
                confidence="unsure",
                source="deterministic fallback: ingest LLM call failed outright for this message",
            )
        ],
        narration="",
    )
    new_model, _ = apply_patch_set(model, patch_set)
    return new_model


async def critique_ingest_completeness(
    model: ArchitectureModel,
    user_message: str,
    patch_set: PatchSet,
    gateway: LLMGateway,
    meter: SessionTokenMeter,
    *,
    session_id: str = "",
    generator_reasoning: str | None = None,
) -> PatchSet:
    """Second-opinion pass modeled on the review stage's rules->critic->judge
    pattern (technique #8), applied to discovery ingestion instead of plan
    review. ingest_node's own patch proposal (plus every deterministic backup
    above) is otherwise trusted once and never independently checked. This is
    the general-purpose backstop for the whole CLASS of bug the deterministic
    backups above patch one specific instance of at a time: a fact the LLM
    narrates but never turns into a patch, which silently repeats the same
    question next turn because gap analysis only ever reads model data, never
    narration text.

    Skipped for trivial messages (nothing substantive to possibly miss) to
    avoid spending a full STRONG-tier call auditing "yes"/"gcp"-style replies
    that are already handled by the dedicated short-answer backups above.
    """

    if len(user_message.split()) < 6:
        return patch_set

    prompt = get_prompt("ingest_completeness_critic")
    user_prompt = (
        f"ARCHITECTURE MODEL BEFORE THIS TURN:\n{render_model_for_prompt(model)}\n\n"
        f"USER MESSAGE:\n{user_message}\n\n"
        f"PROPOSED PATCHES (about to be applied):\n{patch_set.model_dump_json(indent=2)}\n\n"
        "NARRATION ABOUT TO BE SHOWN (reference only — narration is NOT captured in the model, "
        "only the patches above are):\n"
        f"{patch_set.narration}"
        f"{render_generator_reasoning_for_prompt(generator_reasoning)}"
    )

    try:
        # CHEAP tier, not STRONG: this is a bounded verification pass over
        # structured input (does the patch set cover what the message said?),
        # not open-ended synthesis — the same reliability/cost tradeoff as
        # requirement_coverage_critic below. Keeps the primary reasoning calls
        # (ingest itself, the requirement-coverage generator, question
        # generation) on the strong tier for maximum intelligence while
        # cutting real per-turn latency on the two check-only passes.
        response = await gateway.complete(
            tier=ModelTier.CHEAP,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=IngestCompletenessCriticOutput,
            meter=meter,
            node_name="discovery.ingest_completeness_critic",
        )
    except StructuredOutputError as exc:
        # Additive-only signal — a failure here must not block the turn.
        logger.warning("ingest completeness critic failed, continuing without it: %s", exc)
        return patch_set

    verdict = response.parsed
    trace_node(
        node_name="discovery.ingest_completeness_critic",
        session_id=session_id,
        metadata={
            "fully_captured": verdict.fully_captured,
            "missed_count": len(verdict.missed_facts),
            "invented_count": len(verdict.invented_facts),
            "contradiction_count": len(verdict.contradictions),
        },
    )
    if verdict.invented_facts:
        logger.warning(
            "ingest completeness critic flagged possibly invented facts for session %s: %s",
            session_id,
            verdict.invented_facts,
        )
    if verdict.contradictions:
        logger.warning(
            "ingest completeness critic flagged %d contradiction(s) for session %s: %s",
            len(verdict.contradictions),
            session_id,
            [c.description for c in verdict.contradictions],
        )

    new_patches: list = []
    if verdict.missed_facts:
        summary = "Additional detail captured by completeness review: " + "; ".join(verdict.missed_facts) + "."
        new_patches.append(
            AddAssumptionPatch(
                text=summary,
                related_component_ids=[component.id for component in model.components],
                source=f"ingest completeness critic caught this message text unpatched: {user_message.strip()!r}",
            )
        )

    # Contradiction Detector: a real logical conflict is not something to
    # silently pick a side on (that would be the LLM guessing which of two
    # things the user "really" meant) or to bury only in narration (discarded
    # after this turn, invisible to gap analysis) — it becomes a real,
    # tracked open question via the SAME mechanism the discuss-before-adding
    # flow already uses, so the next turn's gap analysis surfaces it and it
    # gets asked about like any other unresolved unknown.
    for contradiction in verdict.contradictions:
        text = contradiction.description
        if contradiction.existing_fact:
            text += f' (previously: "{contradiction.existing_fact}"; now: "{contradiction.new_statement}")'
        new_patches.append(AddOpenQuestionPatch(text=f"CONTRADICTION — {text}"))

    if not new_patches:
        return patch_set
    return PatchSet(patches=[*patch_set.patches, *new_patches], narration=patch_set.narration)


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
                source="deterministic fallback: verbatim user message text, ingest LLM produced no patch for it",
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
        AddDependencyPatch(
            source_id=app_id,
            target_id="primary_database",
            kind=DependencyKind.DATA_READ,
            source="deterministic default: minimal placeholder architecture, no real dependency stated yet",
        )
    )
    additions.append(
        AddAssumptionPatch(
            text="Drafted a minimal placeholder architecture (application + primary database) at the "
            "user's explicit request to proceed without full details. Replace with real components as "
            "soon as they're known.",
            related_component_ids=[app_id, "primary_database"],
            source="deterministic default: user explicitly asked to proceed without providing architecture details",
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
        AddAssumptionPatch(
            text=summary,
            related_component_ids=[component.id for component in model.components],
            source=f"deterministic fallback: summarized from a target-planning message: {user_message.strip()!r}",
        ),
        ConfirmAssumptionPatch(assumption_id=predicted_id),
    ]

    narration = (
        patch_set.narration
        or "Recorded the migration target context you gave; the source model itself stays unchanged."
    )
    return PatchSet(patches=[*patch_set.patches, *resolve_patches, *assumption_patches], narration=narration)


def model_has_confirmed_greenfield_fact(model: ArchitectureModel) -> bool:
    return any(
        assumption.status == AssumptionStatus.CONFIRMED and _GREENFIELD_ASSUMPTION_MARKER in assumption.text
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
                *(
                    assumption.text
                    for assumption in model.assumptions
                    if assumption.status == AssumptionStatus.OPEN
                ),
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
        and assumption.status == AssumptionStatus.OPEN
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
                source=f"deterministic fallback: short-answer environment detection matched {provider_label!r} "
                f"in the user's reply: {user_message.strip()!r}",
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


def _upgrade_user_technical_level(model: ArchitectureModel, signal: str) -> ArchitectureModel:
    """One-way upgrade only (see ArchitectureModel.user_technical_level's own
    docstring for why): once "technical" is detected it never reverts, and a
    single "unknown" turn (e.g. a terse "yes") never downgrades an
    already-established level. Mirrors the spec's "mixed/uncertain defaults to
    non-technical, since asking an unanswerable technical question is more
    costly than briefly under-asking a technical user" — non_technical is a
    real, sticky floor too, only overridden by a later technical signal."""

    current = model.user_technical_level
    if signal == "unknown" or signal == current:
        return model
    if current == "technical":
        return model
    return model.model_copy(update={"user_technical_level": signal})


def apply_patches_node(state: GraphState) -> dict:
    """Deterministic: validate, apply, audit, version++."""

    patch_set: PatchSet | None = state.get("_patch_set")
    if patch_set is None:
        # Preserve whatever error ingest_node may have just set. This branch
        # runs both when ingest legitimately had nothing to patch (error is
        # already None) and when ingest FAILED outright (error is a real
        # message) — blindly overwriting it here silently erased a genuine
        # ingest failure, making it look like an ordinary error-free turn
        # while the model quietly stayed untouched and the same question
        # repeated with no indication anything had gone wrong.
        return {"last_patch_results": [], "error": state.get("error")}

    require_structural_confirmation = state.get("stage") == Stage.REVIEW
    # Mirrors apply_patch_set's own confirmation_reason computation exactly — a
    # patch set that resolves an open question in the SAME turn has already been
    # explicitly discussed, so it must not also trigger a redundant interrupt.
    already_confirmed_this_turn = any(isinstance(p, ResolveOpenQuestionPatch) for p in patch_set.patches)
    pending = patches_requiring_confirmation(
        state["model"],
        patch_set.patches,
        require_structural_confirmation=require_structural_confirmation,
        allow_high_impact_changes=already_confirmed_this_turn,
    )
    bypass_confirmation = False
    if pending:
        # AG-UI human-in-the-loop pause (see app/api/routers/ag_ui.py): the first
        # time this node runs for this turn, interrupt() raises and the graph run
        # ends here — the frontend renders an approve/edit/reject card from the
        # payload below. On resume, LangGraph re-runs this node from the top
        # (everything above is a pure re-computation of the same `pending` list,
        # so that's safe) and interrupt() returns the recorded decision instead
        # of pausing again.
        decision = interrupt(
            {
                # "reason" and "message" are ag-ui-langgraph's own field names
                # (see lg_interrupt_to_agui) — promoted to top-level fields on
                # the AG-UI Interrupt the frontend receives. Everything else
                # (patches) is still delivered, just nested under
                # interrupt.metadata.langgraph.raw on the frontend rather than
                # a top-level field, since AG-UI's Interrupt schema doesn't
                # define an app-specific payload field.
                "reason": "structural_confirmation",
                "message": (
                    "These architecture changes need your explicit confirmation before "
                    "they're applied — reply to approve, edit, or reject them."
                ),
                "patches": [
                    {"op": str(p.op), "summary": patch_set.narration, "detail": p.model_dump(mode="json")}
                    for p in pending
                ],
            }
        )
        bypass_confirmation = bool(isinstance(decision, dict) and decision.get("approved"))

    new_model, results = apply_patch_set(
        state["model"],
        patch_set,
        require_structural_confirmation=require_structural_confirmation,
        bypass_confirmation=bypass_confirmation,
    )
    new_model = _upgrade_user_technical_level(new_model, patch_set.user_technical_signal)
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
        "error": None,
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


async def _apply_cloud_discovery_if_connected(model: ArchitectureModel, session_id: str) -> tuple[ArchitectureModel, list[str]]:
    """Cloud-discovery-first (spec §2): called only when this session has an
    active AWS connection (app.integrations.aws_session_cache). Reuses a
    cached inventory scan within its TTL rather than re-hitting AWS every
    single turn — the fetch is a real network call, not free, and the
    inventory does not change turn-to-turn in practice."""

    inventory = aws_session_cache.get_cached_inventory(session_id)
    if inventory is None:
        credentials = aws_session_cache.get_credentials(session_id)
        if credentials is None:
            return model, []
        try:
            inventory = await fetch_aws_inventory(credentials)
        except AWSProviderError as exc:
            # A live AWS call can fail transiently (rate limit, network) —
            # this must never block or fail the turn, same contract as the
            # ingest completeness critic's own additive-only failure handling.
            logger.warning("cloud discovery AWS fetch failed for session %s: %s", session_id, exc)
            return model, []
        aws_session_cache.store_inventory(session_id, inventory)

    return apply_cloud_discovery(model, inventory)


async def gap_analysis_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """Recomputes unknowns from the UPDATED model. Open questions, orphan
    components, missing environment/criticality, and unconfirmed assumptions
    stay pure deterministic code (technique #4) — those are structural facts
    about the model that never need judgment. Which basic application
    requirement areas matter for THIS system, and whether each is covered, is
    inherently semantic and domain-dependent, so that piece is a separate LLM
    classification call merged in alongside the deterministic gaps, not a
    fixed keyword-matched category list.
    """

    model = state["model"]
    session_id = state.get("session_id", "")
    cloud_scan_notes: list[str] = []
    if aws_session_cache.is_connected(session_id):
        model, cloud_scan_notes = await _apply_cloud_discovery_if_connected(model, session_id)

    deterministic_gaps = analyze_gaps(model)
    settings = get_settings()
    # Same length-based gate ingest_node uses for its own fast path (never a
    # blanket "fast mode always skips this" switch) — a short reply stays fast,
    # but a substantial message still gets the full requirement-coverage
    # generator/critic pass instead of silently losing that entire dimension of
    # analysis regardless of how much the user actually wrote.
    fast_turn = settings.discovery_fast_mode and len(state.get("user_message", "")) < settings.discovery_full_prompt_min_chars
    if fast_turn:
        requirement_gaps = []
    else:
        requirement_gaps, model = await assess_dynamic_requirement_coverage(
            model,
            gateway,
            meter,
            user_message=state.get("user_message", ""),
            conversation_context=state.get("conversation_context") or "",
            session_id=state.get("session_id", ""),
        )
    # 5, not 3: requirement-coverage gaps are now one-per-topic (see
    # assess_dynamic_requirement_coverage) rather than one merged bundle, so a
    # system with several genuinely distinct high-impact unknowns needs more
    # than 3 total slots to avoid re-merging them back together downstream —
    # generate_questions_node's own significance filter is still what keeps
    # the actual question count from ballooning, this cap just stops the
    # gap list itself from growing unbounded.
    gaps = sorted([*deterministic_gaps, *requirement_gaps], key=lambda g: g.priority, reverse=True)[:5]
    gaps = _adapt_gaps_to_latest_user_message(gaps, state)
    trace_node(
        node_name="discovery.gap_analysis",
        session_id=session_id,
        metadata={"gap_count": len(gaps), "cloud_scan_matches": len(cloud_scan_notes)},
    )
    result: dict = {"_gaps": gaps, "model": model, "error": None}
    if cloud_scan_notes:
        # A lightweight recognition check, not a blocking confirmation (spec
        # §2) — this is why it's a narration append, never an open_question or
        # a gap: the facts are already status=CONFIRMED assumptions by the time
        # this runs (see apply_cloud_discovery), so there is nothing left to
        # confirm, only something worth telling the user happened.
        note = "Cross-referenced your connected AWS account and filled in automatically: " + "; ".join(
            cloud_scan_notes
        ) + ". Let me know if any of this looks wrong or out of date."
        result["narration"] = f"{state.get('narration', '')}\n\n{note}".strip()
    return result


async def assess_dynamic_requirement_coverage(
    model: ArchitectureModel,
    gateway: LLMGateway,
    meter: SessionTokenMeter,
    *,
    user_message: str = "",
    conversation_context: str = "",
    session_id: str = "",
) -> tuple[list[Gap], ArchitectureModel]:
    """Replaces a fixed keyword-matched requirement checklist with domain-aware
    LLM judgment: what requirement areas actually matter for THIS system, and
    is each one covered, explicitly not applicable, hedged/uncertain, still
    unknown, or already asked about once and due to be escalated instead of
    repeated? A keyword scan can't tell "we removed SSO" from "we have SSO",
    can't handle paraphrase, and can only ever check categories a human
    anticipated in advance — this call is free to propose categories specific
    to this system's domain a fixed list never would (e.g. "seat locking
    during checkout" for a booking system).

    A generator/critic pair (technique #8), the same pattern already used for
    ingestion: the generator's own single-shot judgment can mark a hedge
    ("no idea how seat locking works, maybe just a db transaction") as
    "covered" — a plain db transaction doesn't actually prevent double-
    booking — and a system class can need a category (double-booking
    prevention, payment idempotency) the generator simply didn't think to
    ask about. The critic re-reads the same conversation independently and
    corrects both failure modes, plus a third: a hedge that's already been
    asked about once and would otherwise repeat the identical question
    forever, since "still uncertain" and "never asked" look the same to a
    single-shot classifier with no sense of its own history. Escalating a
    repeated hedge into a durably-recorded, clearly-labeled risk assumption
    (returned as part of the model) is what actually stops the loop — the
    caller must persist the returned model, not just the gaps.

    Skipped while the model is still sparse (no components, no assumptions)
    — that case is the deterministic SPARSE_ARCHITECTURE_CONTEXT gap's job,
    asking one compact intake question rather than a per-category breakdown.
    """

    if not model.components and not model.assumptions:
        return [], model

    prompt = get_prompt("assess_requirement_coverage")
    user_prompt = (
        f"CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(model)}\n\n"
        f"USER MESSAGE HISTORY FOR THIS SESSION:\n{conversation_context or user_message or '(none)'}\n\n"
        "LATEST USER MESSAGE (check its exact wording for hedges too, in case something from this turn "
        f"hasn't been written into the model yet):\n{user_message or '(none)'}"
    )

    try:
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=RequirementCoverageOutput,
            meter=meter,
            node_name="discovery.requirement_coverage",
        )
    except StructuredOutputError as exc:
        logger.warning("requirement coverage assessment failed, skipping this turn: %s", exc)
        return [], model

    verdicts = await _critique_requirement_coverage(
        model,
        user_message,
        response.parsed.requirements,
        gateway,
        meter,
        conversation_context=conversation_context,
        session_id=session_id,
        generator_reasoning=response.reasoning,
    )
    trace_node(
        node_name="discovery.requirement_coverage",
        session_id=session_id,
        metadata={
            "category_count": len(verdicts),
            "unknown_count": sum(1 for v in verdicts if v.status == "unknown"),
            "hedged_count": sum(1 for v in verdicts if v.status == "hedged_or_uncertain"),
            "escalated_count": sum(1 for v in verdicts if v.status == "escalate_as_risk"),
        },
    )

    model = _record_escalated_risks(model, [v for v in verdicts if v.status == "escalate_as_risk"])

    concerning = [v for v in verdicts if v.status in ("unknown", "hedged_or_uncertain")]
    if not concerning:
        return [], model

    # One Gap PER CONCERNING TOPIC, never one merged gap bundling several
    # (technique: "one question per gap, never merged across categories" —
    # the previous version built a single BASIC_APP_REQUIREMENTS gap whose
    # description concatenated every concerning category into one string,
    # which is exactly what produced a wall-of-text single question covering
    # access control, payments, compliance, and volume all at once). Priority
    # is impact-weighted per topic (a high-impact hedge outranks a merely
    # unknown, low-stakes one) rather than a single flat category constant —
    # a genuinely high-impact + still-hedged fact should compete for a
    # question slot on its own merits, not be hidden inside a bundle.
    gaps: list[Gap] = []
    for verdict in concerning:
        is_high_impact_hedge = verdict.high_impact and verdict.status == "hedged_or_uncertain"
        if is_high_impact_hedge:
            description = (
                f"IMPORTANT — \"{verdict.category}\" was answered with a hedge, but getting it wrong would "
                f"cause a real production problem for this system"
                + (f' (you said: "{verdict.evidence}")' if verdict.evidence else "")
                + " — ask for a confident, concrete answer rather than accepting the hedge."
            )
        else:
            description = (
                f"\"{verdict.category}\" is still unknown for this specific system. The user can answer with "
                "details or explicitly say none/not applicable."
            )
        gaps.append(
            Gap(
                category=GapCategory.BASIC_APP_REQUIREMENTS,
                description=description,
                related_component_ids=[component.id for component in model.components],
                priority=_requirement_gap_priority(verdict),
            )
        )
    return gaps, model


def _record_escalated_risks(model: ArchitectureModel, escalated: list[RequirementCoverageVerdict]) -> ArchitectureModel:
    """Converts a repeated hedge into a durable, auto-confirmed risk assumption
    instead of another identical question. Applied directly (bypassing the
    LLM patch-proposal pipeline, the same pattern _apply_obvious_helper_dependencies
    already uses) since this is code, not the LLM, deciding the escalation —
    the LLM's role was already spent producing the verdict and the
    recommended_mitigation text in assess_requirement_coverage's own call.
    """

    if not escalated:
        return model

    additions: list = []
    for verdict in escalated:
        text = f"FLAGGED RISK — unconfirmed by user: {verdict.category}."
        if verdict.evidence:
            text += f' User said: "{verdict.evidence}".'
        if verdict.recommended_mitigation:
            text += f" Recommendation: {verdict.recommended_mitigation}"
        additions.append(
            AddAssumptionPatch(
                text=text,
                related_component_ids=[component.id for component in model.components],
                source=f"requirement coverage critic escalated a repeated hedge on {verdict.category!r} "
                "without a more confident answer across turns",
            )
        )

    base_count = len(model.assumptions)
    confirms = [ConfirmAssumptionPatch(assumption_id=f"A{base_count + i + 1}") for i in range(len(additions))]
    patch_set = PatchSet(patches=[*additions, *confirms], narration="")
    new_model, _ = apply_patch_set(model, patch_set)
    return new_model


async def _critique_requirement_coverage(
    model: ArchitectureModel,
    user_message: str,
    verdicts: list[RequirementCoverageVerdict],
    gateway: LLMGateway,
    meter: SessionTokenMeter,
    *,
    conversation_context: str = "",
    session_id: str = "",
    generator_reasoning: str | None = None,
) -> list[RequirementCoverageVerdict]:
    """The critic half of the generator/critic pair — see
    assess_dynamic_requirement_coverage's docstring. Additive-only in effect
    (it only ever changes what a gap looks like, never mutates the model
    directly), so a failure here degrades gracefully to the generator's own
    verdicts rather than blocking the turn.
    """

    prompt = get_prompt("requirement_coverage_critic")
    user_prompt = (
        f"CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(model)}\n\n"
        f"USER MESSAGE HISTORY FOR THIS SESSION:\n{conversation_context or user_message or '(none)'}\n\n"
        f"LATEST USER MESSAGE:\n{user_message or '(none)'}\n\n"
        f"GENERATOR'S VERDICTS TO REVIEW:\n{json.dumps([v.model_dump() for v in verdicts], indent=2)}"
        f"{render_generator_reasoning_for_prompt(generator_reasoning)}"
    )

    try:
        # CHEAP tier — see ingest_completeness_critic's identical comment: a
        # bounded second-opinion check over the generator's own structured
        # verdicts, not open-ended synthesis, with automatic escalation to
        # STRONG built into LLMGateway.complete() if it ever fails.
        response = await gateway.complete(
            tier=ModelTier.CHEAP,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=RequirementCoverageCriticOutput,
            meter=meter,
            node_name="discovery.requirement_coverage_critic",
        )
    except StructuredOutputError as exc:
        logger.warning(
            "requirement coverage critic failed for session %s, using generator's verdicts unchanged: %s",
            session_id,
            exc,
        )
        return verdicts

    if response.parsed.corrections_made:
        logger.info(
            "requirement coverage critic corrected verdicts for session %s: %s",
            session_id,
            response.parsed.corrections_made,
        )
    return response.parsed.corrected_requirements


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
        # Never end a turn with silence (technique: explicit terminal handling,
        # not an implicit dead end) — a user who just answered a real question
        # and gets an empty response back cannot tell "discovery is genuinely
        # done" from "the app is broken." This is a deterministic fallback, not
        # an LLM call: it fires only once real gaps have run out, so there is
        # nothing left to reason about dynamically — the message is structural
        # ("you're done, here's the next step"), not domain content.
        closing = (
            "I believe I now have enough of a picture of your architecture to move "
            "forward — I don't have any more open questions right now. Take a look at "
            "the model summary above; if it looks complete and accurate, accept it to "
            "move into migration planning. If anything's missing, wrong, or you think "
            "of more detail, just tell me and I'll fold it in."
        )
        return {
            "pending_questions": [],
            "question_details": [],
            "narration": f"{state.get('narration', '')}\n\n{closing}".strip(),
            "stage": Stage.DISCOVERY,
            "_gaps": None,
            "error": None,
        }

    settings = get_settings()
    # Same "short message -> smaller prompt, cheaper tier" lever ingest_node uses,
    # applied here too — a condensed PROMPT and a faster tier are a legitimate
    # latency win because the reasoning is still real and dynamic. A hardcoded
    # if/category->fixed-string shortcut used to live here instead; it was faster
    # only because it skipped reasoning entirely, which is exactly the tradeoff
    # this project has repeatedly rejected (see generate_questions' own anti-echo
    # rule — a fixed string per category is the same class of bug as echoing a
    # gap's raw description, just baked into Python instead of a bad model
    # output). Never bring that shortcut back; make the real call fast instead.
    fast_turn = settings.discovery_fast_mode and len(state.get("user_message", "")) < settings.discovery_full_prompt_min_chars
    prompt = get_prompt("generate_questions_fast" if fast_turn else "generate_questions")
    user_prompt = (
        f"CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"LATEST USER MESSAGE:\n{state.get('user_message', '')}\n\n"
        f"PREVIOUS AGENT MESSAGE, IF THE USER IS ANSWERING IT:\n{state.get('previous_agent_message') or '(none)'}\n\n"
        f"COMPUTED GAPS (ask about these, and only these):\n{render_gaps_for_prompt(gaps)}"
    )

    try:
        # STRONG tier normally: proposing a plausible caller for an orphan
        # component (rather than asking a blank "does this connect to anything?")
        # needs the same cross-section reasoning over the injected model that
        # ingestion needs. On the fast path (short message, same gate ingest_node
        # uses), CHEAP tier + the condensed prompt above trade a little of that
        # depth for real latency — never for a fixed non-answer.
        response = await gateway.complete(
            tier=ModelTier.CHEAP if fast_turn else ModelTier.STRONG,
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
            "question_details": [],
            "stage": Stage.DISCOVERY,
            "_gaps": None,
            "error": None,
        }

    return {
        "pending_questions": [q.text for q in response.parsed.questions],
        "question_details": response.parsed.questions,
        "narration": f"{state.get('narration', '')}\n\n{response.parsed.narration}".strip(),
        "stage": Stage.DISCOVERY,
        "_gaps": None,
        "error": None,
    }


