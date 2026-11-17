import json

import httpx

from app.config import Settings
from app.services.agent_models import (
    AgentToolClient,
    GeminiToolClient,
    GroqToolClient,
)
from app.services.agent_service import configured_models


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
    assert result.tool_calls == [{
        "id": "call-1", "name": "get_pages",
        "arguments": {"document_id": "doc-a", "pages": [2]},
    }]
    client.close()


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
    assert result.tool_calls[0]["name"] == "find_passages"
    client.close()
