from app.services.agent_loop import run_tool_loop
from app.services.agent_models import AgentTurn, ScriptedModel


def test_tool_loop_executes_tool_then_requests_final_without_tools():
    model = ScriptedModel([
        AgentTurn(tool_calls=[{"id": "1", "name": "find_passages", "arguments": {"topic": "cytology"}}]),
        AgentTurn("I found the source passages."),
    ])
    executed = []

    result = run_tool_loop(
        model,
        [{"role": "user", "content": "Find cytology."}],
        [],
        lambda name, args: executed.append((name, args)) or {"result_id": "r1"},
    )

    assert result.reply == "I found the source passages."
    assert executed == [("find_passages", {"topic": "cytology"})]
    assert result.model_calls == 2
    assert result.tool_calls == 1


def test_tool_loop_enforces_call_caps_and_returns_completed_results():
    model = ScriptedModel([
        AgentTurn(tool_calls=[
            {"id": "1", "name": "one", "arguments": {}},
            {"id": "2", "name": "two", "arguments": {}},
        ]),
    ])
    result = run_tool_loop(
        model, [{"role": "user", "content": "do both"}], [],
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
        model, [{"role": "user", "content": "Page 2"}], [],
        lambda _name, _args: (_ for _ in ()).throw(ValueError("private source detail")),
    )

    assert result.reply == "I need a valid page number."
    assert "private source detail" not in str(result.tool_results)


def test_two_model_failures_return_nonempty_fallback():
    class BrokenModel:
        provider = "scripted"
        model = "broken"

        def complete(self, *_args, **_kwargs):
            raise RuntimeError("private provider details")

    result = run_tool_loop(
        BrokenModel(), [{"role": "user", "content": "help"}], [], lambda *_: None
    )

    assert result.reply
    assert result.model_calls == 2
    assert all("private provider details" not in warning for warning in result.warnings)
