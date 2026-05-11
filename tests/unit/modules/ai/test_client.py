"""Unit tests for OpenAIClient using respx to mock HTTP."""

import pytest
import respx
from httpx import Response

from backend.modules.ai.client import OpenAIClient
from backend.modules.ai.exceptions import AIInvalidResponseError, AIProviderError, AIRateLimitError


@pytest.fixture
def client():
    return OpenAIClient(budget_tracker=None)


GOOD_RESPONSE = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1710000000,
    "model": "gpt-4o-mini",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"score":80,"tier":"high","reasoning":"test","recommended_action":"call"}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
}


@pytest.mark.asyncio
@respx.mock
async def test_chat_completion_success(client):
    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(200, json=GOOD_RESPONSE)
    )
    result = await client.chat_completion(
        messages=[{"role": "user", "content": "score this lead"}],
        model="gpt-4o-mini",
    )
    assert result.content == '{"score":80,"tier":"high","reasoning":"test","recommended_action":"call"}'
    assert result.model == "gpt-4o-mini"
    assert result.input_tokens == 100
    assert result.output_tokens == 30
    assert result.cost_usd > 0
    assert result.duration_ms >= 0


@pytest.mark.asyncio
@respx.mock
async def test_chat_completion_rate_limit_raises(client):
    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(429, json={"error": {"message": "rate limit"}})
    )
    with pytest.raises(AIRateLimitError):
        await client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
        )


@pytest.mark.asyncio
@respx.mock
async def test_chat_completion_server_error_raises(client):
    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(500, json={"error": {"message": "server error"}})
    )
    with pytest.raises(AIProviderError):
        await client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
        )


@pytest.mark.asyncio
@respx.mock
async def test_chat_completion_invalid_json_raises(client):
    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(200, text="not-json-at-all", headers={"content-type": "application/json"})
    )
    with pytest.raises(AIInvalidResponseError):
        await client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
        )


@pytest.mark.asyncio
@respx.mock
async def test_api_key_not_in_response_body(client):
    """Verify API key is never echoed back in any response field."""
    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(200, json=GOOD_RESPONSE)
    )
    result = await client.chat_completion(
        messages=[{"role": "user", "content": "test"}],
        model="gpt-4o-mini",
    )
    assert "test-openai-key" not in result.content
    assert "test-openai-key" not in result.model


def test_supported_models():
    models = OpenAIClient.supported_models()
    assert "gpt-4o-mini" in models
