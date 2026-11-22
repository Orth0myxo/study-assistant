"""Provider adapters for the agent's bounded tool-calling loop."""

from __future__ import annotations

import email.utils
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.services.timing import log_provider_call


@dataclass
class AgentTurn:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass
class _OutputUse:
    at: float
    tokens: int


_output_usage: dict[str, list[_OutputUse]] = {}
_output_lock = threading.Lock()


def reset_output_token_tracker() -> None:
    with _output_lock:
        _output_usage.clear()


def _reserve_output(
    key: str, max_tokens: int, per_minute: int | None
) -> _OutputUse | None:
    if not per_minute or per_minute <= 0:
        return None
    now = time.monotonic()
    with _output_lock:
        uses = _output_usage.setdefault(key, [])
        uses[:] = [use for use in uses if now - use.at < 60]
        used = sum(use.tokens for use in uses)
        if used + max_tokens > per_minute:
            retry_after = max(1.0, 60 - (now - uses[0].at)) if uses else 60.0
            raise AgentModelError(
                "The agent output-token budget is exhausted.",
                code="agent_rate_limited",
                status_code=429,
                retry_after=retry_after,
                retryable=False,
            )
        entry = _OutputUse(now, max_tokens)
        uses.append(entry)
        return entry


def _settle_output(
    key: str, entry: _OutputUse | None, tokens: int | None
) -> None:
    if entry is None:
        return
    with _output_lock:
        entry.tokens = max(0, int(tokens or 0))


class AgentModelError(RuntimeError):
    """A provider failure classified for the API and the mobile app."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int | None = None,
        retry_after: float | None = None,
        retryable: bool = False,
        rate_limits: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable
        self.rate_limits = rate_limits or {}


class ToolCallingClient(Protocol):
    provider: str
    model: str

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, allow_tools: bool
    ) -> AgentTurn: ...


class ScriptedModel:
    """Deterministic model for loop tests and offline evaluations."""

    provider = "scripted"
    model = "scripted"

    def __init__(self, turns: list[AgentTurn]) -> None:
        self.turns = list(turns)
        self.calls = 0

    def complete(self, messages, tools, *, allow_tools: bool) -> AgentTurn:
        self.calls += 1
        if not self.turns:
            return AgentTurn("I completed the available steps.")
        turn = self.turns.pop(0)
        if not allow_tools and turn.tool_calls:
            return AgentTurn(turn.text)
        return turn


_DURATION_RE = re.compile(
    r"^(?:(?P<hours>\d+(?:\.\d+)?)h)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)m(?!s))?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)s)?"
    r"(?:(?P<millis>\d+(?:\.\d+)?)ms)?$"
)


def _duration_seconds(value: str | None) -> float | None:
    """Parse a provider duration such as ``43s``, ``1m30s`` or ``500ms``."""
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    match = _DURATION_RE.match(text)
    if not match or not any(match.groupdict().values()):
        return None
    parts = match.groupdict()
    return max(
        0.0,
        float(parts["hours"] or 0) * 3600.0
        + float(parts["minutes"] or 0) * 60.0
        + float(parts["seconds"] or 0)
        + float(parts["millis"] or 0) / 1000.0,
    )


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    duration = _duration_seconds(value)
    if duration is not None:
        return duration
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _honest_rate_limit_wait(
    retry_after: float | None, rate_limits: dict[str, str]
) -> float | None:
    """Use the larger of retry-after and the provider's token/request reset."""
    candidates = [retry_after] if retry_after is not None else []
    for name in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        candidates.append(_duration_seconds(rate_limits.get(name)))
    values = [value for value in candidates if value is not None]
    return max(values) if values else None


def _error_text(response: httpx.Response, key: str) -> str:
    text = (response.text or "")[:1000].replace(key, "[redacted]")
    try:
        body = response.json()
        message = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None)
        if message:
            text = str(message)
    except (ValueError, TypeError):
        pass
    return " ".join(text.replace(key, "[redacted]").split())[:300]


def _http_failure(
    response: httpx.Response,
    key: str,
) -> tuple[AgentModelError, str, float | None, dict[str, str]]:
    status = response.status_code
    error_text = _error_text(response, key)
    retry_after = _retry_after_seconds(response.headers.get("retry-after"))
    rate_limits = {
        name: value
        for name, value in response.headers.items()
        if name.lower().startswith("x-ratelimit-")
    }
    if status == 429:
        retry_after = _honest_rate_limit_wait(retry_after, rate_limits)
        code, retryable = "agent_rate_limited", False
    elif status in {401, 403}:
        code, retryable = "agent_provider_auth_failed", False
    elif status == 404:
        code, retryable = "agent_model_not_found", False
    elif status >= 500:
        code, retryable = "agent_provider_unavailable", True
    else:
        code, retryable = "agent_provider_error", False
    error = AgentModelError(
        f"The agent provider returned HTTP {status}.",
        code=code,
        status_code=status,
        retry_after=retry_after,
        retryable=retryable,
        rate_limits=rate_limits,
    )
    return error, error_text, retry_after, rate_limits


def _request_tokens(payload: dict[str, Any]) -> int:
    return max(1, (len(json.dumps(payload, ensure_ascii=False, default=str)) + 3) // 4)


class OpenAICompatibleToolClient:
    """OpenAI-style chat-completions adapter for agent tool calling."""

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        reasoning_effort: str | None = None,
        max_output_tokens: int = 500,
        tool_max_output_tokens: int = 800,
        output_tokens_per_minute: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = timeout
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max(1, int(max_output_tokens))
        self._tool_max_output_tokens = max(1, int(tool_max_output_tokens))
        self._output_tokens_per_minute = output_tokens_per_minute
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(self, messages, tools, *, allow_tools: bool) -> AgentTurn:
        output_limit = (
            self._tool_max_output_tokens if allow_tools else self._max_output_tokens
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": output_limit,
        }
        if allow_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        started = time.perf_counter()
        budget_key = f"{self.provider}:{self.model}"
        try:
            budget_entry = _reserve_output(
                budget_key, output_limit, self._output_tokens_per_minute
            )
        except AgentModelError as exc:
            log_provider_call(
                self.provider,
                self.model,
                "agent",
                started,
                "local_rate_limit",
                status_code=429,
                retry_after=exc.retry_after,
                request_tokens=_request_tokens(payload),
            )
            raise
        output_tokens: int | None = None
        outcome = "error"
        status_code: int | None = None
        retry_after: float | None = None
        error_text: str | None = None
        rate_limits: dict[str, str] = {}
        try:
            response = self._client.post(
                self._url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
            status_code = response.status_code
            outcome = f"http_{status_code}"
            if status_code >= 400:
                failure, error_text, retry_after, rate_limits = _http_failure(
                    response, self._key
                )
                raise failure
            body = response.json()
            message = body["choices"][0]["message"]
            calls = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = None
                calls.append(
                    {
                        "id": call.get("id"),
                        "name": function.get("name"),
                        "arguments": args,
                    }
                )
            usage = body.get("usage") if isinstance(body, dict) else None
            if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                output_tokens = int(usage["completion_tokens"])
            choice = body["choices"][0]
            finish_reason = choice.get("finish_reason")
            outcome = "ok"
            return AgentTurn(
                str(message.get("content") or ""),
                calls,
                str(finish_reason) if finish_reason else None,
            )
        except httpx.TimeoutException as exc:
            outcome = "timeout"
            raise AgentModelError(
                "The agent model timed out.",
                code="agent_timed_out",
                status_code=504,
                retryable=False,
            ) from exc
        except AgentModelError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentModelError(
                "The agent model could not complete the request.",
                code="agent_provider_error",
                status_code=status_code,
                retryable=False,
            ) from exc
        finally:
            _settle_output(budget_key, budget_entry, output_tokens)
            log_provider_call(
                self.provider,
                self.model,
                "agent",
                started,
                outcome,
                status_code=status_code,
                retry_after=retry_after,
                error=error_text,
                request_tokens=_request_tokens(payload),
                rate_limits=rate_limits,
            )


class AgentToolClient(OpenAICompatibleToolClient):
    provider = "agent"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if not (
            settings.agent_api_key
            and settings.agent_model
            and settings.agent_base_url
        ):
            raise ValueError("The agent endpoint is not configured.")
        super().__init__(
            provider="agent",
            api_key=settings.agent_api_key,
            model=settings.agent_model,
            base_url=settings.agent_base_url,
            timeout=settings.agent_request_timeout_s,
            reasoning_effort=settings.agent_reasoning_effort,
            max_output_tokens=settings.agent_max_output_tokens,
            tool_max_output_tokens=settings.agent_tool_max_output_tokens,
            output_tokens_per_minute=settings.agent_output_tokens_per_minute,
            client=client,
        )


class GroqToolClient(OpenAICompatibleToolClient):
    provider = "groq"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if not settings.groq_api_key:
            raise ValueError("Groq is not configured for the agent.")
        super().__init__(
            provider="groq",
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
            timeout=settings.agent_request_timeout_s,
            reasoning_effort=settings.agent_reasoning_effort,
            max_output_tokens=settings.agent_max_output_tokens,
            tool_max_output_tokens=settings.agent_tool_max_output_tokens,
            output_tokens_per_minute=settings.agent_output_tokens_per_minute,
            client=client,
        )


class GeminiToolClient:
    provider = "gemini"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if not settings.gemini_api_key:
            raise ValueError("Gemini is not configured for the agent.")
        self.model = settings.gemini_model
        self._key = settings.gemini_api_key
        self._url = f"{settings.gemini_api_base_url.rstrip('/')}/models/{self.model}:generateContent"
        self._timeout = settings.agent_request_timeout_s
        self._max_output_tokens = max(1, int(settings.agent_max_output_tokens))
        self._tool_max_output_tokens = max(1, int(settings.agent_tool_max_output_tokens))
        self._output_tokens_per_minute = settings.agent_output_tokens_per_minute
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(self, messages, tools, *, allow_tools: bool) -> AgentTurn:
        system = "\n\n".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        )
        contents = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "tool":
                contents.append({"role": "user", "parts": [{"functionResponse": {
                    "name": message.get("name", "tool"),
                    "response": {"content": message.get("content", "")},
                }}]})
                continue
            parts = []
            if message.get("content"):
                parts.append({"text": message.get("content", "")})
            for call in message.get("tool_calls", []):
                function = call.get("function") or {} if isinstance(call, dict) else {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except (TypeError, ValueError):
                    arguments = {}
                parts.append({"functionCall": {
                    "name": function.get("name", "unknown"),
                    "args": arguments,
                }})
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts or [{"text": ""}]})
        output_limit = (
            self._tool_max_output_tokens if allow_tools else self._max_output_tokens
        )
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": output_limit},
        }
        if allow_tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool["function"]["name"], "description": tool["function"]["description"], "parameters": tool["function"]["parameters"]}
                for tool in tools
            ]}]
        started = time.perf_counter()
        budget_key = f"{self.provider}:{self.model}"
        try:
            budget_entry = _reserve_output(
                budget_key, output_limit, self._output_tokens_per_minute
            )
        except AgentModelError as exc:
            log_provider_call(
                self.provider,
                self.model,
                "agent",
                started,
                "local_rate_limit",
                status_code=429,
                retry_after=exc.retry_after,
                request_tokens=_request_tokens(payload),
            )
            raise
        output_tokens: int | None = None
        outcome = "error"
        status_code: int | None = None
        retry_after: float | None = None
        error_text: str | None = None
        rate_limits: dict[str, str] = {}
        try:
            response = self._client.post(
                self._url,
                json=payload,
                headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
            status_code = response.status_code
            outcome = f"http_{status_code}"
            if status_code >= 400:
                failure, error_text, retry_after, rate_limits = _http_failure(
                    response, self._key
                )
                raise failure
            body = response.json()
            usage = body.get("usageMetadata") if isinstance(body, dict) else None
            if isinstance(usage, dict) and usage.get("candidatesTokenCount") is not None:
                output_tokens = int(usage["candidatesTokenCount"])
            candidate = body["candidates"][0]
            parts = candidate["content"]["parts"]
            text_parts = [str(part["text"]) for part in parts if isinstance(part, dict) and "text" in part]
            calls = [
                {"id": f"gemini-{index}", "name": part["functionCall"].get("name"), "arguments": part["functionCall"].get("args", {})}
                for index, part in enumerate(parts)
                if isinstance(part, dict) and isinstance(part.get("functionCall"), dict)
            ]
            finish_reason = candidate.get("finishReason")
            outcome = "ok"
            return AgentTurn(
                "\n".join(text_parts),
                calls,
                str(finish_reason) if finish_reason else None,
            )
        except httpx.TimeoutException as exc:
            outcome = "timeout"
            raise AgentModelError(
                "The agent model timed out.",
                code="agent_timed_out",
                status_code=504,
                retryable=False,
            ) from exc
        except AgentModelError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentModelError(
                "The agent model could not complete the request.",
                code="agent_provider_error",
                status_code=status_code,
                retryable=False,
            ) from exc
        finally:
            _settle_output(budget_key, budget_entry, output_tokens)
            log_provider_call(
                self.provider,
                self.model,
                "agent",
                started,
                outcome,
                status_code=status_code,
                retry_after=retry_after,
                error=error_text,
                request_tokens=_request_tokens(payload),
                rate_limits=rate_limits,
            )
