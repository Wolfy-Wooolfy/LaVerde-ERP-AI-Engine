"""Additional coverage for OpenAIClient token counting and retry logic."""

import pytest
import respx
from httpx import Response

from backend.modules.ai.client import OpenAIClient, count_tokens
from backend.modules.ai.exceptions import AIProviderError, AITimeoutError


def test_count_tokens_returns_positive():
    n = count_tokens("Hello, this is a test message for tokenization.")
    assert n > 0


def test_count_tokens_empty_string():
    n = count_tokens("")
    assert n == 0


@pytest.fixture
def client():
    return OpenAIClient(budget_tracker=None)


GOOD_RESPONSE = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1710000000,
    "model": "gpt-4o-mini",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"score":80,"tier":"high","reasoning":"ok","recommended_action":"call"}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
}


@pytest.mark.asyncio
@respx.mock
async def test_cost_recorded_with_budget_tracker(tmp_path):
    from backend.modules.ai.budget_tracker import BudgetTracker

    bt = BudgetTracker(10.0, 0.8, budget_file=tmp_path / "b.json")
    c = OpenAIClient(budget_tracker=bt)

    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(200, json=GOOD_RESPONSE)
    )

    initial = bt.current_month_spend()
    await c.chat_completion(messages=[{"role": "user", "content": "test"}], model="gpt-4o-mini")
    assert bt.current_month_spend() > initial
    await c.close()


@pytest.mark.asyncio
@respx.mock
async def test_chat_completion_400_raises_provider_error(client):
    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(
        return_value=Response(400, json={"error": {"message": "bad request"}})
    )
    with pytest.raises(AIProviderError):
        await client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
        )


@pytest.mark.asyncio
@respx.mock
async def test_response_format_json_mode_passed(client):
    """Verify response_format is sent in payload."""
    req = None

    def capture(request, route):
        nonlocal req
        req = request
        return Response(200, json=GOOD_RESPONSE)

    respx.post("http://127.0.0.1:9000/v1/chat/completions").mock(side_effect=capture)
    await client.chat_completion(
        messages=[{"role": "user", "content": "test"}],
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
    )
    import json
    body = json.loads(req.content)
    assert body.get("response_format") == {"type": "json_object"}
