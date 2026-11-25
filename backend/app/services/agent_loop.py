"""Bounded provider-neutral tool loop used by the optional study agent."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from app.errors import ApiError
from app.services.agent_models import AgentModelError, AgentTurn, ToolCallingClient

logger = logging.getLogger(__name__)

_TOOL_MARKUP_RE = re.compile(
    r"<tool_call\b|<function\s*=|<parameter\s*=|</tool_call>|</function>|</parameter>",
    re.IGNORECASE,
)
_TOOL_NAME_RE = re.compile(
    r"<function\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE
)
_DELIMITER_RE = re.compile(r"[\[\]<>]")


def _offered_tool_terms(
    tool_schemas: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    parameters: set[str] = set()
    for tool in tool_schemas:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            names.add(name)
        schema = function.get("parameters")
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                parameters.update(
                    str(key) for key in properties if isinstance(key, str)
                )
    return names, parameters


def _tool_name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )


def _requested_tool_name(text: str, tool_names: set[str]) -> str:
    match = _TOOL_NAME_RE.search(text)
    if match:
        return match.group(1)
    for name in sorted(tool_names, key=len, reverse=True):
        if _tool_name_pattern(name).search(text):
            return name
    return ""


def _looks_like_pseudo_tool_call(
    text: str,
    tool_names: set[str],
    parameter_names: set[str],
) -> bool:
    if not text or not _DELIMITER_RE.search(text):
        return False
    if any(_tool_name_pattern(name).search(text) for name in tool_names):
        return True
    return any(
        re.search(
            rf"\{{\s*[\"']{re.escape(name)}[\"']\s*:",
            text,
            re.IGNORECASE,
        )
        for name in parameter_names
    )


class AgentToolError(Exception):
    """A safe, actionable tool failure that the model may correct."""

    def __init__(
        self,
        code: str,
        message: str,
        field: str | None = None,
        id_kind: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.id_kind = id_kind

    def as_value(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.field:
            error["field"] = self.field
        if self.id_kind:
            error["id_kind"] = self.id_kind
        return {"error": error}


@dataclass
class LoopResult:
    reply: str
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    tool_calls: int = 0
    warnings: list[str] = field(default_factory=list)
    status: str | None = None
    pending: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    estimated_tokens: int = 0
    retries: int = 0
    tokens_in: int = 0
    final_call_skipped: bool = False


_TERMINAL_REPLIES = {
    "summarize": "Here is the AI summary.",
    "build_export": "The export is ready.",
    "extract_heading_sections": "Here are the extracted sections.",
    "get_pages": "Here are the source passages.",
}


def _estimate_input_tokens(
    messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]
) -> int:
    encoded = json.dumps(
        {"messages": messages, "tools": tool_schemas},
        ensure_ascii=False,
        default=str,
    )
    return max(1, (len(encoded) + 3) // 4)



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
    resume_messages: list[dict[str, Any]] | None = None,
    resume_outputs: list[dict[str, Any]] | None = None,
    resume_warnings: list[str] | None = None,
    resume_model_calls: int = 0,
    resume_tool_calls: int = 0,
    resume_estimated_tokens: int = 0,
    rate_limit_wait_s: float = 0.0,
    terminal_tools: set[str] | None = None,
) -> LoopResult:
    """Execute validated tools and finish with a no-tools model response.

    Model supplied arguments remain untrusted and are handed to ``execute`` for
    tool-specific Pydantic validation and ownership checks.
    """
    started = time.monotonic()
    history = [
        dict(message)
        for message in (resume_messages if resume_messages is not None else messages)
    ]
    outputs: list[dict[str, Any]] = list(resume_outputs or [])
    warnings: list[str] = list(resume_warnings or [])
    known_tool_names, parameter_names = _offered_tool_terms(tool_schemas)
    model_calls = max(0, int(resume_model_calls))
    tool_calls = max(0, int(resume_tool_calls))
    estimated_tokens = max(0, int(resume_estimated_tokens))
    empty_replies = 0
    retried_transient = False
    rate_limit_retried = False
    retries = 0
    tokens_in = 0
    markup_retried = False
    token_budget_stopped = False
    max_model_calls = max(1, min(int(max_model_calls), 4))
    max_tool_calls = max(0, min(int(max_tool_calls), 6))

    while model_calls < max_model_calls and (
        shared_model_calls is None or shared_model_calls[0] < max_model_calls
    ):
        if time.monotonic() - started >= timeout_s:
            warnings.append("Agent request time limit reached.")
            logger.info("agent_reply_source source=fixed outcome=ok")
            return LoopResult(
                "The agent reached its time limit before finishing.",
                outputs,
                model_calls,
                tool_calls,
                warnings,
                status="agent_time_limit",
                retries=retries,
                tokens_in=tokens_in,
            )
        if estimated_tokens >= token_budget:
            warnings.append("Agent token budget reached.")
            token_budget_stopped = True
            break
        allow_tools = (
            tool_calls < max_tool_calls
            and model_calls < max_model_calls - 1
            and (shared_model_calls is None or shared_model_calls[0] < max_model_calls - 1)
        )
        remaining = max(timeout_s - (time.monotonic() - started), 0.1)
        input_tokens = _estimate_input_tokens(history, tool_schemas)
        original_timeout = getattr(model, "_timeout", None)
        if isinstance(original_timeout, (int, float)):
            model._timeout = min(float(original_timeout), remaining)
        try:
            if shared_model_calls is not None:
                if shared_model_calls[0] >= max_model_calls:
                    break
                shared_model_calls[0] += 1
            tokens_in += input_tokens
            turn: AgentTurn = model.complete(history, tool_schemas, allow_tools=allow_tools)
            model_calls += 1
        except AgentModelError as exc:
            model_calls += 1
            if (
                exc.code == "agent_rate_limited"
                and not rate_limit_retried
                and rate_limit_wait_s > 0
                and exc.retry_after is not None
            ):
                wait = max(0.0, float(exc.retry_after))
                remaining_s = timeout_s - (time.monotonic() - started)
                if wait <= rate_limit_wait_s and wait + 0.25 < remaining_s:
                    rate_limit_retried = True
                    retries += 1
                    logger.info(
                        "agent_rate_limit wait_s=%.1f cap_s=%.1f remaining_s=%.1f "
                        "outcome=waited",
                        wait,
                        rate_limit_wait_s,
                        max(remaining_s, 0.0),
                    )
                    time.sleep(wait)
                    continue
                logger.info(
                    "agent_rate_limit wait_s=%.1f cap_s=%.1f remaining_s=%.1f "
                    "outcome=skipped_too_long",
                    wait,
                    rate_limit_wait_s,
                    max(remaining_s, 0.0),
                )
            if (
                exc.retryable
                and not retried_transient
                and model_calls < max_model_calls
                and time.monotonic() - started < timeout_s
            ):
                retried_transient = True
                time.sleep(min(0.5, max(0.0, timeout_s - (time.monotonic() - started))))
                continue
            raise
        except Exception:  # provider exceptions may contain sensitive request details
            model_calls += 1
            warnings.append("The agent model could not complete the request.")
            break
        finally:
            if isinstance(original_timeout, (int, float)):
                model._timeout = original_timeout

        estimated_tokens += max(1, (len(turn.text) + sum(len(str(c)) for c in turn.tool_calls)) // 4)
        if (turn.finish_reason or "").lower() in {"length", "max_tokens"}:
            logger.info("agent_reply_source source=model outcome=ok")
            return LoopResult(
                turn.text.strip(),
                outputs,
                model_calls,
                tool_calls,
                warnings,
                status="agent_output_truncated",
                retries=retries,
                tokens_in=tokens_in,
            )
        if not turn.tool_calls or not allow_tools:
            text = turn.text.strip()
            if _TOOL_MARKUP_RE.search(text) or _looks_like_pseudo_tool_call(
                text, known_tool_names, parameter_names
            ):
                requested_tool = _requested_tool_name(text, known_tool_names)
                safe_tool = (
                    requested_tool if requested_tool in known_tool_names else "unknown"
                )
                can_retry = (
                    not markup_retried
                    and model_calls < max_model_calls
                    and time.monotonic() - started < timeout_s
                )
                logger.info(
                    "agent_tool_markup_stripped tool=%s retried=%s",
                    safe_tool,
                    "true" if can_retry else "false",
                )
                if can_retry:
                    markup_retried = True
                    history.append({"role": "assistant", "content": turn.text})
                    history.append(
                        {
                            "role": "system",
                            "content": (
                                "Tool-call markup is not a valid reply. Call a tool "
                                "through the tool interface, or answer in plain text "
                                "without tags. Do not repeat the markup."
                            ),
                        }
                    )
                    continue
                logger.info("agent_reply_source source=fixed outcome=ok")
                return LoopResult(
                    "The request could not be completed. Please retry.",
                    outputs,
                    model_calls,
                    tool_calls,
                    warnings,
                    status="agent_tool_markup",
                    retries=retries,
                    tokens_in=tokens_in,
                )
            if text:
                logger.info("agent_reply_source source=model outcome=ok")
                return LoopResult(
                    text,
                    outputs,
                    model_calls,
                    tool_calls,
                    warnings,
                    retries=retries,
                    tokens_in=tokens_in,
                )
            empty_replies += 1
            if empty_replies < 2 and model_calls < max_model_calls:
                history.append({"role": "assistant", "content": "Please provide a concise final reply."})
                continue
            break

        available_tool_calls = max_tool_calls - tool_calls
        current_calls = turn.tool_calls[:available_tool_calls]
        terminal_candidate = bool(current_calls) and len(current_calls) == len(
            turn.tool_calls
        )
        if len(current_calls) < len(turn.tool_calls):
            warnings.append("Agent tool-call limit reached.")
            terminal_candidate = False
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
        approval_result: dict[str, Any] | None = None
        approval_call: dict[str, Any] | None = None
        for index, call in enumerate(current_calls):
            if tool_calls >= max_tool_calls:
                warnings.append("Agent tool-call limit reached.")
                terminal_candidate = False
                break
            name = call.get("name")
            call_id = call.get("id", f"call-{model_calls}-{index}")
            tool_started = time.perf_counter()
            outcome = "ok"
            field_names: list[str] = []
            id_kind = "none"
            try:
                if not isinstance(name, str) or not name:
                    raise ValueError("tool name is required")
                value = execute(name, call.get("arguments"))
                if (
                    terminal_tools is None
                    or not isinstance(name, str)
                    or name not in terminal_tools
                ):
                    terminal_candidate = False
                display_value = None
                encoded = json.dumps(value, ensure_ascii=False, default=str)
                if len(encoded) > result_chars:
                    if isinstance(value, dict) and value.get("needs_confirmation"):
                        value = {
                            "needs_confirmation": True,
                            "topics": value.get("topics", [])[:12],
                            "unreadable_lines": value.get("unreadable_lines", [])[:8],
                        }
                    elif isinstance(value, dict) and value.get("kind") == "summary":
                        display_value = value
                        value = {
                            **value,
                            "summary": str(value.get("summary") or "")[:result_chars],
                        }
                    else:
                        encoded = encoded[:result_chars] + "…"
                        value = {"truncated_result": encoded}
                outputs.append(
                    {
                        "name": name,
                        "result": display_value if display_value is not None else value,
                    }
                )
                if isinstance(value, dict) and value.get("needs_confirmation"):
                    question_result = value
                    terminal_candidate = False
                if isinstance(value, dict) and value.get("state") == "needs_approval":
                    approval_result = value
                    terminal_candidate = False
                    approval_call = {
                        "call_id": call_id,
                        "name": name,
                        "arguments": call.get("arguments"),
                    }
            except AgentToolError as exc:
                terminal_candidate = False
                outcome = exc.code
                field_names = [exc.field] if exc.field else []
                id_kind = exc.id_kind or "none"
                value = exc.as_value()
            except ValidationError as exc:
                terminal_candidate = False
                outcome = "invalid_arguments"
                field_names = sorted(
                    {
                        ".".join(
                            str(part)
                            for part in error.get("loc", ())
                            if part != "body"
                        )
                        for error in exc.errors()
                    }
                    - {""}
                )[:5]
                fields = ", ".join(field_names) or "arguments"
                value = {
                    "error": {
                        "code": "invalid_arguments",
                        "message": f"Invalid tool field(s): {fields}. Correct them and retry once.",
                        "fields": field_names,
                    }
                }
            except (ValueError, TypeError, KeyError):
                terminal_candidate = False
                outcome = "invalid_arguments"
                value = {
                    "error": {
                        "code": "invalid_arguments",
                        "message": "Tool arguments were invalid. Correct them and retry once.",
                    }
                }
            except ApiError as exc:
                terminal_candidate = False
                outcome = exc.code
                value = {"error": {"code": exc.code, "message": exc.message}}
            except Exception:  # tool exceptions must not leak source or internal details
                terminal_candidate = False
                outcome = "tool_error"
                value = {
                    "error": {
                        "code": "tool_error",
                        "message": "The requested tool could not complete.",
                    }
                }
            finally:
                elapsed = time.perf_counter() - tool_started
                safe_name = (
                    name
                    if isinstance(name, str) and name in known_tool_names
                    else "unknown"
                )
                logger.info(
                    "agent tool=%s seconds=%.2f outcome=%s fields=%s id_kind=%s",
                    safe_name,
                    elapsed,
                    outcome,
                    ",".join(field_names),
                    id_kind,
                )
            tool_calls += 1
            history.append({"role": "tool", "tool_call_id": call_id,
                            "name": str(name or "unknown"),
                            "content": json.dumps(value, ensure_ascii=False, default=str)[:result_chars]})
            estimated_tokens += min(len(str(value)), result_chars) // 4
            if question_result is not None or approval_result is not None:
                break
        if approval_result is not None and approval_call is not None:
            pending_messages = [dict(message) for message in history]
            if pending_messages and pending_messages[-1].get("role") == "tool":
                pending_messages.pop()
            pending_outputs = list(outputs)
            if pending_outputs and pending_outputs[-1].get("name") == approval_call["name"]:
                pending_outputs.pop()
            logger.info("agent_reply_source source=fixed outcome=ok")
            return LoopResult(
                "I need your approval before sending these page images to the AI provider.",
                pending_outputs,
                model_calls,
                tool_calls,
                warnings,
                status="needs_approval",
                pending={**approval_call, "details": approval_result},
                messages=pending_messages,
                estimated_tokens=estimated_tokens,
                retries=retries,
                tokens_in=tokens_in,
            )
        if question_result is not None:
            question = str(
                question_result.get("question")
                or "I need one confirmation before continuing."
            )
            logger.info("agent_reply_source source=fixed outcome=ok")
            return LoopResult(
                question,
                outputs,
                model_calls,
                tool_calls,
                warnings,
                retries=retries,
                tokens_in=tokens_in,
            )
        if terminal_candidate and terminal_tools:
            last_name = str(current_calls[-1].get("name") or "")
            reply = _TERMINAL_REPLIES.get(last_name, "The result is ready.")
            logger.info("agent_reply_source source=fixed outcome=ok")
            logger.info(
                "agent_final_call_skipped tool=%s tokens_saved~%d outcome=terminal_result",
                last_name if last_name in known_tool_names else "unknown",
                input_tokens,
            )
            return LoopResult(
                reply,
                outputs,
                model_calls,
                tool_calls,
                warnings,
                retries=retries,
                tokens_in=tokens_in,
                final_call_skipped=True,
            )

    if outputs:
        warnings.append("A final model reply was unavailable; the completed tool results are included.")
        logger.info("agent_reply_source source=fixed outcome=ok")
        return LoopResult(
            "I completed the available study actions. See the result blocks.",
            outputs,
            model_calls,
            tool_calls,
            warnings,
            status="agent_token_budget" if token_budget_stopped else "agent_no_reply",
            retries=retries,
            tokens_in=tokens_in,
        )
    logger.info("agent_reply_source source=fixed outcome=ok")
    return LoopResult(
        "The agent could not produce a reply.",
        outputs,
        model_calls,
        tool_calls,
        warnings,
        status="agent_token_budget" if token_budget_stopped else "agent_no_reply",
        retries=retries,
        tokens_in=tokens_in,
    )
