"""Deterministic mock responses for the OpenAI mock server."""

import time

SUCCESS_RESPONSE = {
    "id": "chatcmpl-test123",
    "object": "chat.completion",
    "created": int(time.time()),
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": '{"score": 75, "tier": "high", "reasoning": "Overdue in established stage with contact info.", "recommended_action": "Call immediately"}',
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
}

RATE_LIMIT_RESPONSE = {
    "error": {
        "message": "Rate limit reached",
        "type": "requests",
        "code": "rate_limit_exceeded",
    }
}

INVALID_JSON_RESPONSE = {
    "id": "chatcmpl-badjson",
    "object": "chat.completion",
    "created": int(time.time()),
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "This is not JSON at all!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
}

SERVER_ERROR_RESPONSE = {
    "error": {"message": "Internal server error", "type": "server_error", "code": "internal_error"}
}
