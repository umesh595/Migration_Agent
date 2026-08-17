"""Provider-agnostic LLM interface. Adding Groq/Anthropic later means writing one
new class in app/llm/providers/ that implements LLMProvider — not touching the
gateway, the graph nodes, or any prompt (DECISIONS.md Q1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


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
