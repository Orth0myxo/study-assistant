from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services import agent_service
from app.services.agent_models import AgentTurn
from app.services.topic_sources import topic_sources
from app.services.users import users
from tests.helpers import build_pdf, build_png

SCENARIOS_PATH = Path(__file__).parent / "agent_eval" / "scenarios.json"


def _scenarios() -> list[dict[str, Any]]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def _resolve(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return context.get(value, value)
    if isinstance(value, list):
        return [_resolve(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, context) for key, item in value.items()}
    return value


def _latest_tool_result(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        try:
            value = json.loads(str(message.get("content") or "{}"))
        except (TypeError, ValueError):
            continue
        result_id = value.get("result_id") if isinstance(value, dict) else None
        if isinstance(result_id, str):
            return result_id
    return None


class PlanModel:
    """Deterministic provider that follows a fixture plan step by step."""

    provider = "scripted"
    model = "scripted"

    def __init__(self, plan: list[dict[str, Any]], context: dict[str, str]) -> None:
        self.plan = list(plan)
        self.context = dict(context)
        self.calls = 0

    def complete(self, messages, tools, *, allow_tools: bool) -> AgentTurn:
        self.calls += 1
        if not self.plan or not allow_tools:
            return AgentTurn("Fixture reply.")
        step = self.plan.pop(0)
        context = dict(self.context)
        latest = _latest_tool_result(messages)
        if latest:
            context["$tool_result"] = latest
        arguments = _resolve(step.get("arguments") or {}, context)
        return AgentTurn(
            tool_calls=[
                {
                    "id": f"fixture-{self.calls}",
                    "name": step["name"],
                    "arguments": arguments,
                }
            ]
        )

    def close(self) -> None:
        return None


class FixtureSummaryClient:
    def summarize(self, *, source_text: str, source_label: str, purpose: str = "section") -> str:
        return "AI-generated fixture summary."

    def close(self) -> None:
        return None


def _upload_fixture(client, scenario: dict[str, Any]) -> str:
    pages = scenario.get("fixture", {}).get("pages") or ["Fixture source passage."]
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("fixture.pdf", build_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _make_outline_source(client, document_id: str, identity: dict[str, str], topics: list[str]) -> str:
    response = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id},
        files={"images": ("outline.png", build_png(), "image/png")},
    )
    assert response.status_code == 201, response.text
    source_id = response.json()["source_id"]
    if topics:
        updated = topic_sources.update_for_owner(
            source_id, identity["user_id"], topics=topics
        )
        assert updated is not None
    return source_id


def _result_texts(client, blocks: list[dict[str, Any]]) -> tuple[list[str], int]:
    texts: list[str] = []
    page_count = 0
    for block in blocks:
        if block.get("type") != "passages":
            continue
        response = client.get(f"/api/v1/agent/results/{block['result_id']}")
        assert response.status_code == 200, response.text
        for topic in response.json().get("topics", []):
            for passage in topic.get("passages", []):
                page_count += 1 if passage.get("page") is not None else 0
                texts.append(str(passage.get("text") or ""))
    return texts, page_count


def _check_turn(
    client,
    turn: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> None:
    checks = turn.get("checks") or {}
    types = [block.get("type") for block in blocks]
    for required in checks.get("blocks", []):
        assert required in types, (required, types)
    for forbidden in checks.get("forbid_blocks", []):
        assert forbidden not in types, (forbidden, types)

    texts, page_count = _result_texts(client, blocks)
    joined = "\n".join(texts)
    if "passage_contains" in checks:
        assert checks["passage_contains"] in joined
    if "result_text_contains" in checks:
        assert checks["result_text_contains"] in joined
    if "page_count_min" in checks:
        assert page_count >= int(checks["page_count_min"])

    summaries = [block for block in blocks if block.get("type") == "summary"]
    if "summary_label_contains" in checks:
        assert any(
            checks["summary_label_contains"] in str(block.get("label") or "")
            for block in summaries
        )
    if checks.get("summary_basis_nonempty"):
        assert any(str(block.get("basis") or "").strip() for block in summaries)
    if "summary_not_contains" in checks:
        assert all(
            checks["summary_not_contains"] not in str(block.get("text") or "")
            for block in summaries
        )

    questions = [block for block in blocks if block.get("type") == "question"]
    if "question_contains" in checks:
        assert any(
            checks["question_contains"] in str(block.get("text") or "")
            for block in questions
        )
    if "question_options_min" in checks:
        assert any(
            len(block.get("options") or []) >= int(checks["question_options_min"])
            for block in questions
        )
    if checks.get("file_download"):
        files = [block for block in blocks if block.get("type") == "file"]
        assert files
        response = client.get(f"/api/v1/agent/files/{files[0]['file_id']}")
        assert response.status_code == 200
        assert response.content


def test_agent_scenarios_are_abstract_and_well_formed():
    scenarios = _scenarios()
    goals = [scenario["goal"] for scenario in scenarios]

    assert len(scenarios) >= 10
    assert len(goals) == len(set(goals))
    for scenario in scenarios:
        assert isinstance(scenario.get("attachments"), list)
        assert scenario.get("turns")
        assert "message" not in scenario
        assert scenario["goal"].replace("_", "").isalnum()
        for turn in scenario["turns"]:
            assert turn.get("plan")
            for step in turn["plan"]:
                assert step.get("name")
                arguments = step.get("arguments")
                assert isinstance(arguments, dict)
                assert "message" not in arguments


def test_agent_fixture_scenarios_reach_the_expected_outcome(
    client, identity, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(agent_service, "build_ai_client", lambda *args, **kwargs: FixtureSummaryClient())
    users.set_ai_reading_consent(identity["user_id"], "always")

    class FixtureVisionReader:
        def interpret_outline_image(self, _image, _mime):
            return []

        def read_table_image(self, _image, _mime):
            return {"columns": [], "rows": []}

        def read_page_image(self, _image, _mime):
            return ""

        def close(self):
            pass

    monkeypatch.setattr(
        agent_service,
        "get_image_reader",
        lambda _settings: FixtureVisionReader(),
    )

    for scenario in _scenarios():
        document_id = _upload_fixture(client, scenario)
        source_id = None
        outline_topics = scenario.get("fixture", {}).get("outline_topics")
        if outline_topics is not None:
            source_id = _make_outline_source(
                client, document_id, identity, list(outline_topics)
            )
        attachments = [
            {
                **attachment,
                "id": document_id if attachment.get("kind") == "pdf" else source_id,
            }
            for attachment in scenario.get("attachments", [])
        ]
        conversation_id = None
        for turn in scenario["turns"]:
            context = {
                "$doc": document_id,
                "$source": source_id or "",
                "$last_result": "",
            }
            if conversation_id:
                conversation = agent_service.agent_store.get_conversation(
                    conversation_id, identity["user_id"]
                )
                assert conversation is not None
                context["$last_result"] = conversation.last_result_id or ""
            model = PlanModel(turn["plan"], context)
            monkeypatch.setattr(
                agent_service, "configured_models", lambda _settings, model=model: [model]
            )
            blocks, conversation, _ = agent_service.run_agent_turn(
                identity["user_id"],
                "request A",
                conversation_id,
                settings,
                attachments,
            )
            conversation_id = conversation.conversation_id
            _check_turn(client, turn, blocks)


def test_agent_tool_rejects_another_owners_result(client, identity, make_user, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(agent_service, "build_ai_client", lambda *args, **kwargs: FixtureSummaryClient())
    document_id = _upload_fixture(
        client, {"fixture": {"pages": ["Cytology source passage one."]}}
    )
    result = agent_service.agent_store.save_result(
        identity["user_id"], document_id, "passages", {"topics": []}
    )
    other = make_user()
    model = PlanModel(
        [{"name": "get_pages", "arguments": {"result_id": result.result_id}}],
        {"$doc": document_id, "$source": "", "$last_result": ""},
    )
    monkeypatch.setattr(
        agent_service, "configured_models", lambda _settings: [model]
    )

    blocks, _, usage = agent_service.run_agent_turn(
        other["user_id"], "request A", None, settings
    )

    assert blocks == []
    assert usage.tool_calls == 1
    assert usage.tool_results == []
