from __future__ import annotations

import json
from pathlib import Path

TOOLS = {
    "list_materials",
    "read_outline_image",
    "find_passages",
    "get_pages",
    "summarize",
    "build_export",
}
BLOCKS = {"passages", "summary", "file", "question"}
CASES_PATH = Path(__file__).parent / "agent_eval" / "cases.json"


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_agent_eval_cases_have_unique_ids_and_valid_turns():
    cases = _cases()
    case_ids = [case["case_id"] for case in cases]

    assert len(cases) >= 20
    assert len(case_ids) == len(set(case_ids))
    for case in cases:
        assert isinstance(case.get("attachments"), list)
        assert case.get("turns")
        for turn in case["turns"]:
            assert turn.get("message", "").strip()
            assert set(turn.get("expected_tools", [])) <= TOOLS
            assert set(turn.get("expected_block_types", [])) <= BLOCKS


def test_agent_eval_set_covers_the_pilot_regressions():
    by_id = {case["case_id"]: case for case in _cases()}

    followup = by_id["followup_original_passages_from_last_result"]["turns"]
    assert followup[0]["expected_tools"] == ["find_passages", "summarize"]
    assert followup[1]["expected_tools"] == []
    assert followup[1]["expected_block_types"] == ["passages"]

    page_two = by_id["page_number"]["turns"][0]
    assert page_two["message"] == "Page 2"
    assert page_two["expected_tools"] == ["get_pages"]

    export_followup = by_id["export_that_word_after_result"]["turns"]
    assert export_followup[-1]["expected_tools"] == ["build_export"]
