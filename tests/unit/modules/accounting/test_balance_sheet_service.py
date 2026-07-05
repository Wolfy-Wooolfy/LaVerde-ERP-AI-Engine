"""
Unit tests for the Accounting Balance Sheet service (Module 4 · Phase 1).

OdooClient is fully mocked via a dispatch keyed on (model, method) — house
keyed-dispatch pattern, order-independent. No Odoo connection, no cache
(the service is deliberately uncached in this phase — Decision M4.3).

Coverage map (spec §4.5):
  1. internal_group beats code prefix
  2. sign conventions per group
  3. section totals = Σ member balances; subgroup math
  4. unallocated_result + balanced/difference (balanced AND unbalanced)
  5. zero-balance accounts omitted from lists; all-zero subgroups omitted
  6. unmapped displayed account_type → raises naming value(s)
  7. line referencing unknown account id → raises
  8. off-balance internal_group excluded + reported
  9. 2-decimal rounding at serialization only
 10. deterministic sorting (subgroups and accounts)
 (+ contract shape, posted-domain lock, OdooQueryError wrapping,
    label-map completeness, read-only guard)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.accounting.services.balance_sheet_service import (
    ACCOUNT_TYPE_LABELS_AR,
    BANNER_AR,
    SECTION_LABELS_AR,
    BalanceSheetIntegrityError,
    get_balance_sheet,
)
from backend.shared.odoo.client import ALLOWED_METHODS

# ── Fixture builders ──────────────────────────────────────────────────────────


def _acct(acct_id: int, code: str, account_type: str, internal_group: str, name: str | None = None) -> dict:
    return {
        "id": acct_id,
        "code": code,
        "name": name or f"حساب {acct_id}",
        "account_type": account_type,
        "internal_group": internal_group,
    }


def _grp(acct_id: int, debit: float, credit: float) -> dict:
    """One read_group row: per-account posted debit/credit sums."""
    return {"account_id": [acct_id, f"acct {acct_id}"], "debit": debit, "credit": credit, "__count": 1}


def _make_client(accounts: list[dict], line_groups: list[dict]) -> MagicMock:
    """Dispatch mock keyed on (model, method) — order-independent."""
    client = MagicMock()

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "account.account" and method == "search_read":
            return accounts
        if model == "account.move.line" and method == "read_group":
            return line_groups
        raise AssertionError(
            f"unexpected RPC {model}.{method} — the service must issue exactly "
            "the two known read-only calls"
        )

    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _section(result: dict, group: str) -> dict:
    return next(s for s in result["sections"] if s["group"] == group)


def _subgroup(result: dict, group: str, account_type: str) -> dict | None:
    return next(
        (sg for sg in _section(result, group)["subgroups"] if sg["account_type"] == account_type),
        None,
    )


# Balanced fixture: assets 1000 == liabilities 300 + equity 630 + result 70.
_BALANCED_ACCOUNTS = [
    _acct(1, "10001000", "asset_cash", "asset"),
    _acct(2, "30001000", "liability_current", "liability"),
    _acct(3, "40001000", "equity", "equity"),
    _acct(4, "50001000", "income", "income"),
    _acct(5, "60001000", "expense", "expense"),
]
_BALANCED_GROUPS = [
    _grp(1, 1000.0, 0.0),   # asset  → +1000
    _grp(2, 0.0, 300.0),    # liab   → +300
    _grp(3, 0.0, 630.0),    # equity → +630
    _grp(4, 50.0, 200.0),   # income  → credit−debit = +150
    _grp(5, 100.0, 20.0),   # expense → credit−debit = −80
]


# ── 0. Contract shape ─────────────────────────────────────────────────────────


async def test_happy_path_contract_shape() -> None:
    result = await get_balance_sheet(client=_make_client(_BALANCED_ACCOUNTS, _BALANCED_GROUPS))

    assert set(result.keys()) == {
        "generated_at", "currency", "banner_ar", "totals",
        "excluded_off_balance", "sections", "rpc_duration_ms",
    }
    assert "cache_status" not in result  # no caching in this phase (M4.3)
    assert result["currency"] == "EGP"
    assert result["banner_ar"] == BANNER_AR
    assert isinstance(result["rpc_duration_ms"], int) and result["rpc_duration_ms"] >= 0
    # Africa/Cairo offset — +03:00 in DST (Apr–Oct), +02:00 otherwise.
    assert result["generated_at"].endswith(("+02:00", "+03:00"))
    # Sections: fixed order with Arabic labels.
    assert [s["group"] for s in result["sections"]] == ["asset", "liability", "equity"]
    assert [s["label_ar"] for s in result["sections"]] == [
        SECTION_LABELS_AR["asset"], SECTION_LABELS_AR["liability"], SECTION_LABELS_AR["equity"],
    ]
    assert set(result["totals"].keys()) == {
        "assets", "liabilities", "equity", "unallocated_result",
        "liabilities_plus_equity_plus_result", "difference", "balanced",
    }


async def test_rpc_arguments_locked_to_spec() -> None:
    """Hard-constraint lock: amounts ONLY from posted account.move.line
    debit/credit; accounts fetched with the 5 spec fields."""
    client = _make_client(_BALANCED_ACCOUNTS, _BALANCED_GROUPS)
    await get_balance_sheet(client=client)

    calls = {c.args[:2]: c for c in client.execute_kw.call_args_list}
    acct_call = calls[("account.account", "search_read")]
    assert acct_call.kwargs["kwargs"]["fields"] == ["id", "code", "name", "account_type", "internal_group"]

    line_call = calls[("account.move.line", "read_group")]
    domain, fields, groupby = line_call.kwargs["args"]
    assert domain == [("parent_state", "=", "posted")]
    assert fields == ["debit", "credit"]
    assert groupby == ["account_id"]


# ── 1. internal_group beats code prefix ───────────────────────────────────────


async def test_internal_group_beats_code_prefix() -> None:
    """An account coded '2xxxxxxx' (liability-looking prefix) with
    internal_group='asset' MUST land under assets — the 20002000 case."""
    accounts = [
        _acct(10, "20002000", "asset_current", "asset"),
        _acct(11, "20003000", "liability_current", "liability"),
    ]
    groups = [_grp(10, 500.0, 0.0), _grp(11, 0.0, 500.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    asset_codes = [
        a["code"]
        for sg in _section(result, "asset")["subgroups"]
        for a in sg["accounts"]
    ]
    liability_codes = [
        a["code"]
        for sg in _section(result, "liability")["subgroups"]
        for a in sg["accounts"]
    ]
    assert "20002000" in asset_codes
    assert "20002000" not in liability_codes
    assert result["totals"]["assets"] == 500.0


# ── 2. Sign conventions ───────────────────────────────────────────────────────


async def test_sign_convention_asset_debit_minus_credit() -> None:
    accounts = [_acct(1, "1000", "asset_cash", "asset")]
    groups = [_grp(1, 150.0, 50.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["totals"]["assets"] == 100.0
    assert _subgroup(result, "asset", "asset_cash")["accounts"][0]["balance"] == 100.0


async def test_sign_convention_liability_and_equity_credit_minus_debit() -> None:
    accounts = [
        _acct(1, "3000", "liability_current", "liability"),
        _acct(2, "4000", "equity", "equity"),
    ]
    groups = [_grp(1, 30.0, 130.0), _grp(2, 10.0, 60.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["totals"]["liabilities"] == 100.0
    assert result["totals"]["equity"] == 50.0


async def test_negative_equity_preserved() -> None:
    """Equity is negative on the live database today — the sign must survive."""
    accounts = [_acct(1, "4000", "equity", "equity")]
    groups = [_grp(1, 700.0, 100.0)]  # debit-heavy equity → −600

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["totals"]["equity"] == -600.0
    assert _section(result, "equity")["total"] == -600.0


# ── 3. Section totals = Σ member balances; subgroup math ─────────────────────


async def test_section_and_subgroup_totals_sum_members() -> None:
    accounts = [
        _acct(1, "1001", "asset_cash", "asset"),
        _acct(2, "1002", "asset_cash", "asset"),
        _acct(3, "1003", "asset_fixed", "asset"),
    ]
    groups = [_grp(1, 100.0, 0.0), _grp(2, 250.0, 50.0), _grp(3, 40.0, 0.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert _subgroup(result, "asset", "asset_cash")["total"] == 300.0
    assert _subgroup(result, "asset", "asset_fixed")["total"] == 40.0
    assert _section(result, "asset")["total"] == 340.0
    assert result["totals"]["assets"] == 340.0


# ── 4. unallocated_result + balanced / unbalanced ─────────────────────────────


async def test_unallocated_result_and_balanced_equation() -> None:
    result = await get_balance_sheet(client=_make_client(_BALANCED_ACCOUNTS, _BALANCED_GROUPS))

    totals = result["totals"]
    assert totals["unallocated_result"] == 70.0  # income +150, expense −80
    assert totals["liabilities_plus_equity_plus_result"] == 1000.0
    assert totals["difference"] == 0.0
    assert totals["balanced"] is True
    # income/expense accounts must NOT appear inside any section.
    all_codes = [
        a["code"]
        for s in result["sections"]
        for sg in s["subgroups"]
        for a in sg["accounts"]
    ]
    assert "50001000" not in all_codes and "60001000" not in all_codes


async def test_unbalanced_difference_and_flag() -> None:
    accounts = [
        _acct(1, "1000", "asset_cash", "asset"),
        _acct(2, "3000", "liability_current", "liability"),
        _acct(3, "4000", "equity", "equity"),
    ]
    groups = [_grp(1, 1000.0, 0.0), _grp(2, 0.0, 300.0), _grp(3, 0.0, 600.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["totals"]["difference"] == 100.0
    assert result["totals"]["balanced"] is False


async def test_balanced_within_piaster_tolerance() -> None:
    """|difference| = 0.004 < 0.01 → balanced True (tolerance is one piaster)."""
    accounts = [
        _acct(1, "1000", "asset_cash", "asset"),
        _acct(2, "3000", "liability_current", "liability"),
    ]
    groups = [_grp(1, 100.004, 0.0), _grp(2, 0.0, 100.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["totals"]["balanced"] is True
    assert result["totals"]["difference"] == 0.0  # 0.004 rounds away at 2dp


# ── 5. Omission rules ─────────────────────────────────────────────────────────


async def test_zero_balance_accounts_omitted_from_lists_totals_unaffected() -> None:
    accounts = [
        _acct(1, "1001", "asset_cash", "asset"),  # visible
        _acct(2, "1002", "asset_cash", "asset"),  # lines net to zero → omitted
        _acct(3, "1003", "asset_cash", "asset"),  # no lines at all → omitted
    ]
    groups = [_grp(1, 100.0, 0.0), _grp(2, 55.0, 55.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    subgroup = _subgroup(result, "asset", "asset_cash")
    assert [a["code"] for a in subgroup["accounts"]] == ["1001"]
    assert subgroup["total"] == 100.0
    assert result["totals"]["assets"] == 100.0


async def test_all_zero_subgroup_omitted() -> None:
    accounts = [
        _acct(1, "1001", "asset_cash", "asset"),
        _acct(2, "1501", "asset_fixed", "asset"),  # zero → whole subgroup omitted
    ]
    groups = [_grp(1, 100.0, 0.0), _grp(2, 20.0, 20.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    types_present = [sg["account_type"] for sg in _section(result, "asset")["subgroups"]]
    assert types_present == ["asset_cash"]


async def test_offsetting_subgroup_kept_with_visible_accounts() -> None:
    """Subgroup total ≈ 0 but member accounts are non-zero → subgroup stays."""
    accounts = [
        _acct(1, "1001", "asset_cash", "asset"),
        _acct(2, "1002", "asset_cash", "asset"),
    ]
    groups = [_grp(1, 500.0, 0.0), _grp(2, 0.0, 500.0)]  # +500 / −500

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    subgroup = _subgroup(result, "asset", "asset_cash")
    assert subgroup is not None
    assert subgroup["total"] == 0.0
    assert len(subgroup["accounts"]) == 2


async def test_tiny_balances_accumulate_into_kept_subgroup() -> None:
    """Each account < 0.005 (omitted from the list) but their FULL-precision
    sum is 0.008 ≥ ε → subgroup kept with empty list, total 0.01."""
    accounts = [
        _acct(1, "1001", "asset_cash", "asset"),
        _acct(2, "1002", "asset_cash", "asset"),
    ]
    groups = [_grp(1, 0.004, 0.0), _grp(2, 0.004, 0.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    subgroup = _subgroup(result, "asset", "asset_cash")
    assert subgroup is not None
    assert subgroup["accounts"] == []
    assert subgroup["total"] == 0.01


# ── 6. Unmapped displayed account_type → fail loud ────────────────────────────


async def test_unmapped_account_type_raises_naming_value() -> None:
    accounts = [_acct(1, "1000", "asset_weird", "asset")]

    with pytest.raises(BalanceSheetIntegrityError, match="asset_weird"):
        await get_balance_sheet(client=_make_client(accounts, []))


async def test_unmapped_account_types_all_named() -> None:
    accounts = [
        _acct(1, "1000", "asset_weird", "asset"),
        _acct(2, "3000", "liability_strange", "liability"),
    ]

    with pytest.raises(BalanceSheetIntegrityError) as exc_info:
        await get_balance_sheet(client=_make_client(accounts, []))

    message = str(exc_info.value)
    assert "asset_weird" in message and "liability_strange" in message


async def test_unmapped_income_type_does_not_raise() -> None:
    """The label map covers DISPLAYED groups only — income/expense types are
    never displayed, so an exotic income type must not trip the fail-loud."""
    accounts = [
        _acct(1, "1000", "asset_cash", "asset"),
        _acct(2, "5000", "income_exotic", "income"),
    ]
    groups = [_grp(1, 10.0, 0.0), _grp(2, 0.0, 10.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["totals"]["unallocated_result"] == 10.0


# ── 7. Unknown account id in posted lines → fail loud ─────────────────────────


async def test_unknown_account_id_in_lines_raises() -> None:
    accounts = [_acct(1, "1000", "asset_cash", "asset")]
    groups = [_grp(1, 100.0, 0.0), _grp(999, 5.0, 0.0)]  # 999 not in chart

    with pytest.raises(BalanceSheetIntegrityError, match="999"):
        await get_balance_sheet(client=_make_client(accounts, groups))


# ── 8. Off-balance internal_group ─────────────────────────────────────────────


async def test_off_balance_excluded_from_totals_and_reported() -> None:
    accounts = [
        _acct(1, "1000", "asset_cash", "asset"),
        _acct(2, "9000", "off_balance", "off_balance"),
    ]
    groups = [_grp(1, 100.0, 0.0), _grp(2, 40.0, 10.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    assert result["excluded_off_balance"] == {"count": 1, "total": 30.0}
    assert result["totals"]["assets"] == 100.0  # 9000 not counted anywhere
    all_codes = [
        a["code"]
        for s in result["sections"]
        for sg in s["subgroups"]
        for a in sg["accounts"]
    ]
    assert "9000" not in all_codes


# ── 9. Rounding at serialization only ─────────────────────────────────────────


async def test_amounts_rounded_to_two_decimals() -> None:
    accounts = [
        _acct(1, "1001", "asset_cash", "asset"),
        _acct(2, "1002", "asset_cash", "asset"),
    ]
    # 123.456 → 123.46; float-artifact sum 10.111 + 20.222 = 30.333 → 30.33
    groups = [_grp(1, 123.456, 0.0), _grp(2, 30.333, 0.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    balances = {a["code"]: a["balance"] for a in _subgroup(result, "asset", "asset_cash")["accounts"]}
    assert balances["1001"] == 123.46
    assert balances["1002"] == 30.33
    assert result["totals"]["assets"] == 153.79  # rounded from 153.789, not 123.46+30.33


async def test_negative_zero_never_emitted() -> None:
    """A tiny negative total must serialize as 0.0, never -0.0."""
    accounts = [_acct(1, "3000", "liability_current", "liability")]
    groups = [_grp(1, 0.001, 0.0)]  # credit−debit = −0.001 → rounds to −0.0

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    total = result["totals"]["liabilities"]
    assert total == 0.0
    assert str(total) == "0.0"  # not "-0.0"


# ── 10. Deterministic sorting ─────────────────────────────────────────────────


async def test_subgroups_sorted_by_abs_total_desc_tie_type_asc() -> None:
    accounts = [
        _acct(1, "1001", "asset_prepayments", "asset"),  # +50 (tie with fixed)
        _acct(2, "1002", "asset_fixed", "asset"),        # +50 (tie)
        _acct(3, "1003", "asset_cash", "asset"),         # −900 (largest |total|)
    ]
    groups = [_grp(1, 50.0, 0.0), _grp(2, 50.0, 0.0), _grp(3, 0.0, 900.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    order = [sg["account_type"] for sg in _section(result, "asset")["subgroups"]]
    # asset_cash first (|−900| wins), then the 50/50 tie alphabetically.
    assert order == ["asset_cash", "asset_fixed", "asset_prepayments"]


async def test_accounts_sorted_by_abs_balance_desc_tie_code_asc() -> None:
    accounts = [
        _acct(1, "1003", "asset_cash", "asset"),  # +20 (tie, higher code)
        _acct(2, "1001", "asset_cash", "asset"),  # +20 (tie, lower code)
        _acct(3, "1002", "asset_cash", "asset"),  # −75 (largest |balance|)
    ]
    groups = [_grp(1, 20.0, 0.0), _grp(2, 20.0, 0.0), _grp(3, 0.0, 75.0)]

    result = await get_balance_sheet(client=_make_client(accounts, groups))

    codes = [a["code"] for a in _subgroup(result, "asset", "asset_cash")["accounts"]]
    assert codes == ["1002", "1001", "1003"]


# ── RPC failure wrapping + read-only paranoia ─────────────────────────────────


async def test_rpc_failure_raises_odoo_query_error() -> None:
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("connection refused"))

    with pytest.raises(OdooQueryError):
        await get_balance_sheet(client=client)


def test_label_map_covers_all_live_displayed_types() -> None:
    """The 11 account_type values present for asset/liability/equity in the
    live chart (read-only probe, 2026-07-05) — plus the approved neutral
    wording for equity_unaffected (can be a loss, never presume profit)."""
    assert set(ACCOUNT_TYPE_LABELS_AR.keys()) == {
        "asset_receivable", "asset_cash", "asset_current", "asset_prepayments",
        "asset_fixed", "asset_non_current",
        "liability_payable", "liability_current", "liability_non_current",
        "equity", "equity_unaffected",
    }
    for account_type, label in ACCOUNT_TYPE_LABELS_AR.items():
        assert isinstance(label, str) and label.strip(), f"{account_type} has an empty label"
    assert ACCOUNT_TYPE_LABELS_AR["equity_unaffected"] == "نتيجة السنة الحالية"


def test_no_write_methods_in_allowed_methods() -> None:
    assert not (ALLOWED_METHODS & {"create", "write", "unlink"})
