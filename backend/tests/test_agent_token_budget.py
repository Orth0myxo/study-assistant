from __future__ import annotations

import json
import math

from app.config import Settings
from app.services.agent_service import _agent_system_prompt, _agent_tools_for_chat
from app.services.agent_store import AgentConversation

CHAT_TYPES = {
    "no material attached": set(),
    "Word document only": {"docx"},
    "PDF no images": {"pdf"},
    "PDF plus image/outline": {"pdf", "image"},
}

# Measured in CI run 45138472414 as tools JSON plus system prompt characters:
# no material 261+858=1119; Word 4323+2060=6383; PDF 4698+2115=6813;
# PDF plus image 5153+2263=7416. Each ceiling is the measured value plus 10%,
# rounded up, so a change that bloats the prompt fails here with the numbers.
CEILINGS = {
    "no material attached": 1231,
    "Word document only": 7022,
    "PDF no images": 7495,
    "PDF plus image/outline": 8158,
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
            f"agent_size name={name!r} tools={tools_chars} "
            f"prompt={prompt_chars} total={fixed_chars}",
            flush=True,
        )
        ceiling = CEILINGS[name]
        assert fixed_chars <= ceiling, (
            f"{name}: {fixed_chars} characters exceeds the ceiling of {ceiling} "
            f"(tools={tools_chars}, prompt={prompt_chars}); measured baseline "
            f"plus 10%"
        )

    memory_tokens = int(Settings().agent_memory_tokens)
    equivalent_tokens = math.ceil(measured["PDF no images"] / 3) + memory_tokens
    assert equivalent_tokens <= 6000, (
        "PDF no images: "
        f"{equivalent_tokens} tokens at the conservative 3 chars/token estimate "
        f"(fixed={measured['PDF no images']}, memory={memory_tokens}) exceeds 6000"
    )
