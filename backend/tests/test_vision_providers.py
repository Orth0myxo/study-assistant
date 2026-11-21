from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.ai_client import AiError
from app.services.vision_providers import (
    _GROQ_VISION_RESERVATIONS,
    GroqVisionProvider,
)


@pytest.fixture(autouse=True)
def _reset_groq_vision_budget():
    _GROQ_VISION_RESERVATIONS.clear()
    yield
    _GROQ_VISION_RESERVATIONS.clear()


def _settings(**overrides) -> Settings:
    return Settings(
        groq_api_key="test-key",
        groq_vision_model="qwen/test-vision-model",
        groq_base_url="https://groq.test/v1",
        **overrides,
    )


def test_groq_vision_sends_one_base64_data_url_with_env_model():
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"text": "Page words"})}}
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    reader = GroqVisionProvider(_settings(), http_client=client)
    try:
        assert reader.read_page_image(b"png-bytes", "image/png") == "Page words"
    finally:
        reader.close()
        client.close()

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["model"] == "qwen/test-vision-model"
    assert payload["max_completion_tokens"] == 1024
    content = payload["messages"][0]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 1
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_groq_vision_classifies_model_without_image_support():
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "This model does not support image inputs"}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    reader = GroqVisionProvider(_settings(), http_client=client)
    try:
        with pytest.raises(AiError) as error:
            reader.read_page_image(b"image", "image/png")
    finally:
        reader.close()
        client.close()
    assert error.value.code == "vision_model_unsupported"


def test_groq_vision_classifies_rate_limit():
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "5"}, json={"error": {}})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    reader = GroqVisionProvider(_settings(), http_client=client)
    try:
        with pytest.raises(AiError) as error:
            reader.read_page_image(b"image", "image/png")
    finally:
        reader.close()
        client.close()
    assert error.value.code == "vision_rate_limited"
    assert error.value.retry_after == 5


def test_groq_vision_rejects_request_image_over_encoded_payload_budget():
    reader = GroqVisionProvider(_settings(), http_client=httpx.Client())
    try:
        with pytest.raises(AiError) as error:
            reader.read_page_image(b"x" * (14 * 1024 * 1024 + 1), "image/png")
    finally:
        reader.close()
    assert error.value.code == "vision_image_too_large"
