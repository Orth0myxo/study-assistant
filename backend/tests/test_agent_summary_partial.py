from __future__ import annotations

import logging
import time

import pytest

from app.config import get_settings
from app.services import agent_service
from app.services.agent_loop import AgentToolError
from app.services.agent_service import AgentTools
from app.services.agent_store import AgentConversation
from app.services.ai_chain import AiAllProvidersFailed
from app.services.ai_client import AiProviderError, AiRateLimited
from tests.helpers import build_pdf


@pytest.fixture(autouse=True)
def _summary_logs(caplog):
    caplog.set_level(logging.INFO, logger="app.services.agent_service")


def _upload_pdf(client) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "five-parts.pdf",
                build_pdf(
                    [f"Part {number} source sentence here." for number in range(1, 6)]
                ),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _tools(identity, document_id: str) -> AgentTools:
    conversation = AgentConversation(
        "conversation-summary",
        identity["user_id"],
        last_document_id=document_id,
        last_source_id=document_id,
        last_source_kind="pdf",
    )
    return AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 10,
        [0],
        [{"kind": "pdf", "document_id": document_id}],
    )


class ScriptedSummary:
    def __init__(self, failures=None, empty=None) -> None:
        self.calls = 0
        self.failures = failures or {}
        self.empty = empty or set()

    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls += 1
        failure = self.failures.get(self.calls)
        if failure is not None:
            raise failure
        if self.calls in self.empty:
            return ""
        return f"Summary part {self.calls}"

    def close(self):
        pass


def _configure(monkeypatch, fake: ScriptedSummary, parts: int = 8) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_max_model_calls", 1)
    monkeypatch.setattr(settings, "agent_summary_model_calls", parts)
    monkeypatch.setattr(settings, "max_summary_chunk_chars", 50)
    monkeypatch.setattr(
        agent_service, "build_ai_client", lambda *args, **kwargs: fake
    )


def _detailed(tools: AgentTools, document_id: str, **extra):
    return tools.execute(
        "summarize",
        {
            "document_id": document_id,
            "scope": "document",
            "length": "detailed",
            **extra,
        },
    )


def test_later_part_failure_keeps_completed_parts(
    client, identity, monkeypatch, caplog
):
    document_id = _upload_pdf(client)
    fake = ScriptedSummary(
        failures={
            3: AiAllProvidersFailed(
                [("groq", AiProviderError("provider is down"))]
            )
        }
    )
    _configure(monkeypatch, fake)
    tools = _tools(identity, document_id)

    result = _detailed(tools, document_id)

    assert result["has_more"] is True
    assert result["next_part"] == 3
    assert result["stopped_reason"] == "provider_unavailable"
    assert "Summary part 1" in result["summary"]
    assert "Summary part 2" in result["summary"]
    assert "Summary part 3" not in result["summary"]
    assert "stopped early" in result["basis"]
    assert "parts_done=2 total=5 reason=provider_unavailable" in caplog.text
    assert "outcome=partial" in caplog.text


def test_first_part_failure_raises_summary_unavailable(
    client, identity, monkeypatch, caplog
):
    document_id = _upload_pdf(client)
    fake = ScriptedSummary(
        failures={
            1: AiAllProvidersFailed(
                [("groq", AiProviderError("provider is down"))]
            )
        }
    )
    _configure(monkeypatch, fake)
    tools = _tools(identity, document_id)

    with pytest.raises(AgentToolError) as excinfo:
        _detailed(tools, document_id)

    assert excinfo.value.code == "summary_unavailable"
    assert "parts_done=0 total=5 reason=provider_unavailable" in caplog.text
    assert "outcome=failed" in caplog.text


def test_empty_part_stops_before_it(client, identity, monkeypatch):
    document_id = _upload_pdf(client)
    fake = ScriptedSummary(empty={2})
    _configure(monkeypatch, fake)
    tools = _tools(identity, document_id)

    result = _detailed(tools, document_id)

    assert result["has_more"] is True
    assert result["next_part"] == 2
    assert result["stopped_reason"] == "empty_part"
    assert "Summary part 1" in result["summary"]
    assert "Summary part 2" not in result["summary"]


def test_all_rate_limit_errors_set_rate_limited_reason(client, identity, monkeypatch):
    document_id = _upload_pdf(client)
    fake = ScriptedSummary(
        failures={
            2: AiAllProvidersFailed(
                [
                    ("groq", AiRateLimited("limited", retry_after=12.0)),
                    ("gemini", AiRateLimited("limited", retry_after=8.0)),
                ]
            )
        }
    )
    _configure(monkeypatch, fake)
    tools = _tools(identity, document_id)

    result = _detailed(tools, document_id)

    assert result["has_more"] is True
    assert result["next_part"] == 2
    assert result["stopped_reason"] == "rate_limited"
    assert result["retry_after_seconds"] == 12.0


def test_not_enough_time_returns_partial_with_continue(
    client, identity, monkeypatch, caplog
):
    document_id = _upload_pdf(client)
    fake = ScriptedSummary()
    _configure(monkeypatch, fake, parts=1)
    tools = _tools(identity, document_id)
    first = _detailed(tools, document_id)
    assert first["next_part"] == 2

    monkeypatch.setattr(
        get_settings(), "agent_summary_part_min_seconds", 100.0
    )
    second = _detailed(tools, document_id, result_id=first["result_id"], start_part=2)

    assert second["has_more"] is True
    assert second["next_part"] == 2
    assert second["stopped_reason"] == "time_limit"
    assert second["continue_prompt"]
    assert "Summary part 1" in second["summary"]
    assert fake.calls == 1
    assert "reason=time_limit" in caplog.text
    assert "outcome=partial" in caplog.text
