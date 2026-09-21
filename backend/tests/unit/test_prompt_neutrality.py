"""Guards the property that question content is DERIVED from the user's own context,
never recited from a list baked into a prompt.

Why this exists: a tester described one kind of system and was asked about capabilities
belonging to a different kind of system entirely — the tool asking about carrots when
they came in for onions. The cause was fixed topic inventories in the prompts and in
gap descriptions ("... authentication/roles, payments if relevant, integrations,
notifications, reporting/exports ..."). A model handed that list asks the list,
regardless of what the user actually described.

The asymmetry that makes this worth a merge-blocking test: an area we fail to ask about
resurfaces later as an unknown, a documented assumption, or a flagged risk — recoverable.
An out-of-context question is not recoverable in the same way, because it tells the user
the tool wasn't listening, which discredits the answers it got right.
"""

import pytest

from app.llm.prompts.registry import REGISTRY

# Prompts whose output reaches the user as a question or as a category quoted inside
# one. These must contain no subject matter of their own.
USER_FACING_QUESTION_PROMPTS = [
    "generate_questions",
    "generate_questions_fast",
    "assess_requirement_coverage",
    "requirement_coverage_critic",
]

# Domain nouns that have no business seeding question content. This is not a style
# rule — any one of these appearing in a user-facing question prompt is a topic the
# model can ask about without the user ever having raised it.
SEEDING_DOMAIN_TERMS = [
    "payment", "stripe", "invoice", "billing", "refund", "chargeback",
    "booking", "seat", "double-booking",
    "pii", "hipaa", "soc2", "gdpr", "pci",
    "sso", "patient", "tenant isolation",
]


@pytest.mark.parametrize("prompt_id", USER_FACING_QUESTION_PROMPTS)
def test_user_facing_question_prompts_seed_no_domain_topics(prompt_id: str):
    body = REGISTRY[prompt_id].system.lower()
    found = sorted({term for term in SEEDING_DOMAIN_TERMS if term in body})
    assert not found, (
        f"prompt '{prompt_id}' names domain topics {found}. Question content must be derived "
        "from the system the user actually described. Teach the transformation with a neutral "
        "placeholder instead of a concrete domain."
    )


@pytest.mark.parametrize("prompt_id", USER_FACING_QUESTION_PROMPTS)
def test_user_facing_question_prompts_prescribe_no_topic_checklist(prompt_id: str):
    """Catches the enumerated-inventory shape even when individual words look harmless
    (e.g. 'authentication/roles, integrations, reporting, notifications, scale')."""

    body = REGISTRY[prompt_id].system.lower()
    checklist_fragments = [
        "user access channel",
        "payments if relevant",
        "reporting/exports",
        "files/storage",
        "scale/traffic/data volume",
        "baseline areas",
        "as a seed",
    ]
    found = sorted({fragment for fragment in checklist_fragments if fragment in body})
    assert not found, (
        f"prompt '{prompt_id}' contains topic-checklist fragments {found}. Which areas matter "
        "is a judgment about the described system, not a list the prompt supplies."
    )


def test_question_prompts_state_the_grounding_requirement():
    """The positive half: removing the checklist only works if the prompt also says
    where question content must come from instead."""

    for prompt_id in ("generate_questions", "generate_questions_fast"):
        body = REGISTRY[prompt_id].system.lower()
        assert "trace" in body and "context" in body, (
            f"prompt '{prompt_id}' no longer states that every question must trace back to "
            "something in the injected context — without that rule, removing the checklist "
            "just leaves the model free-associating."
        )


def test_narration_insight_sentence_is_optional_and_anti_fabrication():
    """The narration instruction now permits one extra sentence of genuine
    professional insight (closing the 'thin, silent narration' gap — a real
    consultant volunteers a relevant consideration unprompted; our narration
    previously never did). This must stay opt-in and grounded, never a forced
    template slot that manufactures pseudo-insight on every turn — that would
    just be a new, subtler form of the same hardcoding problem this file
    guards against elsewhere."""

    body = REGISTRY["ingest_patches"].system
    assert "AT MOST ONE further sentence" in body
    assert "most turns will genuinely have nothing worth adding" in body
    assert "Never invented, never generic" in body
    # Must not be tied to any specific subject matter — that would reintroduce
    # exactly the seeded-topic problem the rest of this file guards against.
    assert "particular subject matter" in body
