"""LLM client: model chain, retry/bench, auth and accounting via MockTransport."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from calibreaudiobridge.config import LLMConfig, LLMModelConfig
from calibreaudiobridge.errors import ConfigError
from calibreaudiobridge.llm.client import LLMClient, LLMExhaustedError, Message

if TYPE_CHECKING:
    from collections.abc import Callable

_USER: list[Message] = [{"role": "user", "content": "q"}]


def _model(
    name: str = "primary",
    *,
    base_url: str = "http://test/v1",
    api_key: str = "k",
    api_key_env: str | None = None,
    max_context_tokens: int = 8192,
) -> LLMModelConfig:
    return LLMModelConfig(
        name=name,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        max_context_tokens=max_context_tokens,
    )


def _ok(
    text: str = "hello", *, prompt: int = 5, completion: int = 3
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        },
    )


class _Scripted:
    """MockTransport handler serving a fixed script and recording requests."""

    def __init__(self, *steps: httpx.Response | Exception) -> None:
        self._steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self._steps.pop(0) if self._steps else httpx.Response(500, text="script empty")
        if isinstance(step, Exception):
            raise step
        return step

    @property
    def models_called(self) -> list[str]:
        return [str(json.loads(request.content)["model"]) for request in self.requests]


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    models: list[LLMModelConfig],
    *,
    attempts: int = 3,
) -> LLMClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    options = LLMConfig(models=tuple(models), max_attempts_per_model=attempts)
    return LLMClient(models, options, http=http)


class TestLLMClient_whenModelSucceeds:
    def test_returns_completion_with_accounting(self) -> None:
        handler = _Scripted(_ok("hi there", prompt=11, completion=7))
        result = _client(handler, [_model()]).complete(_USER, max_output_tokens=32)
        assert result.text == "hi there"
        assert result.model == "primary"
        assert result.tokens_in == 11
        assert result.tokens_out == 7

    def test_sends_bearer_auth_and_payload(self) -> None:
        handler = _Scripted(_ok())
        _client(handler, [_model(api_key="secret")]).complete(_USER, max_output_tokens=64)
        request = handler.requests[0]
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret"
        payload = json.loads(request.content)
        assert payload["model"] == "primary"
        assert payload["max_tokens"] == 64
        assert payload["messages"][0]["content"] == "q"

    def test_missing_usage_defaults_to_zero(self) -> None:
        response = httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
        result = _client(_Scripted(response), [_model()]).complete(_USER, max_output_tokens=8)
        assert result.tokens_in == 0
        assert result.tokens_out == 0


class TestLLMClient_whenApiKeyResolution:
    def test_reads_environment_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAB_TEST_KEY", "env-secret")
        handler = _Scripted(_ok())
        client = _client(handler, [_model(api_key="ignored", api_key_env="CAB_TEST_KEY")])
        client.complete(_USER, max_output_tokens=8)
        assert handler.requests[0].headers["Authorization"] == "Bearer env-secret"

    def test_missing_environment_variable_raises_config_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CAB_TEST_KEY", raising=False)
        handler = _Scripted(_ok())
        client = _client(handler, [_model(api_key_env="CAB_TEST_KEY")])
        with pytest.raises(ConfigError):
            client.complete(_USER, max_output_tokens=8)
        assert handler.requests == []


class TestLLMClient_whenTransientErrors:
    def test_retries_same_model_then_succeeds(self) -> None:
        handler = _Scripted(httpx.Response(500), httpx.Response(500), _ok("third"))
        result = _client(handler, [_model()], attempts=3).complete(_USER, max_output_tokens=8)
        assert result.text == "third"
        assert handler.models_called == ["primary", "primary", "primary"]

    def test_advances_after_retries_exhausted(self) -> None:
        handler = _Scripted(httpx.Response(503), httpx.Response(503), _ok("fallback"))
        models = [_model("primary"), _model("secondary")]
        result = _client(handler, models, attempts=2).complete(_USER, max_output_tokens=8)
        assert result.model == "secondary"
        assert handler.models_called == ["primary", "primary", "secondary"]

    def test_benches_model_for_later_calls(self) -> None:
        handler = _Scripted(
            httpx.Response(500),
            httpx.Response(500),
            _ok("a"),
            _ok("b"),
        )
        models = [_model("primary"), _model("secondary")]
        client = _client(handler, models, attempts=2)
        first = client.complete(_USER, max_output_tokens=8)
        second = client.complete(_USER, max_output_tokens=8)
        assert first.model == "secondary"
        assert second.model == "secondary"
        assert handler.models_called == ["primary", "primary", "secondary", "secondary"]


class TestLLMClient_whenContextLengthError:
    def test_advances_without_retrying(self) -> None:
        handler = _Scripted(
            httpx.Response(400, text="maximum context length exceeded"),
            _ok("fallback"),
        )
        models = [_model("primary"), _model("secondary")]
        result = _client(handler, models, attempts=3).complete(_USER, max_output_tokens=8)
        assert result.model == "secondary"
        assert handler.models_called == ["primary", "secondary"]


class TestLLMClient_whenConnectionFails:
    def test_retries_then_advances(self) -> None:
        handler = _Scripted(
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            _ok("fallback"),
        )
        models = [_model("primary"), _model("secondary")]
        result = _client(handler, models, attempts=2).complete(_USER, max_output_tokens=8)
        assert result.model == "secondary"
        assert handler.models_called == ["primary", "primary", "secondary"]


class TestLLMClient_whenAllModelsFail:
    def test_raises_exhausted_with_last_error(self) -> None:
        handler = _Scripted(httpx.Response(500), httpx.Response(500))
        models = [_model("primary"), _model("secondary")]
        client = _client(handler, models, attempts=1)
        with pytest.raises(LLMExhaustedError) as excinfo:
            client.complete(_USER, max_output_tokens=8)
        assert "secondary" in excinfo.value.last_error

    def test_empty_chain_raises_exhausted(self) -> None:
        client = _client(_Scripted(), [], attempts=1)
        with pytest.raises(LLMExhaustedError):
            client.complete(_USER, max_output_tokens=8)

    def test_has_models_reflects_chain(self) -> None:
        assert _client(_Scripted(), [_model()]).has_models is True
        assert _client(_Scripted(), []).has_models is False
