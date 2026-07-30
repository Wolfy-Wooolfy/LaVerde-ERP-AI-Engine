"""
Unit tests for the Projects Inventory contracts PIPELINE service.

Odoo is fully mocked by a dispatch fake that honours the search_read DOMAIN (including
the dotted `field_id.name` / `field_id.model` paths the real server resolves), the
paging kwargs and the requested FIELDS — so the tests prove the service filters state
changes SERVER-side on the technical field name rather than in Python.

Covers grouping over all 6 non-cancel states, day arithmetic on both the chatter and
the create_date paths with an injected `today`, latest-tracking-wins, write_date never
being consulted, sort order on both lists, counts-only for confirm/delivered, the
reconciliation and unknown-state raises, the empty funnel, caching by call count, the
read-only guard firing before any RPC, RPC-failure wrapping and the schema round-trip.

Live verification: the Commit 3 smoke against the real get_contracts_pipeline().
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.projects_inventory.domain import (
    PIPELINE_REVIEW_STAGES,
    PIPELINE_STAGE_STATES,
)
from backend.modules.projects_inventory.schemas import ContractsPipeline
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services import inventory_service
from backend.modules.projects_inventory.services import pipeline_service
from backend.modules.projects_inventory.services.inventory_service import (
    UnknownContractStateError,
)
from backend.modules.projects_inventory.services.pipeline_service import (
    _cairo_date,
    get_contracts_pipeline,
)

_CONTRACT_MODEL = "rs.contract"
_MESSAGE_MODEL = "mail.message"
_TRACKING_MODEL = "mail.tracking.value"

# Every day figure below is measured against this injected Cairo "today".
_TODAY = date(2026, 7, 30)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


def _c(cid: int, state: str, unit=None, create_date: str = "2026-05-01 08:00:00",
       write_date: str = "2026-06-09 12:56:46", name: str = "") -> dict:
    """One rs.contract row. `write_date` is present in the FIXTURE precisely so the
    tests can prove the service never asks for it."""
    return {
        "id": cid,
        "name": name or f"C{cid:05d}",
        "state": state,
        "unit_id": list(unit) if unit else [100 + cid, f"Unit#U{cid}"],
        "create_date": create_date,
        "write_date": write_date,
    }


def _msg(mid: int, res_id: int, dt: str) -> dict:
    """One mail.message row on rs.contract."""
    return {"id": mid, "model": _CONTRACT_MODEL, "res_id": res_id, "date": dt}


def _tv(message_id: int, field_name: str = "state",
        field_model: str = _CONTRACT_MODEL, tid: int = 0) -> dict:
    """One mail.tracking.value row. The dotted keys mirror what the real server
    resolves for a `field_id.name` / `field_id.model` domain, so the fake can filter
    on exactly what the service pushes server-side."""
    return {
        "id": tid or 5000 + message_id,
        "mail_message_id": [message_id, "message"],
        "field_id.name": field_name,
        "field_id.model": field_model,
    }


def _cmp_value(raw):
    """Odoo domains match on the STORED value: a many2one compares as its bare id, even
    though search_read renders it as [id, name]. The fake must do the same or a
    ("mail_message_id", "in", [...]) domain would never match a rendered pair."""
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return raw[0]
    return raw


def _apply_domain(rows: list[dict], domain: list) -> list[dict]:
    """Evaluate the domain grammar the service actually uses."""
    out = list(rows)
    for field, op, value in domain:
        if op == "=":
            out = [r for r in out if _cmp_value(r.get(field)) == value]
        elif op == "!=":
            out = [r for r in out if _cmp_value(r.get(field)) != value]
        elif op == "in":
            out = [r for r in out if _cmp_value(r.get(field)) in value]
        else:  # pragma: no cover — a new operator must be taught to the fake
            raise AssertionError(f"unsupported domain operator {op!r}")
    return out


def _make_client(contracts=(), messages=(), tracking=()):
    store = {
        _CONTRACT_MODEL: list(contracts),
        _MESSAGE_MODEL: list(messages),
        _TRACKING_MODEL: list(tracking),
    }

    def _dispatch(model, method, args=None, kwargs=None):
        if model not in store or method != "search_read":
            raise AssertionError(f"unexpected RPC: {model}.{method}")
        kwargs = kwargs or {}
        rows = _apply_domain(store[model], (args or [[]])[0])
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit")
        rows = rows[offset:offset + limit] if limit else rows[offset:]
        fields = kwargs.get("fields")
        if fields:
            rows = [{k: v for k, v in r.items() if k in fields} for r in rows]
        return rows

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _calls_for(client, model: str) -> list:
    return [c for c in client.execute_kw.await_args_list if c.args[0] == model]


def _dataset() -> tuple[list[dict], list[dict], list[dict]]:
    """8 non-cancel contracts spanning every non-cancel state.

      c1 draft       create 2026-05-01, no state change            -> 90 days
      c2 draft       create 2026-07-01, no state change            -> 29 days
      c3 finance     create 2026-01-12, state changes Mar 1 + Jun 15 -> 45 days (latest)
      c4 legal       create 2026-02-01, state change Jul 20        -> 10 days
      c5 engineering create 2026-06-30, no state change            -> 30 days
      c6 confirm     )
      c7 confirm     ) counts only — their chatter is never read
      c8 delivered   )

    A cancelled contract is in the fixture too and must be excluded SERVER-side.
    c1 also carries a non-state (sales_price) tracking row and a tracking-free
    message, neither of which may be mistaken for a stage entry.
    """
    contracts = [
        _c(1, "draft", create_date="2026-05-01 08:00:00"),
        _c(2, "draft", create_date="2026-07-01 08:00:00"),
        _c(3, "finance", create_date="2026-01-12 09:27:13"),
        _c(4, "legal", create_date="2026-02-01 00:00:00"),
        _c(5, "engineering", create_date="2026-06-30 08:00:00"),
        _c(6, "confirm"), _c(7, "confirm"), _c(8, "delivered"),
        _c(9, "cancel"),                       # excluded by the query domain
    ]
    messages = [
        _msg(199, 1, "2026-07-29 10:00:00"),   # no tracking rows at all
        _msg(200, 1, "2026-07-28 10:00:00"),   # sales_price change, NOT a stage entry
        _msg(201, 3, "2026-03-01 10:00:00"),   # earlier state change
        _msg(202, 3, "2026-06-15 10:00:00"),   # LATEST state change -> wins
        _msg(203, 4, "2026-07-20 10:00:00"),
        _msg(204, 6, "2026-07-25 10:00:00"),   # on a confirm contract — never fetched
    ]
    tracking = [
        _tv(200, field_name="sales_price"),
        _tv(201), _tv(202), _tv(203),
        _tv(204),
    ]
    return contracts, messages, tracking


def _by_id(entries: list[dict]) -> dict[int, dict]:
    return {e["contract_id"]: e for e in entries}


# ══════════════════════════════════════════════════════════════════════════════
# Grouping
# ══════════════════════════════════════════════════════════════════════════════


async def test_grouping_splits_all_six_non_cancel_states():
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)

    assert [e["contract_id"] for e in res["awaiting_action"]] == [1, 2]
    assert {e["contract_id"] for e in res["under_review"]} == {3, 4, 5}
    assert res["awaiting_action_count"] == 2
    assert res["under_review_count"] == 3
    assert res["confirmed_count"] == 2
    assert res["delivered_count"] == 1
    assert res["total_non_cancel"] == 8


async def test_cancelled_contract_excluded_server_side():
    """The cancel row is in the fixture but the query domain removes it, so it is
    neither grouped nor counted in total_non_cancel."""
    client = _make_client(*_dataset())
    res = await get_contracts_pipeline(client=client, today=_TODAY)
    assert res["total_non_cancel"] == 8          # 9 rows in the fixture, 1 cancelled
    calls = _calls_for(client, _CONTRACT_MODEL)
    assert calls[0].kwargs["args"] == [[("state", "!=", "cancel")]]
    all_ids = ({e["contract_id"] for e in res["awaiting_action"]}
               | {e["contract_id"] for e in res["under_review"]})
    assert 9 not in all_ids


async def test_awaiting_action_entry_shape_has_no_stage():
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    e = _by_id(res["awaiting_action"])[1]
    assert set(e) == {"contract_id", "name", "unit_id", "unit_name",
                      "days_in_stage", "stage", "stage_label"}
    assert e["name"] == "C00001"
    assert e["unit_id"] == 101
    assert e["unit_name"] == "Unit#U1"
    # A draft sits at no named desk.
    assert e["stage"] is None and e["stage_label"] is None


async def test_under_review_entries_carry_technical_stage_and_human_label():
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    by_id = _by_id(res["under_review"])
    assert (by_id[3]["stage"], by_id[3]["stage_label"]) == ("finance", "Finance Review")
    assert (by_id[4]["stage"], by_id[4]["stage_label"]) == ("legal", "Legal Review")
    assert (by_id[5]["stage"], by_id[5]["stage_label"]) == (
        "engineering", "Engineering Review")
    # The labels come from the domain map, not from a copy in the service.
    for e in res["under_review"]:
        assert e["stage_label"] == PIPELINE_REVIEW_STAGES[e["stage"]]


async def test_confirmed_and_delivered_are_counts_only():
    """They have left the funnel: no row list anywhere carries them."""
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    listed = ({e["contract_id"] for e in res["awaiting_action"]}
              | {e["contract_id"] for e in res["under_review"]})
    assert listed.isdisjoint({6, 7, 8})
    assert res["confirmed_count"] == 2
    assert res["delivered_count"] == 1


async def test_unit_m2o_absent_degrades_to_zero_and_dash():
    contracts = [{"id": 1, "name": "C1", "state": "draft", "unit_id": False,
                  "create_date": "2026-07-30 08:00:00", "write_date": "x"}]
    res = await get_contracts_pipeline(client=_make_client(contracts), today=_TODAY)
    e = res["awaiting_action"][0]
    assert e["unit_id"] == 0 and e["unit_name"] == "—"


# ══════════════════════════════════════════════════════════════════════════════
# days_in_stage
# ══════════════════════════════════════════════════════════════════════════════


async def test_days_from_create_date_when_state_never_changed():
    """The NORMAL path — a draft has never left its opening stage."""
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    by_id = _by_id(res["awaiting_action"])
    assert by_id[1]["days_in_stage"] == 90    # 2026-05-01 -> 2026-07-30
    assert by_id[2]["days_in_stage"] == 29    # 2026-07-01 -> 2026-07-30
    # ...and for an under-review contract with no tracking row either.
    assert _by_id(res["under_review"])[5]["days_in_stage"] == 30   # 2026-06-30


async def test_days_from_chatter_state_change_when_present():
    """c4 was created 2026-02-01 but entered legal on 2026-07-20 — the chatter date
    wins, so the age is 10 days, not 179."""
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    assert _by_id(res["under_review"])[4]["days_in_stage"] == 10


async def test_latest_state_change_wins_over_earlier_ones():
    """c3 has two state changes (Mar 1 and Jun 15). The LATEST dates the stage."""
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    assert _by_id(res["under_review"])[3]["days_in_stage"] == 45   # 2026-06-15
    # Not the earlier change (151 days) and not create_date (199 days).
    assert _by_id(res["under_review"])[3]["days_in_stage"] not in (151, 199)


async def test_non_state_tracking_row_is_not_a_stage_entry():
    """c1's only tracking row is a sales_price change on 2026-07-28. If it counted, c1
    would read 2 days; it must still read 90 from create_date."""
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    assert _by_id(res["awaiting_action"])[1]["days_in_stage"] == 90


async def test_state_filter_is_pushed_server_side_on_technical_field_name():
    client = _make_client(*_dataset())
    await get_contracts_pipeline(client=client, today=_TODAY)
    calls = _calls_for(client, _TRACKING_MODEL)
    assert len(calls) == 1
    domain = calls[0].kwargs["args"][0]
    assert ("field_id.name", "=", "state") in domain
    assert ("field_id.model", "=", "rs.contract") in domain
    assert calls[0].kwargs["kwargs"]["fields"] == ["mail_message_id"]


async def test_write_date_is_never_requested_or_consulted():
    """31 of the 33 live pipeline contracts share one bulk-edit write_date, so it is
    rejected as a signal. Here every fixture contract carries a write_date far newer
    than its create_date — if it leaked in, c1 would read 51 days, not 90."""
    client = _make_client(*_dataset())
    res = await get_contracts_pipeline(client=client, today=_TODAY)
    for call in client.execute_kw.await_args_list:
        assert "write_date" not in (call.kwargs["kwargs"].get("fields") or []), call.args
    assert _by_id(res["awaiting_action"])[1]["days_in_stage"] == 90


async def test_days_are_cairo_local_not_utc():
    """A 22:30 UTC stamp is already the NEXT Cairo day (UTC+3 in July), so a contract
    created then is 0 days old on that Cairo date — not 1."""
    contracts = [_c(1, "draft", create_date="2026-07-29 22:30:00")]
    res = await get_contracts_pipeline(client=_make_client(contracts), today=_TODAY)
    assert res["awaiting_action"][0]["days_in_stage"] == 0
    # The helper itself: the same instant is 2026-07-30 in Cairo.
    assert _cairo_date("2026-07-29 22:30:00") == date(2026, 7, 30)
    assert _cairo_date("2026-07-29 20:30:00") == date(2026, 7, 29)


async def test_undatable_and_future_stamps_floor_at_zero():
    contracts = [
        _c(1, "draft", create_date=""),                      # no create_date at all
        _c(2, "draft", create_date="2026-09-01 08:00:00"),   # future -> clock skew
    ]
    res = await get_contracts_pipeline(client=_make_client(contracts), today=_TODAY)
    assert {e["contract_id"]: e["days_in_stage"] for e in res["awaiting_action"]} == {
        1: 0, 2: 0
    }


# ══════════════════════════════════════════════════════════════════════════════
# Sort order
# ══════════════════════════════════════════════════════════════════════════════


async def test_both_lists_sorted_by_days_in_stage_desc():
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    aw = [e["days_in_stage"] for e in res["awaiting_action"]]
    ur = [e["days_in_stage"] for e in res["under_review"]]
    assert aw == [90, 29] == sorted(aw, reverse=True)
    assert ur == [45, 30, 10] == sorted(ur, reverse=True)
    # The oldest deal is the one the board must act on first.
    assert res["awaiting_action"][0]["contract_id"] == 1
    assert res["under_review"][0]["contract_id"] == 3


async def test_sort_ties_break_deterministically_on_contract_id():
    contracts = [_c(cid, "draft", create_date="2026-07-01 08:00:00")
                 for cid in (7, 3, 5)]
    res = await get_contracts_pipeline(client=_make_client(contracts), today=_TODAY)
    assert [e["days_in_stage"] for e in res["awaiting_action"]] == [29, 29, 29]
    assert [e["contract_id"] for e in res["awaiting_action"]] == [3, 5, 7]


# ══════════════════════════════════════════════════════════════════════════════
# Empty funnel + RPC economy
# ══════════════════════════════════════════════════════════════════════════════


async def test_empty_pipeline_returns_empty_lists_with_correct_counts():
    """All confirm/delivered: empty lists, counts intact — and the chatter is never
    touched, so it costs ONE round-trip, not three."""
    contracts = [_c(1, "confirm"), _c(2, "confirm"), _c(3, "delivered")]
    client = _make_client(contracts, *_dataset()[1:])
    res = await get_contracts_pipeline(client=client, today=_TODAY)
    assert res["awaiting_action"] == [] and res["under_review"] == []
    assert res["awaiting_action_count"] == 0 and res["under_review_count"] == 0
    assert res["confirmed_count"] == 2
    assert res["delivered_count"] == 1
    assert res["total_non_cancel"] == 3
    assert client.execute_kw.await_count == 1
    assert _calls_for(client, _MESSAGE_MODEL) == []
    assert _calls_for(client, _TRACKING_MODEL) == []


async def test_cold_cache_issues_exactly_three_fetch_groups():
    client = _make_client(*_dataset())
    await get_contracts_pipeline(client=client, today=_TODAY)
    assert client.execute_kw.await_count == 3
    assert [c.args[0] for c in client.execute_kw.await_args_list] == [
        _CONTRACT_MODEL, _MESSAGE_MODEL, _TRACKING_MODEL
    ]
    assert {c.args[1] for c in client.execute_kw.await_args_list} == {"search_read"}


async def test_message_fetch_scoped_to_in_funnel_contracts_only():
    """confirm/delivered contracts are counts — reading their chatter would be waste."""
    client = _make_client(*_dataset())
    await get_contracts_pipeline(client=client, today=_TODAY)
    calls = _calls_for(client, _MESSAGE_MODEL)
    assert len(calls) == 1
    domain = calls[0].kwargs["args"][0]
    assert ("model", "=", "rs.contract") in domain
    res_ids = next(v for f, op, v in domain if f == "res_id" and op == "in")
    assert sorted(res_ids) == [1, 2, 3, 4, 5]


async def test_no_tracking_fetch_when_contracts_have_no_messages():
    """Nothing to correlate → the third group is skipped."""
    contracts = [_c(1, "draft")]
    client = _make_client(contracts, [], [])
    res = await get_contracts_pipeline(client=client, today=_TODAY)
    assert res["awaiting_action_count"] == 1
    assert client.execute_kw.await_count == 2
    assert _calls_for(client, _TRACKING_MODEL) == []


# ══════════════════════════════════════════════════════════════════════════════
# Caching + guards
# ══════════════════════════════════════════════════════════════════════════════


async def test_cache_hit_on_second_call():
    client = _make_client(*_dataset())
    first = await get_contracts_pipeline(client=client, today=_TODAY)
    assert first["cache_status"] == "fresh"
    second = await get_contracts_pipeline(client=client, today=_TODAY)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    # Still only the ONE cold round of 3 fetches despite two calls.
    assert client.execute_kw.await_count == 3
    assert second["awaiting_action"] == first["awaiting_action"]
    assert second["total_non_cancel"] == first["total_non_cancel"]


async def test_read_only_violation_raises_before_any_rpc(monkeypatch):
    """The guard reads ALLOWED_METHODS in inventory_service's namespace, where
    _assert_read_only is defined."""
    monkeypatch.setattr(
        inventory_service, "ALLOWED_METHODS", frozenset({"search_read", "write"})
    )
    client = _make_client(*_dataset())
    with pytest.raises(ReadOnlyViolationError, match="write"):
        await get_contracts_pipeline(client=client, today=_TODAY)
    assert client.execute_kw.await_count == 0


async def test_rpc_failure_wrapped_as_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_contracts_pipeline(client=client, today=_TODAY)


async def test_unknown_non_cancel_state_raises_naming_the_state():
    """Same strictness axis as the bucket classifier: an unplaceable non-cancel state
    is a loud, named error listing every offender with its count."""
    contracts = [_c(1, "draft"), _c(2, "arbitration"), _c(3, "arbitration")]
    with pytest.raises(UnknownContractStateError) as exc:
        await get_contracts_pipeline(client=_make_client(contracts), today=_TODAY)
    message = str(exc.value)
    assert "arbitration" in message
    assert "'arbitration': 2" in message
    assert isinstance(exc.value, RuntimeError)


async def test_reconciliation_raises_when_groups_do_not_sum(monkeypatch):
    """A defensive invariant: if grouping ever dropped a contract silently, the service
    must refuse to return rather than under-report the funnel."""
    monkeypatch.setattr(
        pipeline_service, "_group_contracts", lambda *a, **k: ([], [], 0, 0)
    )
    contracts = [_c(1, "draft"), _c(2, "confirm"), _c(3, "delivered")]
    with pytest.raises(RuntimeError, match="Pipeline reconciliation FAILED"):
        await get_contracts_pipeline(client=_make_client(contracts), today=_TODAY)


async def test_stage_vocabulary_covers_the_live_non_cancel_states():
    """draft + the three review desks are the in-funnel states; confirm/delivered are
    counted separately. Together they exhaust the 6 non-cancel rs.contract states."""
    assert set(PIPELINE_STAGE_STATES) == {"draft", "legal", "finance", "engineering"}
    assert set(PIPELINE_REVIEW_STAGES) == {"legal", "finance", "engineering"}
    assert "cancel" not in PIPELINE_STAGE_STATES


# ══════════════════════════════════════════════════════════════════════════════
# Schema round-trip
# ══════════════════════════════════════════════════════════════════════════════


async def test_service_output_validates_against_schema():
    res = await get_contracts_pipeline(client=_make_client(*_dataset()), today=_TODAY)
    model = ContractsPipeline.model_validate(res)
    assert model.total_non_cancel == 8
    assert model.awaiting_action_count == len(model.awaiting_action) == 2
    assert model.under_review_count == len(model.under_review) == 3
    # A draft entry validates with a null stage; a review entry with a typed one.
    assert model.awaiting_action[0].stage is None
    assert {e.stage for e in model.under_review} == {"finance", "legal", "engineering"}
    assert model.reference_date == "2026-07-30"
