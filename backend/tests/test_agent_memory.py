from __future__ import annotations

from app.config import Settings, get_settings
from app.services import agent_service
from app.services.agent_models import AgentTurn
from app.services.agent_service import _bounded_memory, run_agent_turn


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
