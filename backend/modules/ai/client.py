"""Async OpenAI client — direct httpx calls, no openai SDK dependency."""

from __future__ import annotations

import time
from typing import Any

import httpx
import tiktoken
from loguru import logger
from tenacity import AsyncRetrying, RetryError, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.core.config import settings
from backend.modules.ai.budget_tracker import PRICING, BudgetTracker, calculate_cost
from backend.modules.ai.exceptions import AIInvalidResponseError, AIProviderError, AIRateLimitError, AITimeoutError
from backend.modules.ai.schemas import ChatCompletionResponse

# Singleton encoder (gpt-4o-mini uses cl100k_base)
_ENCODER: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        try:
            _ENCODER = tiktoken.encoding_for_model("gpt-4o-mini")
        except Exception:
            _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_tokens(text: str) -> int:
    try:
        return len(_get_encoder().encode(text))
    except Exception:
        return len(text) // 4  # rough fallback


class OpenAIClient:
    """Thin async wrapper around OpenAI chat completions API."""

    def __init__(self, budget_tracker: BudgetTracker | None = None) -> None:
        self._base_url = settings.OPENAI_BASE_URL.rstrip("/")
        self._api_key = settings.OPENAI_API_KEY
        self._timeout = settings.AI_REQUEST_TIMEOUT_SECONDS
        self._max_retries = settings.AI_MAX_RETRIES
        self._budget = budget_tracker
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            limits=httpx.Limits(max_connections=10),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 500,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        start = time.monotonic()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries + 1),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type((AIProviderError, httpx.TransportError)),
                reraise=True,
            ):
                with attempt:
                    result = await self._do_request(headers, payload)
        except RetryError as exc:
            raise AIProviderError(f"All retry attempts exhausted: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)

        input_tokens = result.get("usage", {}).get("prompt_tokens", 0)
        output_tokens = result.get("usage", {}).get("completion_tokens", 0)
        content = result["choices"][0]["message"]["content"]
        actual_model = result.get("model", model)

        cost = calculate_cost(model, input_tokens, output_tokens)

        if self._budget:
            self._budget.record_spend(cost, model)

        logger.info(
            f"OpenAI call | model={actual_model} input={input_tokens} "
            f"output={output_tokens} cost=${cost:.6f} duration={duration_ms}ms"
        )

        return ChatCompletionResponse(
            content=content,
            model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
        )

    async def _do_request(self, headers: dict, payload: dict) -> dict:
        url = f"{self._base_url}/chat/completions"
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise AITimeoutError(f"OpenAI request timed out after {self._timeout}s") from exc
        except httpx.TransportError as exc:
            raise AIProviderError(f"HTTP transport error: {exc}") from exc

        if resp.status_code == 429:
            raise AIRateLimitError("OpenAI rate limit exceeded")
        if resp.status_code >= 500:
            raise AIProviderError(f"OpenAI server error: {resp.status_code}")
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise AIProviderError(f"OpenAI client error {resp.status_code}: {body}")

        try:
            return resp.json()  # type: ignore[no-any-return]
        except Exception as exc:
            raise AIInvalidResponseError(f"Could not parse OpenAI JSON: {exc}") from exc

    # ── Pricing helper (for tests / external use) ─────────────────────────────

    @staticmethod
    def supported_models() -> list[str]:
        return list(PRICING.keys())
