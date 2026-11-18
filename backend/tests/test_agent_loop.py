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
