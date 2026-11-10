from __future__ import annotations

import json

from app.services.ai_client import AiRateLimited
from app.services.chat_command import CommandContext, interpret_message, validate_command
from app.services.provider_settings import ProviderSettings


class FakeGroq:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        if self.error:
            raise self.error
        return self.reply


CTX = CommandContext(document_names=["notes.pdf"], page_count=40, doc_format="pdf")
PROVIDER = ProviderSettings(groq_api_key="k")


def test_valid_pages_command():
    fake = FakeGroq(json.dumps({
        "action": "extract",
        "selector": {"type": "pages", "pages": "12-15"},
        "mode": "both",
    }))
    cmd = interpret_message(client=fake, provider=PROVIDER, message="pages 12 to 15 with summary", context=CTX)
    assert cmd and cmd.selector.pages == "12-15" and cmd.mode == "both"
    assert fake.calls[0][2]["json_mode"] is True


def test_only_message_and_facts_are_sent():
    fake = FakeGroq('{"action":"help"}')
    interpret_message(client=fake, provider=PROVIDER, message="x" * 2000, context=CTX)
    user = fake.calls[0][1]
    assert user.count("x") <= PROVIDER.command_max_message_chars


def test_invalid_output_is_discarded():
    for reply in ("not json", '{"action":"delete_everything"}',
                  '{"action":"extract","selector":{"type":"pages"}}',
                  '{"action":"extract","selector":{"type":"pages","pages":"1"},"junk":1}'):
        fake = FakeGroq(reply)
        assert interpret_message(client=fake, provider=PROVIDER, message="hi", context=CTX) is None


def test_provider_error_returns_none():
    fake = FakeGroq(error=AiRateLimited("limit", code="ai_rate_limited", status_code=429, retryable=False))
    assert interpret_message(client=fake, provider=PROVIDER, message="hi", context=CTX) is None


def test_no_document_asks_to_upload():
    cmd = validate_command(
        {"action": "extract", "selector": {"type": "whole"}}, has_document=False
    )
    assert cmd.action == "clarify"
