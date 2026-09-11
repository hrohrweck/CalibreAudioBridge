"""OpenAI-compatible chat client with an ordered model chain (PLAN.md 6.4).

The client is the single LLM egress point: it owns retry/bench policy, bearer
auth resolution and token accounting. Callers hand it fully-formed messages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypedDict

import httpx

from ..errors import CabError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..config import LLMConfig, LLMModelConfig


class Message(TypedDict):
    """One chat message in the OpenAI wire shape."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    """A successful completion plus token accounting."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int


class LLMExhaustedError(CabError):
    """Every model in the chain failed for the current call."""

    last_error: str

    def __init__(self, last_error: str) -> None:
        super().__init__(f"all LLM models exhausted: {last_error}")
        self.last_error = last_error


class _RetryableError(Exception):
    """Transient failure: retry the same model, then bench it."""


class _AdvanceError(Exception):
    """Permanent failure for this model: advance to the next chain entry."""


_DEFAULT_TIMEOUT_S: Final = 120
_CLIENT_ERROR_MIN: Final = 400
_SERVER_ERROR_MIN: Final = 500
_ERROR_BODY_CHARS: Final = 200
_MAX_OUTPUT_TOKENS: Final = 4096
_PROMPT_OVERHEAD_TOKENS: Final = 256


class LLMClient:
    """Ordered model chain over the OpenAI-compatible chat completions API."""

    def __init__(
        self,
        models: Sequence[LLMModelConfig],
        options: LLMConfig,
        http: httpx.Client | None = None,
    ) -> None:
        self._models = tuple(models)
        self._options = options
        self._http = http if http is not None else httpx.Client(timeout=_DEFAULT_TIMEOUT_S)
        self._benched: set[str] = set()

    @property
    def has_models(self) -> bool:
        """Whether the chain has at least one entry (summary cannot degrade)."""
        return bool(self._models)

    @property
    def input_budget_tokens(self) -> int:
        """Usable prompt size for the primary model, output and overhead reserved."""
        if not self._models:
            return 0
        reserve = max(self._options.output_reserve_tokens, _MAX_OUTPUT_TOKENS)
        return max(1, self._models[0].max_context_tokens - reserve - _PROMPT_OVERHEAD_TOKENS)

    def complete(self, messages: list[Message], *, max_output_tokens: int) -> Completion:
        """Complete against the chain; raise LLMExhaustedError when all models fail."""
        last_error = "no models configured"
        for model in self._models:
            if model.name in self._benched:
                continue
            try:
                return self._complete_with(model, messages, max_output_tokens)
            except _AdvanceError as exc:
                last_error = str(exc)
            except _RetryableError as exc:
                last_error = str(exc)
                self._benched.add(model.name)
        raise LLMExhaustedError(last_error)

    def _complete_with(
        self,
        model: LLMModelConfig,
        messages: list[Message],
        max_output_tokens: int,
    ) -> Completion:
        last: _RetryableError | None = None
        for _ in range(self._options.max_attempts_per_model):
            try:
                return self._call(model, messages, max_output_tokens)
            except _RetryableError as exc:
                last = exc
        if last is None:
            raise LLMExhaustedError(f"{model.name}: no attempt made")
        raise last

    def _call(
        self,
        model: LLMModelConfig,
        messages: list[Message],
        max_output_tokens: int,
    ) -> Completion:
        payload = {
            "model": model.name,
            "messages": messages,
            "max_tokens": max_output_tokens,
        }
        try:
            response = self._http.post(
                _completions_url(model), json=payload, headers=self._headers(model)
            )
        except httpx.TransportError as exc:
            raise _RetryableError(f"{model.name}: transport error: {exc}") from exc
        if response.status_code >= _SERVER_ERROR_MIN:
            raise _RetryableError(f"{model.name}: HTTP {response.status_code}")
        if response.status_code >= _CLIENT_ERROR_MIN:
            raise _AdvanceError(
                f"{model.name}: HTTP {response.status_code}: {response.text[:_ERROR_BODY_CHARS]}"
            )
        return _parse(model, response)

    def _headers(self, model: LLMModelConfig) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key(model)}"}

    def _api_key(self, model: LLMModelConfig) -> str:
        if model.api_key_env is None:
            return model.api_key
        value = os.environ.get(model.api_key_env)
        if not value:
            raise ConfigError(
                f"environment variable {model.api_key_env} is not set (model {model.name})"
            )
        return value


def _completions_url(model: LLMModelConfig) -> str:
    return f"{model.base_url.rstrip('/')}/chat/completions"


def _parse(model: LLMModelConfig, response: httpx.Response) -> Completion:
    data = response.json()
    if not isinstance(data, dict):
        raise _AdvanceError(f"{model.name}: response is not an object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _AdvanceError(f"{model.name}: response carries no choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise _AdvanceError(f"{model.name}: response carries no text content")
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return Completion(
        text=text,
        model=model.name,
        tokens_in=_as_int(usage.get("prompt_tokens")),
        tokens_out=_as_int(usage.get("completion_tokens")),
    )


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0
