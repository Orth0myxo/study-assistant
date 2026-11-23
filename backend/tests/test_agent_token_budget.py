from __future__ import annotations

import json

from app.config import Settings
from app.services.agent_service import _agent_system_prompt, _agent_tools_for_chat
from app.services.agent_store import AgentConversation

CHAT_TYPES = {
    "no material attached": set(),
    "Word document only": {"docx"},
    "PDF no images": {"pdf"},
    "PDF plus image/outline": {"pdf", "image"},
}


def _sizes(kinds: set[str]) -> tuple[int, int]:
    settings = Settings()
    tools = _agent_tools_for_chat(
        [],
        AgentConversation(conversation_id="c", owner_id="o"),
        settings,
        material_kinds=kinds,
    )
    prompt = _agent_system_prompt(
        settings,
        {tool["function"]["name"] for tool in tools},
    )
    return len(json.dumps(tools, ensure_ascii=False)), len(prompt)


def test_agent_request_sizes_stay_within_measured_ceilings():
    measured = {}
    for name, kinds in CHAT_TYPES.items():
        tools_chars, prompt_chars = _sizes(kinds)
        fixed_chars = tools_chars + prompt_chars
        measured[name] = fixed_chars
        print(
            f"agent_token_budget chat={name!r} tools_chars={tools_chars} "
            f"prompt_chars={prompt_chars} fixed_chars={fixed_chars}"
        )
        assert fixed_chars <= 20000, (
            f"{name}: {fixed_chars} characters exceeds the measured ceiling "
            f"(tools={tools_chars}, prompt={prompt_chars})"
        )

    memory_tokens = int(Settings().agent_memory_tokens)
    equivalent_chars = measured["PDF no images"] // 3 + memory_tokens
    print(
        f"agent_token_budget pdf_chars_per_3={equivalent_chars} "
        f"memory_tokens={memory_tokens}"
    )
    assert equivalent_chars <= 6000
