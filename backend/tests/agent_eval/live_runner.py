"""On-demand live-model behaviour check for the optional study agent.

This script is not part of CI. It generates varied user requests from abstract
scenario goals, runs the real configured agent, and applies the same outcome
checks as the fixture tests. Generated wording is test output only.

Run from ``backend/``::

    python -m tests.agent_eval.live_runner --variants 3

Provider credentials are read from the normal server environment. Nothing is
written to the repository by default.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.topic_sources import topic_sources
from tests.helpers import build_pdf, build_png

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")


def _scenarios() -> list[dict[str, Any]]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def _provider(settings) -> tuple[str, str, str]:
    order = [
        item.strip().lower()
        for item in settings.agent_provider_order.split(",")
        if item.strip()
    ]
    for name in order:
        if name == "agent" and settings.agent_api_key:
            return "agent", settings.agent_model, settings.agent_api_key
        if name == "groq" and settings.groq_api_key:
            return "groq", settings.groq_model, settings.groq_api_key
        if name == "gemini" and settings.gemini_api_key:
            return "gemini", settings.gemini_model, settings.gemini_api_key
    raise SystemExit(
        "No agent provider key is configured. Set AGENT_API_KEY and its "
        "OpenAI-compatible base URL/model, or another provider in "
        "AGENT_PROVIDER_ORDER."
    )


def _generate_messages(
    settings,
    provider: tuple[str, str, str],
    goal: str,
    fixture: dict[str, Any],
    count: int,
) -> list[str]:
    name, model, key = provider
    system = (
        "Write distinct study-assistant user requests for the abstract goal. "
        "Vary wording, length, typos, and language. Return one request per line. "
        "Do not add numbering, labels, or explanations."
    )
    user = json.dumps(
        {
            "goal": goal,
            "count": count,
            "material": {
                "page_count": len(fixture.get("pages") or []),
                "has_outline_image": "outline_topics" in fixture,
            },
        },
        ensure_ascii=False,
    )
    timeout = httpx.Timeout(60.0, connect=15.0)
    with httpx.Client(timeout=timeout) as client:
        if name in {"agent", "groq"}:
            base_url = (
                settings.agent_base_url if name == "agent" else settings.groq_base_url
            )
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 700,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"].get("content") or ""
        else:
            response = client.post(
                f"{settings.gemini_api_base_url.rstrip('/')}/models/"
                f"{model}:generateContent",
                params={"key": key},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": 0.9, "maxOutputTokens": 700},
                },
            )
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"].get("parts") or []
            content = "".join(str(part.get("text") or "") for part in parts)
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        text = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned[:count]


def _result_texts(client: TestClient, blocks: list[dict[str, Any]]) -> tuple[list[str], int]:
    texts: list[str] = []
    pages: set[int] = set()
    for block in blocks:
        if block.get("type") != "passages":
            continue
        response = client.get(f"/api/v1/agent/results/{block['result_id']}")
        response.raise_for_status()
        for topic in response.json().get("topics", []):
            for passage in topic.get("passages", []):
                if isinstance(passage.get("page"), int):
                    pages.add(passage["page"])
                texts.append(str(passage.get("text") or ""))
    return texts, len(pages)


def _apply_checks(client: TestClient, checks: dict[str, Any], blocks: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    types = [block.get("type") for block in blocks]
    for required in checks.get("blocks", []):
        if required not in types:
            failures.append(f"missing block {required}; got {types}")
    for forbidden in checks.get("forbid_blocks", []):
        if forbidden in types:
            failures.append(f"unexpected block {forbidden}")
    texts, page_count = _result_texts(client, blocks)
    joined = "\n".join(texts)
    if "passage_contains" in checks and checks["passage_contains"] not in joined:
        failures.append("expected source passage was not returned")
    if "result_text_contains" in checks and checks["result_text_contains"] not in joined:
        failures.append("expected result text was not returned")
    if "page_count_min" in checks and page_count < int(checks["page_count_min"]):
        failures.append(f"expected at least {checks['page_count_min']} pages, got {page_count}")
    summaries = [block for block in blocks if block.get("type") == "summary"]
    if "summary_label_contains" in checks and not any(
        checks["summary_label_contains"] in str(block.get("label") or "") for block in summaries
    ):
        failures.append("summary label did not match")
    if checks.get("summary_basis_nonempty") and not any(
        str(block.get("basis") or "").strip() for block in summaries
    ):
        failures.append("summary basis was missing")
    if "summary_not_contains" in checks and any(
        checks["summary_not_contains"] in str(block.get("text") or "") for block in summaries
    ):
        failures.append("summary echoed untrusted instruction text")
    questions = [block for block in blocks if block.get("type") == "question"]
    if "question_contains" in checks and not any(
        checks["question_contains"] in str(block.get("text") or "") for block in questions
    ):
        failures.append("expected question was not returned")
    if "question_options_min" in checks and not any(
        len(block.get("options") or []) >= int(checks["question_options_min"])
        for block in questions
    ):
        failures.append("question options were insufficient")
    if checks.get("file_download"):
        files = [block for block in blocks if block.get("type") == "file"]
        if not files:
            failures.append("file block was missing")
        else:
            response = client.get(f"/api/v1/agent/files/{files[0]['file_id']}")
            if response.status_code != 200 or not response.content:
                failures.append("file download failed or was empty")
    return failures


def _upload_fixture(client: TestClient, scenario: dict[str, Any]) -> str:
    pages = scenario.get("fixture", {}).get("pages") or ["Fixture source passage."]
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("fixture.pdf", build_pdf(pages), "application/pdf")},
    )
    response.raise_for_status()
    return response.json()["document_id"]


def _make_source(
    client: TestClient, document_id: str, user_id: str, topics: list[str]
) -> str:
    response = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id},
        files={"images": ("outline.png", build_png(), "image/png")},
    )
    response.raise_for_status()
    source_id = response.json()["source_id"]
    if topics:
        topic_sources.update_for_owner(source_id, user_id, topics=topics)
    return source_id


def _attachments(scenario: dict[str, Any], document_id: str, source_id: str | None) -> list[dict[str, str]]:
    result = []
    for attachment in scenario.get("attachments", []):
        item = dict(attachment)
        item["id"] = document_id if item.get("kind") == "pdf" else source_id
        if item["id"]:
            result.append(item)
    return result


def _run_scenario(
    client: TestClient,
    identity: dict[str, str],
    scenario: dict[str, Any],
    generated: dict[str, list[str]],
    variants: int,
) -> dict[str, Any]:
    document_id = _upload_fixture(client, scenario)
    outline_topics = scenario.get("fixture", {}).get("outline_topics")
    source_id = (
        _make_source(client, document_id, identity["user_id"], list(outline_topics))
        if outline_topics is not None
        else None
    )
    attachments = _attachments(scenario, document_id, source_id)
    failures: list[dict[str, Any]] = []
    passed = 0
    for variant in range(variants):
        conversation_id: str | None = None
        variant_ok = True
        for turn_index, turn in enumerate(scenario["turns"]):
            goal = str(turn.get("goal") or scenario["goal"])
            messages = generated.get(goal) or []
            if variant >= len(messages):
                failures.append({
                    "goal": goal,
                    "variant": variant + 1,
                    "turn": turn_index + 1,
                    "message": None,
                    "checks": ["generator returned fewer messages than variants"],
                    "blocks": [],
                })
                variant_ok = False
                break
            message = messages[variant]
            payload: dict[str, Any] = {"message": message, "attachments": attachments}
            if conversation_id:
                payload["conversation_id"] = conversation_id
            response = client.post("/api/v1/agent/chat", json=payload)
            if response.status_code != 200:
                failures.append({
                    "goal": goal,
                    "variant": variant + 1,
                    "turn": turn_index + 1,
                    "message": message,
                    "checks": [f"HTTP {response.status_code}: {response.text[:300]}"],
                    "blocks": [],
                })
                variant_ok = False
                break
            body = response.json()
            conversation_id = body["conversation_id"]
            problems = _apply_checks(client, turn.get("checks") or {}, body.get("blocks") or [])
            if problems:
                failures.append({
                    "goal": goal,
                    "variant": variant + 1,
                    "turn": turn_index + 1,
                    "message": message,
                    "checks": problems,
                    "blocks": body.get("blocks") or [],
                })
                variant_ok = False
                break
        if variant_ok:
            passed += 1
    return {"scenario": scenario["goal"], "passed": passed, "total": variants, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", type=int, default=3)
    parser.add_argument("--goal", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scenarios = _scenarios()
    if args.goal:
        scenarios = [item for item in scenarios if item["goal"] in set(args.goal)]
    if not scenarios:
        raise SystemExit("No matching scenarios.")
    variants = max(1, min(int(args.variants), 5))
    settings = get_settings()
    settings.agent_enabled = True
    provider = _provider(settings)
    generated: dict[str, list[str]] = {}
    goals = {
        str(turn.get("goal") or scenario["goal"])
        for scenario in scenarios
        for turn in scenario["turns"]
    }
    for goal in sorted(goals):
        generated[goal] = _generate_messages(
            settings,
            provider,
            goal,
            next(
                scenario["fixture"]
                for scenario in scenarios
                if scenario["goal"] == goal or any(turn.get("goal") == goal for turn in scenario["turns"])
            ),
            variants,
        )

    with TestClient(app) as client:
        registration = client.post("/api/v1/users")
        registration.raise_for_status()
        body = registration.json()
        headers = {"Authorization": f"Bearer {body['token']}"}
        client.headers.update(headers)
        identity = {"user_id": body["user_id"], "headers": headers}
        results = [
            _run_scenario(client, identity, scenario, generated, variants)
            for scenario in scenarios
        ]

    report = {
        "provider": provider[0],
        "model": provider[1],
        "variants": variants,
        "results": results,
        "totals": {
            "passed": sum(item["passed"] for item in results),
            "total": sum(item["total"] for item in results),
        },
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["totals"]["passed"] == report["totals"]["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
