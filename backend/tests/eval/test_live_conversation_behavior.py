"""Permanent live-conversation eval suite (technique #16, extended to the LLM
boundary itself): scripted multi-turn conversations, taken directly from
real director/lead test transcripts and live-reported bugs this project has
hit, run against the REAL discovery graph (real OpenAI calls) and asserted
against concrete, structural outcomes.

This is deliberately NOT part of the default fast suite — test_golden_fixture.py
already covers the deterministic code paths with a MockProvider, on every run,
for free. This suite exists for the failure mode that suite structurally
cannot catch: the model's own judgment drifting on a prompt change. Every
scenario here is a regression for a bug this project actually shipped and
then fixed — a change that breaks one of these has broken something a real
user hit, not a hypothetical.

Run explicitly: `pytest -m live_smoke tests/eval/test_live_conversation_behavior.py -v`
Costs real API calls and takes real wall-clock time — that's the trade for
actually exercising the LLM's behavior instead of a mock standing in for it.
"""

from __future__ import annotations

import pathlib

import pytest

from app.config import get_settings
from app.core.request_intelligence import classify_user_request
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import OpenAIProvider
from app.orchestration.graph import build_discovery_graph
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel, Environment

pytestmark = pytest.mark.live_smoke


def _real_openai_api_key() -> str:
    """tests/conftest.py deliberately sets a dummy OPENAI_API_KEY (via
    os.environ.setdefault) so an accidentally-unmocked LLM call fails loudly
    in the normal suite instead of silently hitting the real API. This suite
    is the one place that's supposed to hit the real API, so it reads the
    real key straight from .env rather than through get_settings(), which
    would resolve to that dummy value for the lifetime of the test process."""

    env_path = pathlib.Path(__file__).resolve().parents[3] / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"OPENAI_API_KEY not found in {env_path}")


async def _run_turns(turns: list[str]) -> tuple[ArchitectureModel, list[list[str]]]:
    """Drives the real discovery graph turn by turn, threading the resulting
    model (and a synthesized 'previous agent message') forward exactly the
    way the API layer does — see app/api/routers/sessions.py's post_message."""

    settings = get_settings()
    provider = OpenAIProvider(
        api_key=_real_openai_api_key(),
        cheap_model=settings.llm_cheap_model,
        strong_model=settings.llm_strong_model,
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=2_000_000)
    graph = build_discovery_graph(gateway, meter).compile()

    model = ArchitectureModel()
    thread = {"configurable": {"thread_id": "eval"}}
    previous_agent_message: str | None = None
    questions_by_turn: list[list[str]] = []

    for message in turns:
        impact = classify_user_request(message)
        state_in = {
            "session_id": "eval",
            "stage": Stage.DISCOVERY,
            "model": model,
            "user_message": message,
            "request_impact": impact,
            "previous_agent_message": previous_agent_message,
        }
        result = await graph.ainvoke(state_in, config=thread)
        model = result["model"]
        questions = result.get("pending_questions", [])
        questions_by_turn.append(questions)
        previous_agent_message = "\n".join(questions) or result.get("narration")

    return model, questions_by_turn


def _all_text(model: ArchitectureModel) -> str:
    parts = [a.text for a in model.assumptions] + [c.description or "" for c in model.components]
    return " ".join(parts).lower()


@pytest.mark.asyncio
async def test_gcp_synonym_answers_resolve_to_one_fact_not_five_repeats():
    """Regression: "gcp" -> "YES" -> "ON GCP" -> "IN GCP" were each treated as
    a fresh unresolved fact, repeating the hosting question five times."""

    model, questions_by_turn = await _run_turns(
        [
            "We have an IoT platform ingesting MQTT telemetry, a FastAPI backend, and a Postgres database.",
            "gcp",
            "YES",
            "ON GCP",
        ]
    )

    assert any(c.environment == Environment.CLOUD for c in model.components)
    last_questions = " ".join(questions_by_turn[-1]).lower()
    assert "hosting" not in last_questions and "on-premises" not in last_questions


@pytest.mark.asyncio
async def test_greenfield_target_answer_is_not_stamped_as_current_environment():
    """Regression: "nothing exists yet, want AWS" got recorded as 'the current
    hosting environment is AWS' — wrong for a system that doesn't exist yet."""

    model, _ = await _run_turns(
        [
            "My application tracks employee allocations.",
            "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs",
        ]
    )

    assert not any(c.environment == Environment.CLOUD for c in model.components)
    assert "greenfield" in _all_text(model) or "target platform is aws" in _all_text(model)


@pytest.mark.asyncio
async def test_architectural_pattern_name_is_not_added_as_a_fake_component():
    """Regression: "DOMA architecture via MQTT" became a literal component
    named 'DOMA Architecture' instead of describing an edge between real
    components."""

    model, _ = await _run_turns(
        [
            "have a simple system accepting user data and accepting booking of movie shows on gcp",
            "There is a payment gateway and a database, I dont know which one",
            "Interconnected and talk to each other using a DOMA architecture via MQTT",
        ]
    )

    for component in model.components:
        assert "doma" not in component.id.lower()
        assert "doma" not in component.name.lower()


@pytest.mark.asyncio
async def test_stated_facts_are_captured_and_never_asked_to_be_reconfirmed():
    """Regression: directly-stated facts (login/roles/email/scale) landed as
    unconfirmed assumptions, so the app asked the user to "confirm" their own
    statement verbatim on every subsequent turn regardless of topic."""

    model, questions_by_turn = await _run_turns(
        [
            "We have a simple todo list app with a web frontend and a database.",
            "yeah login's there, admin and normal users, sends an email after a task is created, roughly 20k users",
            "the frontend calls the backend api directly, nothing else to add there",
        ]
    )

    assert "20k" in _all_text(model) or "20,000" in _all_text(model) or "20000" in _all_text(model)
    confirmed_stated_facts = any(
        a.resolved and ("login" in a.text.lower() or "20k" in a.text.lower() or "admin" in a.text.lower())
        for a in model.assumptions
    )
    assert confirmed_stated_facts, [a.text for a in model.assumptions]

    last_questions = " ".join(questions_by_turn[-1]).lower()
    assert "confirm if" not in last_questions and "is it correct that" not in last_questions


@pytest.mark.asyncio
async def test_unrelated_capability_addition_is_discussed_not_silently_added():
    """A brand-new, unscoped capability (nothing in the model calls for it)
    must be challenged before it's added to the architecture — never blindly
    complied with."""

    model, _ = await _run_turns(
        [
            "We have a simple todo list app: a React frontend, a Node.js backend, and a Postgres database.",
            "also add a payment gateway",
        ]
    )

    added_payment_component = any("payment" in c.id.lower() or "payment" in c.name.lower() for c in model.components)
    has_open_question_about_it = any("payment" in q.text.lower() for q in model.open_questions)
    assert not added_payment_component or has_open_question_about_it, (
        "a payment gateway with no basis anywhere in the described app was added without being discussed first"
    )


@pytest.mark.asyncio
async def test_high_impact_replatform_is_discussed_not_silently_applied():
    """Replacing a component's core technology (FastAPI/Python -> Java/Spring)
    is a scope/risk decision — must be raised as a question, never silently
    applied as a plain update_component."""

    model, _ = await _run_turns(
        [
            "We have a FastAPI Python backend and a Postgres database.",
            "change the backend to Java Spring Boot",
        ]
    )

    backend = next((c for c in model.components if "backend" in c.id.lower()), None)
    assert backend is not None
    still_python_or_undecided = "java" not in (backend.technology or "").lower() and "java" not in backend.name.lower()
    has_open_question_about_it = any(
        "java" in q.text.lower() or "spring" in q.text.lower() for q in model.open_questions
    )
    assert still_python_or_undecided or has_open_question_about_it, (
        "the backend's core technology was silently replaced without being raised as a discussion first"
    )
