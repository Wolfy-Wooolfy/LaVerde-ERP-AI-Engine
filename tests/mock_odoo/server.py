"""
Mock Odoo JSON-RPC server for development and testing.
Handles authenticate, search_read, read_group, search_count on crm.lead and crm.stage.

Run standalone:
    python -m tests.mock_odoo.server
    python -m tests.mock_odoo.server --scenario timeout
    python -m tests.mock_odoo.server --scenario auth_fail
    python -m tests.mock_odoo.server --scenario empty
"""

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tests.mock_odoo.fixtures import LEADS, STAGES

# Active scenario (set at startup via CLI --scenario flag)
ACTIVE_SCENARIO: str = "default"

# ── Domain evaluator ──────────────────────────────────────────────────────────


def _field_value(record: dict, field: str) -> Any:
    rv = record.get(field, False)
    if isinstance(rv, list) and len(rv) == 2:
        return rv[0]
    return rv


def _matches(record: dict, condition: Any) -> bool:
    if not isinstance(condition, list) or len(condition) != 3:
        return True  # skip operators like "&", "|"
    field, op, value = condition

    rv_raw = record.get(field, False)
    rv_id = rv_raw[0] if isinstance(rv_raw, list) and len(rv_raw) == 2 else rv_raw

    if op == "=":
        if value is False or value is None:
            return not rv_raw and rv_raw != 0
        return rv_raw == value or rv_id == value
    if op == "!=":
        if value is False or value is None:
            return bool(rv_raw) or rv_raw == 0
        return rv_raw != value and rv_id != value
    if op == "in":
        return rv_raw in value or rv_id in value
    if op == "not in":
        return rv_raw not in value and rv_id not in value
    if op == ">":
        return (rv_raw > value) if rv_raw is not False else False
    if op == "<":
        return (rv_raw < value) if rv_raw is not False else False
    return True


def filter_domain(records: list[dict], domain: list) -> list[dict]:
    """All conditions are ANDed (Odoo default, no OR used in this app)."""
    result = []
    for rec in records:
        if all(_matches(rec, c) for c in domain if isinstance(c, list)):
            result.append(rec)
    return result


# ── ORM method implementations ────────────────────────────────────────────────


def _get_dataset(model: str) -> list[dict]:
    if ACTIVE_SCENARIO == "empty":
        return []
    if model == "crm.lead":
        return LEADS
    if model == "crm.stage":
        return STAGES
    return []


def _search_read(
    model: str,
    domain: list,
    fields: list,
    limit: int,
    offset: int,
    order: str,
) -> list[dict]:
    records = filter_domain(_get_dataset(model), domain)
    if order:
        field_name = order.split()[0]
        reverse = "desc" in order.lower()
        records = sorted(records, key=lambda r: r.get(field_name) or "", reverse=reverse)
    if offset:
        records = records[offset:]
    if limit:
        records = records[:limit]
    if fields:
        return [{f: r.get(f) for f in fields if f in r} | {"id": r["id"]} for r in records]
    return records


def _read_group(
    model: str,
    domain: list,
    fields: list,
    groupby: list,
    lazy: bool = True,
) -> list[dict]:
    records = filter_domain(_get_dataset(model), domain)

    if not groupby:
        return [{"__count": len(records)}]

    groups: dict[tuple, list] = {}
    for rec in records:
        key = tuple(
            (
                rec.get(gb)[0]  # type: ignore[index]
                if isinstance(rec.get(gb), list) and len(rec.get(gb)) == 2  # type: ignore[arg-type]
                else rec.get(gb)
            )
            for gb in groupby
        )
        groups.setdefault(key, []).append(rec)

    result = []
    for _key, group_records in groups.items():
        row: dict[str, Any] = {"__count": len(group_records)}
        sample = group_records[0]
        for _i, gb in enumerate(groupby):
            row[gb] = sample.get(gb)
            if len(groupby) == 1:
                row[f"{gb}_count"] = len(group_records)
        result.append(row)

    return result


def _search_count(model: str, domain: list) -> int:
    return len(filter_domain(_get_dataset(model), domain))


# ── FastAPI app ───────────────────────────────────────────────────────────────


class RpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str = "call"
    params: dict = {}
    id: Any = 1


def create_app(scenario: str = "default") -> FastAPI:
    global ACTIVE_SCENARIO
    ACTIVE_SCENARIO = scenario

    mock = FastAPI(title="Mock Odoo JSON-RPC", docs_url=None, redoc_url=None)

    @mock.post("/jsonrpc")
    async def jsonrpc_handler(body: RpcRequest) -> JSONResponse:
        # Scenario: simulate network timeout
        if ACTIVE_SCENARIO == "timeout":
            await asyncio.sleep(35)  # longer than ODOO_TIMEOUT_SECONDS

        params = body.params
        service = params.get("service", "")
        method = params.get("method", "")
        args = params.get("args", [])

        def ok(result: Any) -> JSONResponse:
            return JSONResponse({"jsonrpc": "2.0", "id": body.id, "result": result})

        def err(msg: str) -> JSONResponse:
            return JSONResponse({"jsonrpc": "2.0", "id": body.id, "error": {"message": msg}})

        # ── common.authenticate ───────────────────────────────────────────────
        if service == "common" and method == "authenticate":
            if ACTIVE_SCENARIO == "auth_fail":
                return ok(False)
            db, username, api_key, _ = args
            if api_key == "invalid-key":
                return ok(False)
            return ok(42)

        # ── object.execute_kw ─────────────────────────────────────────────────
        if service == "object" and method == "execute_kw":
            if len(args) < 6:
                return err("Invalid execute_kw args")
            _db, uid, api_key, model, orm_method, orm_args, *rest = args
            kwargs = rest[0] if rest else {}

            if orm_method == "search_read":
                domain = orm_args[0] if orm_args else []
                fields = kwargs.get("fields", [])
                limit = kwargs.get("limit", 0)
                offset = kwargs.get("offset", 0)
                order = kwargs.get("order", "")
                return ok(_search_read(model, domain, fields, limit, offset, order))

            if orm_method == "read_group":
                domain = orm_args[0] if len(orm_args) > 0 else []
                groupby = orm_args[2] if len(orm_args) > 2 else []
                lazy = kwargs.get("lazy", True)
                return ok(_read_group(model, domain, [], groupby, lazy))

            if orm_method == "search_count":
                domain = orm_args[0] if orm_args else []
                return ok(_search_count(model, domain))

            return err(f"Unknown ORM method: {orm_method}")

        return err(f"Unknown service/method: {service}.{method}")

    return mock


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Mock Odoo JSON-RPC server")
    parser.add_argument("--port", type=int, default=8069, help="Port to listen on")
    parser.add_argument(
        "--scenario",
        choices=["default", "timeout", "auth_fail", "empty"],
        default="default",
        help="Test scenario to simulate",
    )
    parsed = parser.parse_args()

    print(
        f"Mock Odoo on http://127.0.0.1:{parsed.port}/jsonrpc"
        f"  [scenario={parsed.scenario}]  ({len(LEADS)} leads)"
    )
    uvicorn.run(
        create_app(scenario=parsed.scenario),
        host="127.0.0.1",
        port=parsed.port,
        log_level="info",
    )
