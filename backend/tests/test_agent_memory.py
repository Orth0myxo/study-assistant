from __future__ import annotations

import logging

import pytest

from app.config import Settings, get_settings
from app.services import agent_service
from app.services.agent_models import AgentTurn
from app.services.agent_service import (
    _bounded_memory,
    _remember_turn,
    run_agent_turn,
)


class RecordingModel:
    provider = "scripted"
    model = "recording"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.requests: list[list[dict[str, object]]] = []

    def complete(self, messages, _tools, *, allow_tools):
        self.requests.append([dict(message) for message in messages])
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else "Done."
        return AgentTurn(reply)


def test_follow_up_receives_the_earlier_turn(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_memory_turns", 4)
    monkeypatch.setattr(settings, "agent_memory_tokens", 600)
    model = RecordingModel(
        ["The second point is about cell structure.", "The second point adds detail."]
    )
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    _, conversation, _ = run_agent_turn(
        "owner-memory", "Summarize the second point.", None, settings
    )
    _, conversation, _ = run_agent_turn(
        "owner-memory",
        "Give more detail on the second point.",
        conversation.conversation_id,
        settings,
    )

    second_request = model.requests[1]
    contents = [
        (message.get("role"), message.get("content"))
        for message in second_request
    ]
    assert ("user", "Summarize the second point.") in contents
    assert ("assistant", "The second point is about cell structure.") in contents
    assert conversation.memory[-2:] == [
        {"role": "user", "content": "Give more detail on the second point."},
        {"role": "assistant", "content": "The second point adds detail."},
    ]


def test_memory_is_bounded_by_turns_and_tokens():
    settings = Settings(agent_memory_turns=2, agent_memory_tokens=40)
    memory = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer " * 40},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer " * 40},
        {"role": "user", "content": "third question"},
        {"role": "assistant", "content": "third answer " * 40},
    ]

    bounded, used = _bounded_memory(memory, settings)

    assert len(bounded) <= 4
    assert used <= 40
    assert bounded[-1]["role"] == "assistant"
    assert bounded[-1]["content"].startswith("third answer")


@pytest.mark.parametrize(
    "status",
    [
        "agent_time_limit",
        "agent_output_truncated",
        "agent_tool_markup",
        "needs_approval",
        "agent_token_budget",
        "agent_no_reply",
    ],
)
def test_failed_turn_is_not_saved_to_memory(status, caplog):
    settings = Settings(agent_memory_turns=4, agent_memory_tokens=600)
    memory = [
        {"role": "user", "content": "Earlier question."},
        {"role": "assistant", "content": "Earlier answer."},
    ]

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        saved = _remember_turn(
            memory, "New question.", "Incomplete reply.", settings, status
        )

    assert saved == memory
    assert f"status={status}" in caplog.text
    assert "turn_saved=false" in caplog.text
    assert "outcome=skipped_status" in caplog.text


def test_normal_turn_is_saved_to_memory(caplog):
    settings = Settings(agent_memory_turns=4, agent_memory_tokens=600)

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        saved = _remember_turn(
            [], "New question.", "Complete reply.", settings, None
        )

    assert saved == [
        {"role": "user", "content": "New question."},
        {"role": "assistant", "content": "Complete reply."},
    ]
    assert "turn_saved=true" in caplog.text
    assert "status=ok" in caplog.text
    assert "outcome=ok" in caplog.text


def test_bounded_memory_keeps_complete_alternating_pairs():
    settings = Settings(agent_memory_turns=4, agent_memory_tokens=600)
    memory = [
        {"role": "user", "content": "Unanswered question."},
        {"role": "user", "content": "Answered question."},
        {"role": "assistant", "content": "Answer."},
        {"role": "user", "content": "Empty reply question."},
        {"role": "assistant", "content": "   "},
    ]

    bounded, _ = _bounded_memory(memory, settings)

    assert bounded == [
        {"role": "user", "content": "Answered question."},
        {"role": "assistant", "content": "Answer."},
    ]


def test_default_memory_budget_keeps_recent_ids_and_short_replies():
    settings = Settings()

    bounded, used = _bounded_memory(
        [
            {"role": "user", "content": "oldest question"},
            {"role": "assistant", "content": "oldest answer"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "use document doc-123 and result res-456"},
            {"role": "assistant", "content": "done"},
        ],
        settings,
    )

    assert settings.agent_memory_turns == 2
    assert settings.agent_memory_tokens == 300
    assert bounded == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "use document doc-123 and result res-456"},
        {"role": "assistant", "content": "done"},
    ]
    assert used <= 300
