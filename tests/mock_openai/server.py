"""
Mock OpenAI server on port 9000.

Usage:
    python -m tests.mock_openai.server [--scenario success|rate_limit|timeout|invalid_json]

Set OPENAI_BASE_URL=http://localhost:9000/v1 to point the AI client here.
"""

import asyncio
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tests.mock_openai.fixtures import (
    INVALID_JSON_RESPONSE,
    RATE_LIMIT_RESPONSE,
    SERVER_ERROR_RESPONSE,
    SUCCESS_RESPONSE,
)

# Global scenario — can be changed at runtime via POST /control/scenario
_scenario = "success"

app = FastAPI(title="Mock OpenAI Server")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    global _scenario

    if _scenario == "rate_limit":
        return JSONResponse(status_code=429, content=RATE_LIMIT_RESPONSE)

    if _scenario == "timeout":
        await asyncio.sleep(60)  # simulate hanging
        return JSONResponse(status_code=200, content=SUCCESS_RESPONSE)

    if _scenario == "invalid_json":
        return JSONResponse(status_code=200, content=INVALID_JSON_RESPONSE)

    if _scenario == "server_error":
        return JSONResponse(status_code=500, content=SERVER_ERROR_RESPONSE)

    # Default: success — return deterministic response
    response = {**SUCCESS_RESPONSE, "created": int(time.time())}
    return JSONResponse(status_code=200, content=response)


@app.post("/control/scenario")
async def set_scenario(request: Request) -> JSONResponse:
    """Test helper: switch mock scenario at runtime."""
    global _scenario
    body = await request.json()
    _scenario = body.get("scenario", "success")
    return JSONResponse({"scenario": _scenario})


@app.get("/control/scenario")
async def get_scenario() -> JSONResponse:
    return JSONResponse({"scenario": _scenario})


if __name__ == "__main__":
    import uvicorn

    scenario = "success"
    for i, arg in enumerate(sys.argv):
        if arg == "--scenario" and i + 1 < len(sys.argv):
            scenario = sys.argv[i + 1]
    _scenario = scenario
    print(f"Starting mock OpenAI server on port 9000 (scenario={scenario})")
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")
