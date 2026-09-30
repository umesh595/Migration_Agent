"""Provider-agnostic LLM interface. Adding Groq/Anthropic later means writing one
new class in app/llm/providers/ that implements LLMProvider — not touching the
gateway, the graph nodes, or any prompt (DECISIONS.md Q1)."""

from __future__ import annotations

import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
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
        "\u2022": "*",
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
    """Normalize LLM text while keeping ordinary punctuation intact.

    Local Windows Postgres instances may use WIN1252. Normalize common Unicode
    punctuation to safe equivalents, then replace characters that cannot be
    represented by CP1252.

    Important: do not replace '?' because it is valid CP1252 punctuation and
    may be meaningful in generated questions.
    """

    normalized = unicodedata.normalize("NFKC", value).translate(_TEXT_REPLACEMENTS)

    return normalized.encode("cp1252", errors="replace").decode("cp1252")


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

    # A reasoning-model provider (e.g. CodeVector's DeepSeek route) may return a
    # separate reasoning trace alongside the final structured `parsed` output.
    reasoning: str | None = None


@dataclass(frozen=True)
class LLMCallOptions:
    thinking: str = "off"


class StructuredOutputError(Exception):
    """Raised when a provider could not return schema-valid output within the
    retry budget. Callers must treat this as 'state untouched' — never persist
    partial output (Doc 3 §3.2 failure branches)."""


class ProviderQuotaExceededError(StructuredOutputError):
    """Raised when the provider rejects a call because its account has no
    quota/credits left.

    This is distinct from transient rate limits or malformed responses, which
    should continue through the normal retry/escalation path.
    """


class ProviderRequestError(StructuredOutputError):
    """Raised when the provider API fails before returning usable model output.

    This covers account/API availability failures, transport errors, and
    timeouts.
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
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        options: LLMCallOptions | None = None,
    ) -> StructuredResponse[T]:
        """One structured-output call.

        Must raise StructuredOutputError if the provider returns content that
        doesn't validate against response_model. Retry/escalation policy lives
        in the gateway, not here.

        `on_delta`, when given, is a reasoning-model provider's opportunity to
        call it with each chain-of-thought text fragment as it streams in.
        Providers that do not support streaming may ignore it.
        """

    @abstractmethod
    def model_for_tier(self, tier: ModelTier) -> str:
        ...