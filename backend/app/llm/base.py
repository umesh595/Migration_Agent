"""Provider-agnostic LLM interface. Adding Groq/Anthropic later means writing one
new class in app/llm/providers/ that implements LLMProvider — not touching the
gateway, the graph nodes, or any prompt (DECISIONS.md Q1)."""

from __future__ import annotations

import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

_TEXT_REPLACEMENTS = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2022": "-",
        "\u2026": "...",
        "\u2190": "<-",
        "\u2191": "^",
        "\u2192": "->",
        "\u2193": "v",
        "\u2713": "OK",
        "\ufe0f": "",
    }
)


def normalize_llm_text(value: str) -> str:
    """Keep LLM-facing and checkpointed text safe for local Windows Postgres.

    Some local dev databases are initialized with WIN1252 instead of UTF-8. A
    model response containing JSON escapes for characters like U+2011 can make
    Postgres reject LangGraph checkpoint writes. Normalizing here preserves the
    meaning while avoiding those unsupported code points.
    """

    normalized = unicodedata.normalize("NFKC", value).translate(_TEXT_REPLACEMENTS)
    return normalized.encode("cp1252", errors="replace").decode("cp1252").replace("?", "-")


class ModelTier(StrEnum):
    CHEAP = "cheap"
    STRONG = "strong"


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StructuredResponse[T: BaseModel]:
    parsed: T
    usage: LLMUsage
    model: str
    attempts: int


class StructuredOutputError(Exception):
    """Raised when a provider could not return schema-valid output within the
    retry budget. Callers must treat this as 'state untouched' — never persist
    partial output (Doc 3 §3.2 failure branches)."""


class ProviderQuotaExceededError(StructuredOutputError):
    """A specific StructuredOutputError subclass: the provider rejected the
    call because its account has no quota/credits left, not because the
    response failed to validate. A provider raises this instead of the plain
    base class only for that specific condition, never for a transient rate
    limit or a malformed response — those should keep retrying/escalating
    against the same provider the way they always have. FallbackLLMProvider
    is the only thing that catches this subclass specifically; every other
    existing catch site still treats it as a normal StructuredOutputError."""


class ProviderRequestError(StructuredOutputError):
    """Raised when the provider API failed before returning usable model output.

    This covers account/API availability failures, transport errors, and timeouts.
    FallbackLLMProvider can switch providers for these without hiding schema bugs.
    """


class TokenBudgetExceededError(Exception):
    """Raised when a session's cumulative token spend would exceed its budget."""


class LLMProvider(ABC):
    @abstractmethod
    async def complete_structured[T: BaseModel](
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> StructuredResponse[T]:
        """One structured-output call. Must raise StructuredOutputError if the
        provider returns content that doesn't validate against response_model —
        the retry/escalation policy lives in the gateway, not here."""

    @abstractmethod
    def model_for_tier(self, tier: ModelTier) -> str: ...
