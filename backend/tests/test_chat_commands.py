import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.schemas.chat_command import ChatCommand, ChatCommandRequest
from app.services.chat_commands import fast_path, groq_command, resolve_command


MESSAGES = [
    "that part about malaria parasites in the mosquito",
    "Explain malaria parasites in the mosquito",
    "can you find the section on sickle cell disease",
    "I need the part where they discuss mitosis",
    "what did my notes say about glycolysis?",
    "Summarise the Calvin cycle for me",
    "please locate the bit about kidney function",
    "I want to revise cell membrane transport",
    "could you explain photosynthesis from these notes",
    "show me where they mention haemoglobin",
    "the bit about malaria in mosquitoes",
    "MALARIA PARASITES IN MOSQUITOES, please",
    "find malaria parasites in the mosquito!!!",
    "I need an explanation of mitosis and meiosis",
    "What is the lecturer saying about immunity?",
    "can u find dat part abt malaria parasite",
    "malaira parasite in mosqito",
    "Pls explain the renin angiotensin system",
    "I don't understand the part on photosynthesis",
    "give me notes about DNA replication",
    "is there anything about insulin resistance",
    "look up the section about blood groups",
    "that paragraph discussing protein synthesis",
    "tell me about the Krebs cycle",
    "What does it say on active transport?",
    "find the topic: communicable diseases",
    "Can you locate antibiotic resistance in the material",
    "explain the immune respone",
    "where are the notes on malaria parasites in mosquitoes",
    "please get the section about renal filtration",
    "Mixed CASE request about malaria parasites",
    "Summarize pages 12 to 15 verbatim with a short summary then Word file",
]


def _payload(message: str) -> ChatCommandRequest:
    return ChatCommandRequest.model_validate(
        {
            "message": message,
            "context": {
                "document_names": ["lecture.pdf"],
                "page_count": 42,
                "doc_format": "pdf",
            },
        }
    )


def _command_json():
    return {
        "action": "extract",
        "selector": {
            "type": "topic",
            "topic": "malaria parasites in the mosquito",
            "pages": [],
            "chapter": None,
            "items": [],
        },
        "mode": "source_passages",
        "export_format": None,
        "question": None,
    }


@pytest.mark.parametrize("message", MESSAGES)
def test_groq_command_accepts_realistic_phrasings_without_sending_content(message, monkeypatch):
    settings = get_settings().model_copy(update={"groq_api_key": "test-groq-key"})
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_command_json())}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = groq_command(_payload(message), settings, http_client=client)
    assert isinstance(result, ChatCommand)
    body = json.loads(seen[0].content)
    prompt = body["messages"][1]["content"]
    assert message[:500] in prompt
    assert "lecture.pdf" in prompt
    assert "document text" not in prompt.lower()
    assert "chat history" not in prompt.lower()


@pytest.mark.parametrize(
    "message,expected_type",
    [
        ("pages 12-15", "pages"),
        ("Pages 12 to 15 exact", "pages"),
        ("pages 3, 5 and 8-10", "pages"),
        ("chapter 3", "headings"),
        ("question 1 to 50", "items"),
    ],
)
def test_fast_path_never_calls_groq(message, expected_type, monkeypatch):
    import app.services.chat_commands as module

    monkeypatch.setattr(module, "groq_command", lambda *args: pytest.fail("Groq called for fast path"))
    command = resolve_command(_payload(message), get_settings())
    assert command.selector.type == expected_type


def test_fast_path_maps_modes_and_export_format():
    command = fast_path("pages 12 to 15 verbatim with a short summary then Word file")
    assert command is not None
    assert command.mode == "both"
    assert command.export_format == "docx"
    assert command.selector.pages == [(12, 15)]


def test_invalid_provider_json_falls_back_to_existing_style_intent():
    settings = get_settings().model_copy(update={"groq_api_key": "test"})
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "{bad"}}]}
            )
        )
    )
    import app.services.chat_commands as module

    result = module.groq_command
    # Exercise the command failure path without needing network access.
    def bad_command(payload, _settings):
        return groq_command(payload, settings, http_client=client)

    module.groq_command = bad_command
    try:
        command = resolve_command(_payload("Explain malaria"), settings)
    finally:
        module.groq_command = result
    assert command.action == "extract"
    assert command.selector.topic == "malaria"


def test_provider_outage_falls_back_or_clarifies_without_crashing(monkeypatch):
    import app.services.chat_commands as module

    monkeypatch.setattr(module, "groq_command", lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    # resolve_command catches typed provider errors. Unexpected exceptions must
    # also be treated as a failed model response, not an app crash.
    payload = _payload("that part about malaria parasites in the mosquito")
    command = resolve_command(payload, get_settings())
    assert command.action == "clarify"


def test_command_schema_rejects_unknown_and_incomplete_fields():
    command = _command_json()
    command["unexpected"] = True
    with pytest.raises(ValidationError):
        ChatCommand.model_validate(command)
    incomplete = _command_json()
    incomplete["selector"]["topic"] = None
    with pytest.raises(ValidationError):
        ChatCommand.model_validate(incomplete)


def test_chat_command_endpoint_uses_per_minute_limiter(client, monkeypatch):
    from app.api.chat import _requests

    _requests.clear()
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 1)
    body = {
        "message": "pages 12-15",
        "context": {"document_names": [], "page_count": 30, "doc_format": "pdf"},
    }
    first = client.post("/api/v1/chat/command", json=body)
    second = client.post("/api/v1/chat/command", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["selector"]["pages"] == [[12, 15]]
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
    _requests.clear()
