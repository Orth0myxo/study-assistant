"""Provider adapters for the agent's bounded tool-calling loop."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.services.timing import log_provider_call


@dataclass
class AgentTurn:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


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
        client: httpx.Client | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = timeout
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(self, messages, tools, *, allow_tools: bool) -> AgentTurn:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if allow_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        started = time.perf_counter()
        outcome = "error"
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
            outcome = f"http_{response.status_code}"
            response.raise_for_status()
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
            outcome = "ok"
            return AgentTurn(str(message.get("content") or ""), calls)
        except httpx.TimeoutException as exc:
            outcome = "timeout"
            raise RuntimeError("The agent model timed out.") from exc
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "The agent model could not complete the request."
            ) from exc
        finally:
            log_provider_call(self.provider, self.model, "agent", started, outcome)


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
        payload: dict[str, Any] = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
        if allow_tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool["function"]["name"], "description": tool["function"]["description"], "parameters": tool["function"]["parameters"]}
                for tool in tools
            ]}]
        started = time.perf_counter()
        outcome = "error"
        try:
            response = self._client.post(
                self._url,
                json=payload,
                headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
            outcome = f"http_{response.status_code}"
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"]["parts"]
            text_parts = [str(part["text"]) for part in parts if isinstance(part, dict) and "text" in part]
            calls = [
                {"id": f"gemini-{index}", "name": part["functionCall"].get("name"), "arguments": part["functionCall"].get("args", {})}
                for index, part in enumerate(parts)
                if isinstance(part, dict) and isinstance(part.get("functionCall"), dict)
            ]
            outcome = "ok"
            return AgentTurn("\n".join(text_parts), calls)
        except httpx.TimeoutException as exc:
            outcome = "timeout"
            raise RuntimeError("The agent model timed out.") from exc
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("The agent model could not complete the request.") from exc
        finally:
            log_provider_call(self.provider, self.model, "agent", started, outcome)
