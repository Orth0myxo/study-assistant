import pytest

from app.services.agent_loop import run_tool_loop
from app.services.agent_models import AgentModelError, AgentTurn, ScriptedModel


def test_tool_loop_executes_tool_then_requests_final_without_tools():
    model = ScriptedModel([
        AgentTurn(tool_calls=[{"id": "1", "name": "find_passages", "arguments": {"topic": "cytology"}}]),
        AgentTurn("I found the source passages."),
    ])
    executed = []

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "request A"}],
        [],
        lambda name, args: executed.append((name, args)) or {"result_id": "r1"},
    )

    assert result.reply == "I found the source passages."
    assert executed == [("find_passages", {"topic": "cytology"})]
    assert result.model_calls == 2
    assert result.tool_calls == 1


def test_truncated_reply_keeps_partial_text_and_is_not_retried():
    model = ScriptedModel([
        AgentTurn("A partial answer", finish_reason="length"),
        AgentTurn("This retry must not be used."),
    ])

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "request A"}],
        [],
        lambda *_: None,
    )

    assert result.reply == "A partial answer"
    assert result.status == "agent_output_truncated"
    assert result.model_calls == 1
    assert model.calls == 1
    assert result.warnings == []


def test_truncated_tool_call_is_not_executed_or_retried():
    model = ScriptedModel([
        AgentTurn(
            tool_calls=[{"id": "1", "name": "get_pages", "arguments": {}}],
            finish_reason="length",
        ),
        AgentTurn("This retry must not be used."),
    ])
    executed = []

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "request A"}],
        [],
        lambda name, args: executed.append((name, args)),
    )

    assert result.status == "agent_output_truncated"
    assert result.tool_calls == 0
    assert result.model_calls == 1
    assert model.calls == 1
    assert executed == []


def test_tool_loop_enforces_call_caps_and_returns_completed_results():
    model = ScriptedModel([
        AgentTurn(tool_calls=[
            {"id": "1", "name": "one", "arguments": {}},
            {"id": "2", "name": "two", "arguments": {}},
        ]),
    ])
    result = run_tool_loop(
        model, [{"role": "user", "content": "request C"}], [],
        lambda name, args: {"name": name}, max_tool_calls=1,
    )

    assert result.tool_calls == 1
    assert [item["name"] for item in result.tool_results] == ["one"]
    assert result.reply
    assert any("tool-call limit" in warning for warning in result.warnings)


def test_tool_argument_error_is_reported_without_exposing_exception_text():
    model = ScriptedModel([
        AgentTurn(tool_calls=[{"id": "1", "name": "get_pages", "arguments": None}]),
        AgentTurn("I need a valid page number."),
    ])

    result = run_tool_loop(
        model, [{"role": "user", "content": "request B"}], [],
        lambda _name, _args: (_ for _ in ()).throw(ValueError("private source detail")),
    )

    assert result.reply == "I need a valid page number."
    assert "private source detail" not in str(result.tool_results)


def test_generic_model_failure_returns_once_without_retrying():
    class BrokenModel:
        provider = "scripted"
        model = "broken"

        def complete(self, *_args, **_kwargs):
            raise RuntimeError("private provider details")

    result = run_tool_loop(
        BrokenModel(), [{"role": "user", "content": "request D"}], [], lambda *_: None
    )

    assert result.reply == "The agent could not produce a reply."
    assert result.model_calls == 1
    assert result.warnings == ["The agent model could not complete the request."]
    assert all("private provider details" not in warning for warning in result.warnings)


def test_rate_limit_stops_the_loop_without_a_second_call():
    class LimitedModel:
        provider = "scripted"
        model = "limited"
        calls = 0

        def complete(self, *_args, **_kwargs):
            self.calls += 1
            raise AgentModelError(
                "limited", code="agent_rate_limited", status_code=429,
                retry_after=12.0,
            )

    model = LimitedModel()
    with pytest.raises(AgentModelError) as failure:
        run_tool_loop(
            model, [{"role": "user", "content": "request E"}], [], lambda *_: None
        )

    assert model.calls == 1
    assert failure.value.code == "agent_rate_limited"
    assert failure.value.retry_after == 12.0


def test_transient_provider_error_retries_once_then_raises():
    class UnavailableModel:
        provider = "scripted"
        model = "unavailable"
        calls = 0

        def complete(self, *_args, **_kwargs):
            self.calls += 1
            raise AgentModelError(
                "unavailable", code="agent_provider_unavailable", status_code=503,
                retryable=True,
            )

    model = UnavailableModel()
    with pytest.raises(AgentModelError):
        run_tool_loop(
            model, [{"role": "user", "content": "request F"}], [], lambda *_: None
        )

    assert model.calls == 2


def test_empty_final_reply_retries_once_then_returns_safe_fallback():
    model = ScriptedModel([AgentTurn(""), AgentTurn("")])

    result = run_tool_loop(
        model, [{"role": "user", "content": "request G"}], [], lambda *_: None
    )

    assert model.calls == 2
    assert result.reply == "The agent could not produce a reply."


def test_shared_model_budget_counts_calls_from_other_agent_tools():
    model = ScriptedModel([AgentTurn("final reply")])
    budget = [3]

    result = run_tool_loop(
        model, [{"role": "user", "content": "finish"}], [], lambda *_: None,
        max_model_calls=4, shared_model_calls=budget,
    )

    assert result.reply == "final reply"
    assert result.model_calls == 1
    assert budget == [4]


class RecordingModel:
    provider = "scripted"
    model = "recording"

    def __init__(self, arguments):
        self.arguments = arguments
        self.messages = []
        self.calls = 0

    def complete(self, messages, tools, *, allow_tools):
        self.calls += 1
        self.messages = [dict(message) for message in messages]
        if self.calls == 1:
            return AgentTurn(
                tool_calls=[{"id": "1", "name": "tool", "arguments": self.arguments}]
            )
        return AgentTurn("I can correct that.")


def _tool_message(model: RecordingModel) -> dict:
    import json

    tool_messages = [m for m in model.messages if m.get("role") == "tool"]
    return json.loads(tool_messages[-1]["content"])


def test_specific_tool_error_is_returned_to_the_model_with_its_field():
    from app.services.agent_loop import AgentToolError

    model = RecordingModel({"format": "pdf"})

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "export it"}],
        [],
        lambda *_: (_ for _ in ()).throw(
            AgentToolError(
                "result_required",
                "No stored result is selected.",
                field="result_id",
            )
        ),
    )

    assert result.reply == "I can correct that."
    assert _tool_message(model)["error"] == {
        "code": "result_required",
        "message": "No stored result is selected.",
        "field": "result_id",
    }


def test_validation_error_reports_field_names_only():
    from pydantic import BaseModel, Field

    class ToolArgs(BaseModel):
        document_id: str = Field(min_length=1)
        pages: list[int] = Field(min_length=1)

    model = RecordingModel({})

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "show pages"}],
        [],
        lambda *_: ToolArgs.model_validate({}),
    )

    error = _tool_message(model)["error"]
    assert result.reply == "I can correct that."
    assert error["code"] == "invalid_arguments"
    assert error["fields"] == ["document_id", "pages"]
    assert "Invalid tool field(s): document_id, pages." in error["message"]


def test_api_error_is_returned_to_the_model_instead_of_aborting_the_loop():
    from app.errors import ApiError

    model = RecordingModel({})

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "summarize"}],
        [],
        lambda *_: (_ for _ in ()).throw(
            ApiError(
                "ocr_incomplete",
                "OCR is still needed for page(s) 3.",
                409,
                details={"secret": "must not be forwarded"},
            )
        ),
    )

    assert result.reply == "I can correct that."
    assert _tool_message(model)["error"] == {
        "code": "ocr_incomplete",
        "message": "OCR is still needed for page(s) 3.",
    }


def test_time_limit_returns_a_distinct_status_with_completed_results():
    model = ScriptedModel([AgentTurn("This reply must not be requested.")])

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "slow request"}],
        [],
        lambda *_: None,
        timeout_s=0.0,
    )

    assert result.status == "agent_time_limit"
    assert result.reply == "The agent reached its time limit before finishing."
    assert result.warnings == ["Agent request time limit reached."]
    assert model.calls == 0


def test_leaked_tool_markup_is_stripped_logged_and_retried(caplog):
    leaked = (
        "<tool_call><function=get_readable_text>"
        "<parameter=document_id>doc-1</parameter></tool_call>"
    )
    model = ScriptedModel([AgentTurn(leaked), AgentTurn("Plain final answer.")])

    with caplog.at_level("INFO", logger="app.services.agent_loop"):
        result = run_tool_loop(
            model,
            [{"role": "user", "content": "read it"}],
            [],
            lambda *_: None,
        )

    assert result.reply == "Plain final answer."
    assert result.model_calls == 2
    assert "agent_tool_markup_stripped tool=unknown retried=true" in caplog.text
    assert "<tool_call>" not in result.reply


def test_leaked_markup_that_repeats_returns_one_plain_sentence(caplog):
    leaked = (
        "<function=read_outline_image><parameter=source_id>"
        "59c14ae0</parameter>"
    )
    model = ScriptedModel([AgentTurn(leaked), AgentTurn(leaked)])
    tools = [
        {
            "type": "function",
            "function": {"name": "read_outline_image", "parameters": {}},
        }
    ]

    with caplog.at_level("INFO", logger="app.services.agent_loop"):
        result = run_tool_loop(
            model,
            [{"role": "user", "content": "read the outline"}],
            tools,
            lambda *_: None,
        )

    assert result.reply == "The request could not be completed. Please retry."
    assert result.status == "agent_tool_markup"
    assert "tool=read_outline_image retried=true" in caplog.text
    assert "tool=read_outline_image retried=false" in caplog.text
    assert "<function=" not in result.reply
