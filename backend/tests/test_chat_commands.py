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


@pytest.mark.parametrize(
    "message,expected",
    [
        ("page 1 to page 2", [(1, 1), (2, 2)]),
        ("show me the original text on page 1 and page 2", [(1, 1), (2, 2)]),
        ("pages 1, 2 and 4", [(1, 1), (2, 2), (4, 4)]),
        ("pages 1, 2, and 4", [(1, 1), (2, 2), (4, 4)]),
        ("pages 12 through 15", [(12, 15)]),
    ],
)
def test_fast_path_collects_every_page_reference(message, expected):
    command = fast_path(message)
    assert command is not None
    assert command.selector.pages == expected


@pytest.mark.parametrize(
    "message",
    [
        "give me the exact text from the document",
        "return the source text exactly as written",
        "extract verbatim text",
        "give me all the exact text",
    ],
)
def test_whole_document_verbatim_never_calls_groq(message, monkeypatch):
    import app.services.chat_commands as module

    monkeypatch.setattr(
        module, "groq_command", lambda *args: pytest.fail("Groq called for verbatim")
    )
    command = resolve_command(_payload(message), get_settings())
    assert command.action == "summarize_whole"
    assert command.selector.type == "whole"
    assert command.mode == "source_passages"


def test_exact_text_about_a_topic_is_not_whole_document():
    command = fast_path("give me the exact text about photosynthesis")
    assert command is None or command.selector.type == "topic"


def test_groq_command_normalizes_null_optional_selector_fields():
    settings = get_settings().model_copy(update={"groq_api_key": "test-groq-key"})
    command = {
        "action": "extract",
        "selector": {
            "type": "topic",
            "topic": "photosynthesis",
            "pages": None,
            "chapter": None,
            "items": None,
        },
        "mode": "ai_study_material",
        "export_format": None,
        "question": None,
    }
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(command)}}]}
            )
        )
    )
    result = groq_command(
        _payload("explain the part about photosynthesis"), settings, http_client=client
    )
    assert result.selector.topic == "photosynthesis"
    assert result.selector.pages == []
    assert result.selector.items == []


def test_selector_accepts_omitted_optional_fields():
    from app.schemas.chat_command import ChatCommandSelector

    selector = ChatCommandSelector.model_validate({"type": "whole"})
    assert selector.pages == []
    assert selector.items == []
    assert selector.topic is None
    assert selector.chapter is None


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


BARE_TOPICS = [
    "Cervical smear",
    "Cerebrospinal smear",
    "Barr bodies smear",
    "Nipple discharge smear",
    "Urine sediment",
    "Tissue sectioning",
    "Hematoxylin and Eosin staining",
    "Special stain / special organ slide",
    "sickle cell anaemia.",
]

NOT_TOPICS = [
    "I need verbatim text from the image, from the pdf",
    "Exact topic from the picture",
    "I need the passage from the topics in the image, verbatim.",
    "I need summary of the pdf",
    "thanks",
    "ok",
    "can you help me",
    "this is a very long sentence that is clearly not a topic name at all",
]


@pytest.mark.parametrize("message", BARE_TOPICS)
def test_regex_fallback_treats_a_bare_name_as_a_topic(message):
    from app.services.chat_commands import regex_fallback

    command = regex_fallback(message)
    assert command.action == "extract"
    assert command.selector.type == "topic"
    assert command.selector.topic == message.strip(" .")
    assert command.mode == "source_passages"
    assert command.export_format is None


@pytest.mark.parametrize("message", NOT_TOPICS)
def test_regex_fallback_still_asks_when_message_is_not_a_topic_name(message):
    from app.services.chat_commands import regex_fallback

    command = regex_fallback(message)
    assert command.action != "extract" or command.selector.type != "topic"
    assert command.action in {"clarify", "help"}


def test_groq_outage_still_finds_a_bare_topic(monkeypatch):
    import app.services.chat_commands as module
    from app.services.ai_client import AiError

    def offline(*_args, **_kwargs):
        raise AiError("rate limited", code="ai_rate_limited", retryable=True)

    monkeypatch.setattr(module, "groq_command", offline)
    command = resolve_command(_payload("Cervical smear"), get_settings())
    assert command.action == "extract"
    assert command.selector.topic == "Cervical smear"
