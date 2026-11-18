import json
import logging

import httpx
import pytest

from app.config import Settings
from app.services.agent_models import (
    AgentModelError,
    AgentToolClient,
    GeminiToolClient,
    GroqToolClient,
    reset_output_token_tracker,
)
from app.services.agent_service import _agent_system_prompt, configured_models


def test_groq_tool_adapter_builds_function_call_payload():
    seen = {}

    def respond(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "", "tool_calls": [{"id": "call-1", "function": {
                "name": "get_pages", "arguments": '{"document_id":"doc-a","pages":[2]}'
            }}]
        }}]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = GroqToolClient(Settings(groq_api_key="secret"), client=client)
    result = adapter.complete(
        [{"role": "user", "content": "request B"}],
        [{"type": "function", "function": {"name": "get_pages"}}],
        allow_tools=True,
    )

    assert seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["max_completion_tokens"] == 800
    assert seen["body"]["reasoning_effort"] == "low"
    assert result.tool_calls == [{
        "id": "call-1", "name": "get_pages",
        "arguments": {"document_id": "doc-a", "pages": [2]},
    }]
    client.close()


def test_agent_output_cap_depends_on_whether_tools_are_offered():
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "reply"},
            "finish_reason": "stop",
        }]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret",
            agent_model="cap-model",
            agent_base_url="https://agent.example/v1",
            agent_max_output_tokens=321,
            agent_tool_max_output_tokens=654,
        ),
        client=client,
    )
    with_tools = adapter.complete(
        [{"role": "user", "content": "request"}], [], allow_tools=True
    )
    without_tools = adapter.complete(
        [{"role": "user", "content": "request"}], [], allow_tools=False
    )

    assert calls[0]["max_completion_tokens"] == 654
    assert calls[1]["max_completion_tokens"] == 321
    assert with_tools.finish_reason == "stop"
    assert without_tools.finish_reason == "stop"
    client.close()


def test_openai_adapter_preserves_a_length_finish_reason():
    def respond(_request):
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "A partial answer"},
            "finish_reason": "length",
        }]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret",
            agent_model="length-model",
            agent_base_url="https://agent.example/v1",
        ),
        client=client,
    )
    result = adapter.complete(
        [{"role": "user", "content": "request"}], [], allow_tools=False
    )

    assert result.text == "A partial answer"
    assert result.finish_reason == "length"
    client.close()


def test_agent_system_prompt_uses_configured_output_limits():
    prompt = _agent_system_prompt(Settings(
        agent_max_output_tokens=321,
        agent_tool_max_output_tokens=654,
        agent_reasoning_effort="high",
    ))
    changed = _agent_system_prompt(Settings(
        agent_max_output_tokens=322,
        agent_tool_max_output_tokens=655,
        agent_reasoning_effort="low",
    ))

    assert "321" in prompt
    assert "654" in prompt
    assert "high" in prompt
    assert prompt != changed
    assert "322" in changed
    assert "655" in changed
    assert "low" in changed


def test_agent_tool_adapter_builds_openai_compatible_function_call_payload():
    seen = {}

    def respond(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "call-agent", "function": {
                "name": "get_pages",
                "arguments": '{"document_id":"doc-a","pages":[2]}',
            }}],
        }}]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret",
            agent_model="agent-test",
            agent_base_url="https://agent.example/v1",
        ),
        client=client,
    )
    result = adapter.complete(
        [{"role": "user", "content": "request A"}],
        [{"type": "function", "function": {"name": "get_pages"}}],
        allow_tools=True,
    )

    assert seen["url"] == "https://agent.example/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "agent-test"
    assert seen["body"]["tool_choice"] == "auto"
    assert result.tool_calls == [{
        "id": "call-agent",
        "name": "get_pages",
        "arguments": {"document_id": "doc-a", "pages": [2]},
    }]
    client.close()


def test_agent_provider_order_can_rotate_to_an_openai_compatible_endpoint():
    models = configured_models(
        Settings(
            agent_api_key="agent-secret",
            agent_model="agent-test",
            agent_base_url="https://agent.example/v1",
            groq_api_key="groq-secret",
            gemini_api_key="gemini-secret",
        )
    )

    assert [model.provider for model in models] == ["agent"]
    assert models[0].model == "agent-test"
    models[0].close()


def test_gemini_tool_adapter_parses_function_call_and_response_history():
    seen = {}

    def respond(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "find_passages", "args": {"document_id": "doc-a", "topics": ["cytology"]}}}
        ]}}]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = GeminiToolClient(Settings(gemini_api_key="secret"), client=client)
    result = adapter.complete(
        [{"role": "system", "content": "system"},
         {"role": "assistant", "content": "", "tool_calls": [{
             "id": "call-1", "type": "function",
             "function": {"name": "list_materials", "arguments": "{}"},
         }]},
         {"role": "tool", "name": "list_materials", "content": '{"materials":[]}'}],
        [{"type": "function", "function": {
            "name": "find_passages", "description": "Find source text.",
            "parameters": {"type": "object", "properties": {}},
        }}],
        allow_tools=True,
    )

    assert seen["body"]["contents"][0]["parts"][0]["functionCall"]["name"] == "list_materials"
    assert seen["body"]["contents"][1]["parts"][0]["functionResponse"]["name"] == "list_materials"
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 800
    assert result.tool_calls[0]["name"] == "find_passages"
    client.close()


@pytest.mark.parametrize(
    ("status", "headers", "code", "retry_after"),
    [
        (429, {"retry-after": "12", "x-ratelimit-remaining-requests": "0"},
         "agent_rate_limited", 12.0),
        (401, {}, "agent_provider_auth_failed", None),
        (404, {}, "agent_model_not_found", None),
        (503, {}, "agent_provider_unavailable", None),
    ],
)
def test_agent_provider_errors_are_classified(
    status, headers, code, retry_after, caplog
):
    def respond(_request):
        return httpx.Response(status, headers=headers, json={
            "error": {"message": "Provider failure with secret-value"}
        })

    caplog.set_level(logging.INFO)
    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret-value",
            agent_model=f"classify-{status}",
            agent_base_url="https://agent.example/v1",
        ),
        client=client,
    )

    with pytest.raises(AgentModelError) as failure:
        adapter.complete([{"role": "user", "content": "request"}], [], allow_tools=False)

    assert failure.value.code == code
    assert failure.value.status_code == status
    assert failure.value.retry_after == retry_after
    if status == 429:
        assert failure.value.rate_limits["x-ratelimit-remaining-requests"] == "0"
    assert "secret-value" not in caplog.text
    client.close()


def test_agent_timeout_is_classified():
    def respond(_request):
        raise httpx.ReadTimeout("private transport detail")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret",
            agent_model="timeout-model",
            agent_base_url="https://agent.example/v1",
        ),
        client=client,
    )

    with pytest.raises(AgentModelError) as failure:
        adapter.complete([{"role": "user", "content": "request"}], [], allow_tools=False)

    assert failure.value.code == "agent_timed_out"
    assert failure.value.status_code == 504
    client.close()


def test_failed_call_without_usage_does_not_consume_the_output_budget():
    reset_output_token_tracker()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "limited"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "reply"}}],
            "usage": {"completion_tokens": 10},
        })

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret",
            agent_model="failed-budget-model",
            agent_base_url="https://agent.example/v1",
            agent_max_output_tokens=500,
            agent_output_tokens_per_minute=500,
        ),
        client=client,
    )

    with pytest.raises(AgentModelError):
        adapter.complete([{"role": "user", "content": "first"}], [], allow_tools=False)
    adapter.complete([{"role": "user", "content": "second"}], [], allow_tools=False)

    assert len(calls) == 2
    reset_output_token_tracker()
    client.close()


def test_output_token_budget_blocks_a_call_before_the_provider():
    reset_output_token_tracker()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "short reply"}}],
            "usage": {"completion_tokens": 200},
        })

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = AgentToolClient(
        Settings(
            agent_api_key="secret",
            agent_model="budget-model",
            agent_base_url="https://agent.example/v1",
            agent_max_output_tokens=500,
            agent_output_tokens_per_minute=500,
            agent_reasoning_effort="none",
        ),
        client=client,
    )

    adapter.complete([{"role": "user", "content": "first"}], [], allow_tools=False)
    with pytest.raises(AgentModelError) as failure:
        adapter.complete([{"role": "user", "content": "second"}], [], allow_tools=False)

    assert len(calls) == 1
    assert calls[0]["max_completion_tokens"] == 500
    assert calls[0]["reasoning_effort"] == "none"
    assert failure.value.code == "agent_rate_limited"
    assert failure.value.retry_after and failure.value.retry_after > 0
    reset_output_token_tracker()
    client.close()
