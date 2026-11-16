"""Bounded provider-neutral tool loop used by the optional study agent."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from app.errors import ApiError
from app.services.agent_models import AgentTurn, ToolCallingClient

logger = logging.getLogger(__name__)


@dataclass
class LoopResult:
    reply: str
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    tool_calls: int = 0
    warnings: list[str] = field(default_factory=list)


def run_tool_loop(
    model: ToolCallingClient,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
    execute: Callable[[str, Any], Any],
    *,
    max_model_calls: int = 4,
    max_tool_calls: int = 6,
    token_budget: int = 7000,
    result_chars: int = 4000,
    timeout_s: float = 45.0,
    shared_model_calls: list[int] | None = None,
) -> LoopResult:
    """Execute validated tools and finish with a no-tools model response.

    Model supplied arguments remain untrusted and are handed to ``execute`` for
    tool-specific Pydantic validation and ownership checks.
    """
    started = time.monotonic()
    history = [dict(message) for message in messages]
    outputs: list[dict[str, Any]] = []
    warnings: list[str] = []
    known_tool_names = {
        tool.get("function", {}).get("name")
        for tool in tool_schemas
        if isinstance(tool, dict)
    }
    model_calls = tool_calls = estimated_tokens = failures = 0
    max_model_calls = max(1, min(int(max_model_calls), 4))
    max_tool_calls = max(0, min(int(max_tool_calls), 6))

    while model_calls < max_model_calls and (
        shared_model_calls is None or shared_model_calls[0] < max_model_calls
    ):
        if time.monotonic() - started >= timeout_s:
            warnings.append("Agent request time limit reached.")
            break
        if estimated_tokens >= token_budget:
            warnings.append("Agent token budget reached.")
            break
        allow_tools = (
            tool_calls < max_tool_calls
            and model_calls < max_model_calls - 1
            and (shared_model_calls is None or shared_model_calls[0] < max_model_calls - 1)
        )
        remaining = max(timeout_s - (time.monotonic() - started), 0.1)
        original_timeout = getattr(model, "_timeout", None)
        if isinstance(original_timeout, (int, float)):
            model._timeout = min(float(original_timeout), remaining)
        try:
            if shared_model_calls is not None:
                if shared_model_calls[0] >= max_model_calls:
                    break
                shared_model_calls[0] += 1
            turn: AgentTurn = model.complete(history, tool_schemas, allow_tools=allow_tools)
            model_calls += 1
        except Exception:  # provider exceptions may contain sensitive request details
            model_calls += 1
            failures += 1
            warnings.append("The agent model could not complete the request.")
            if failures < 2 and model_calls < max_model_calls:
                continue
            break
        finally:
            if isinstance(original_timeout, (int, float)):
                model._timeout = original_timeout

        estimated_tokens += max(1, (len(turn.text) + sum(len(str(c)) for c in turn.tool_calls)) // 4)
        if not turn.tool_calls or not allow_tools:
            if turn.text.strip():
                return LoopResult(turn.text.strip(), outputs, model_calls, tool_calls, warnings)
            failures += 1
            if failures < 2 and model_calls < max_model_calls:
                history.append({"role": "assistant", "content": "Please provide a concise final reply."})
                continue
            break

        available_tool_calls = max_tool_calls - tool_calls
        current_calls = turn.tool_calls[:available_tool_calls]
        if len(current_calls) < len(turn.tool_calls):
            warnings.append("Agent tool-call limit reached.")
        history.append({
            "role": "assistant",
            "content": turn.text,
            "tool_calls": [
                {"id": call.get("id", f"call-{model_calls}-{index}"),
                 "type": "function",
                 "function": {"name": call.get("name"),
                              "arguments": json.dumps(call.get("arguments"), ensure_ascii=False)}}
                for index, call in enumerate(current_calls)
            ],
        })
        question_result: dict[str, Any] | None = None
        for index, call in enumerate(current_calls):
            if tool_calls >= max_tool_calls:
                warnings.append("Agent tool-call limit reached.")
                break
            name = call.get("name")
            call_id = call.get("id", f"call-{model_calls}-{index}")
            tool_started = time.perf_counter()
            outcome = "ok"
            try:
                if not isinstance(name, str) or not name:
                    raise ValueError("tool name is required")
                value = execute(name, call.get("arguments"))
                encoded = json.dumps(value, ensure_ascii=False, default=str)
                if len(encoded) > result_chars:
                    if isinstance(value, dict) and value.get("needs_confirmation"):
                        value = {
                            "needs_confirmation": True,
                            "topics": value.get("topics", [])[:12],
                            "unreadable_lines": value.get("unreadable_lines", [])[:8],
                        }
                    else:
                        encoded = encoded[:result_chars] + "…"
                        value = {"truncated_result": encoded}
                outputs.append({"name": name, "result": value})
                if isinstance(value, dict) and value.get("needs_confirmation"):
                    question_result = value
            except (ValueError, TypeError, KeyError, ValidationError):
                outcome = "invalid_arguments"
                value = {"error": "Tool arguments were invalid. Correct them and retry once."}
            except ApiError as exc:
                outcome = exc.code
                raise
            except Exception:  # tool exceptions must not leak source or internal details
                outcome = "tool_error"
                value = {"error": "The requested tool could not complete."}
            finally:
                elapsed = time.perf_counter() - tool_started
                safe_name = (
                    name
                    if isinstance(name, str) and name in known_tool_names
                    else "unknown"
                )
                logger.info("agent tool=%s seconds=%.2f outcome=%s", safe_name, elapsed, outcome)
            tool_calls += 1
            history.append({"role": "tool", "tool_call_id": call_id,
                            "name": str(name or "unknown"),
                            "content": json.dumps(value, ensure_ascii=False, default=str)[:result_chars]})
            estimated_tokens += min(len(str(value)), result_chars) // 4
            if question_result is not None:
                break
        if question_result is not None:
            return LoopResult(
                "Please confirm or edit the detected outline topics.",
                outputs,
                model_calls,
                tool_calls,
                warnings,
            )

    if outputs:
        warnings.append("A final model reply was unavailable; the completed tool results are included.")
        return LoopResult("I completed the available study actions. See the result blocks.", outputs, model_calls, tool_calls, warnings)
    return LoopResult("I could not complete that request. Please try a more specific instruction.", outputs, model_calls, tool_calls, warnings)
