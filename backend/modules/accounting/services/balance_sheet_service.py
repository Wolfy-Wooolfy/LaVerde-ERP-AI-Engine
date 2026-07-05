"""
Accounting — Balance Sheet service (Module 4 · Phase 1).

Computes the company balance sheet LIVE from Odoo 17 on every call, grouped
Assets / Liabilities / Equity with subgroups by ``account_type`` and
account-level detail. NO caching in this phase (Decision M4.3): the opening
balance is still being edited in place by finance, so a cached figure could
contradict what the Odoo UI shows during browser verification.

Data sources (read-only, exactly 2 RPCs per request):
  1. ``account.account``   — full chart of accounts (~363 records), search_read
  2. ``account.move.line`` — per-account debit/credit sums via read_group,
     domain ``[('parent_state', '=', 'posted')]``

Hard rules (docs/MODULE_4_ACCOUNTING_DECISIONS.md):
  * Classification uses ``internal_group`` ONLY — never code prefixes
    (Decision M4.1: prefix "2" mixes 13 asset accounts with 70 liability
    accounts in this database; 131.3M EGP effect on account 20002000).
  * Amounts come from line-level debit/credit ONLY — never
    ``account.move.amount_total`` (Decision M4.2: amount_total is wrong by
    ~2M EGP on 'entry' moves in this database).
  * Strictly read-only — no create/write/unlink by any path.
"""

import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import (
    LaVerdeERPError,
    OdooQueryError,
    ReadOnlyViolationError,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Methods that must never appear in ALLOWED_METHODS (house defense-in-depth).
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_ACCOUNT_MODEL = "account.account"
_LINE_MODEL = "account.move.line"
_ACCOUNT_FIELDS = ["id", "code", "name", "account_type", "internal_group"]
# Line-level posted filter (Decision M4.2) — the ONLY amount source.
_POSTED_DOMAIN: list = [("parent_state", "=", "posted")]

_CURRENCY = "EGP"
# Fixed banner while finance is still keying in the opening balance (M4.3).
BANNER_AR = "أرصدة افتتاحية — بيانات تحت الإدخال"

_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")

# Balance-sheet sections in fixed presentation order (M4.6).
_SECTION_ORDER: tuple[str, ...] = ("asset", "liability", "equity")
_DISPLAYED_GROUPS = frozenset(_SECTION_ORDER)
SECTION_LABELS_AR: dict[str, str] = {
    "asset": "الأصول",
    "liability": "الخصوم",
    "equity": "حقوق الملكية",
}
# income/expense accounts feed totals.unallocated_result only (M4.4).
_RESULT_GROUPS = frozenset({"income", "expense"})

# Arabic labels per account_type (M4.5 — fail-loud: a displayed account whose
# type is missing here aborts the request, naming every offending value).
# Covers every account_type present for asset/liability/equity in the live
# chart (16-pair read-only probe, 2026-07-05). equity_unaffected wording is
# deliberately neutral ("نتيجة" not "أرباح") — the current-year line can be
# a loss.
ACCOUNT_TYPE_LABELS_AR: dict[str, str] = {
    "asset_receivable": "ذمم مدينة",
    "asset_cash": "النقدية وما في حكمها",
    "asset_current": "أصول متداولة",
    "asset_prepayments": "مصروفات مدفوعة مقدماً",
    "asset_fixed": "أصول ثابتة",
    "asset_non_current": "أصول غير متداولة",
    "liability_payable": "ذمم دائنة",
    "liability_current": "خصوم متداولة",
    "liability_non_current": "خصوم غير متداولة",
    "equity": "حقوق الملكية",
    "equity_unaffected": "نتيجة السنة الحالية",
}

# |balance| below this is a rounding artifact at 2 decimals — such accounts
# are omitted from the account LISTS; totals still include them (M4.8).
_ZERO_EPSILON = 0.005
# Accounting-equation tolerance (one piaster): |difference| < 0.01 → balanced.
_BALANCE_TOLERANCE = 0.01


class BalanceSheetIntegrityError(LaVerdeERPError):
    """Raised when live accounting data violates an invariant this service
    refuses to paper over: a posted line referencing an account absent from
    the chart fetch, or a displayed account_type missing from the Arabic
    label map. Deliberately NOT an OdooQueryError — the endpoint maps this
    to 500 (the data or the label map must be fixed), not 503 (retry)."""


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


def _round2(value: float) -> float:
    """Serialization-time rounding (M4.9). Normalizes -0.0 to 0.0 so the JSON
    payload never shows a negative zero."""
    rounded = round(value, 2)
    return 0.0 if rounded == 0 else rounded


async def _fetch_accounts(client: OdooClient) -> list[dict]:
    """Fetch the full chart of accounts (one search_read, ~363 records)."""
    try:
        rows = await client.execute_kw(
            _ACCOUNT_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": _ACCOUNT_FIELDS},
        )
    except Exception as exc:
        raise OdooQueryError(f"search_read on {_ACCOUNT_MODEL} failed: {exc}") from exc
    return rows or []


async def _fetch_posted_sums(client: OdooClient) -> list[dict]:
    """Fetch per-account posted debit/credit sums (one read_group)."""
    try:
        rows = await client.execute_kw(
            _LINE_MODEL,
            "read_group",
            args=[_POSTED_DOMAIN, ["debit", "credit"], ["account_id"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(f"read_group on {_LINE_MODEL} failed: {exc}") from exc
    return rows or []


def _sums_by_account(line_groups: list[dict]) -> dict[int, tuple[float, float]]:
    """Reduce read_group rows to {account_id: (debit_sum, credit_sum)}."""
    sums: dict[int, tuple[float, float]] = {}
    for row in line_groups:
        raw = row.get("account_id")
        account_id = raw[0] if isinstance(raw, (list, tuple)) else raw
        if not account_id:
            raise BalanceSheetIntegrityError(
                "read_group returned a posted-line group with no account_id "
                f"(row: {row!r}) — every posted move line must reference an account."
            )
        debit = float(row.get("debit") or 0.0)
        credit = float(row.get("credit") or 0.0)
        prev_debit, prev_credit = sums.get(int(account_id), (0.0, 0.0))
        sums[int(account_id)] = (prev_debit + debit, prev_credit + credit)
    return sums


async def get_balance_sheet(client: Optional[OdooClient] = None) -> dict:
    """Return the live balance sheet, computed fresh on every call (no cache).

    Balance conventions (M4.6):
        internal_group == 'asset'                → balance = debit − credit
        internal_group in ('liability','equity') → balance = credit − debit

    ``totals.unallocated_result`` = Σ(credit − debit) over income + expense
    accounts. It is exactly 0.00 while only the opening balance is posted;
    once operational entries post it becomes the current-period result and
    keeps the accounting equation closed (M4.4):

        assets == liabilities + equity + unallocated_result   (± 0.01 EGP)

    Accounts whose internal_group is outside the five known values are
    excluded from all totals and surfaced in ``excluded_off_balance`` (M4.7).

    All amounts are computed at full float precision and rounded to 2
    decimals at serialization time only (M4.9); a subgroup/section total may
    therefore differ from the sum of its ROUNDED account balances by ≤ 0.01.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated
            with a write method (checked before any RPC).
        OdooQueryError: if either Odoo RPC fails (endpoint maps to 503).
        BalanceSheetIntegrityError: if a posted line references an unknown
            account id, or a displayed account_type has no Arabic label
            (endpoint maps to 500) — every offending value is named.
    """
    _assert_read_only()

    _client = client if client is not None else OdooClient()
    t0 = time.monotonic()
    try:
        accounts = await _fetch_accounts(_client)
        line_groups = await _fetch_posted_sums(_client)
    finally:
        if client is None:
            await _client.close()
    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Balance sheet: {len(accounts)} accounts, {len(line_groups)} posted "
        f"account groups in {rpc_ms}ms (live, uncached)"
    )

    accounts_by_id = {int(a["id"]): a for a in accounts}
    sums = _sums_by_account(line_groups)

    # Fail loud (M4.5/4.1a): posted amounts must never be silently dropped.
    unknown = sorted(set(sums) - set(accounts_by_id))
    if unknown:
        raise BalanceSheetIntegrityError(
            f"{len(unknown)} posted account id(s) have move lines but are absent "
            f"from the {_ACCOUNT_MODEL} fetch: {unknown}. Refusing to emit a "
            "balance sheet that silently drops posted amounts."
        )

    # Fail loud (M4.5/4.1b): every displayed account_type needs an Arabic label.
    unmapped = sorted(
        {
            str(a.get("account_type"))
            for a in accounts
            if a.get("internal_group") in _DISPLAYED_GROUPS
            and a.get("account_type") not in ACCOUNT_TYPE_LABELS_AR
        }
    )
    if unmapped:
        raise BalanceSheetIntegrityError(
            "account_type value(s) missing from ACCOUNT_TYPE_LABELS_AR for "
            f"displayed group(s): {unmapped}. Add Arabic labels before serving "
            "the balance sheet."
        )

    unallocated_result = 0.0
    excluded_count = 0
    excluded_total = 0.0
    # displayed[group][account_type] → [(balance, code, name), ...]
    displayed: dict[str, dict[str, list[tuple[float, str, str]]]] = {
        group: {} for group in _SECTION_ORDER
    }

    for account in accounts:
        debit_sum, credit_sum = sums.get(int(account["id"]), (0.0, 0.0))
        group = account.get("internal_group")

        if group == "asset":
            balance = debit_sum - credit_sum
        elif group in ("liability", "equity"):
            balance = credit_sum - debit_sum
        elif group in _RESULT_GROUPS:
            unallocated_result += credit_sum - debit_sum
            continue
        else:
            # Off-balance / unknown internal_group: excluded from all totals,
            # surfaced only in excluded_off_balance (M4.7). Its "total" is the
            # raw debit − credit sum (no sign convention applies).
            excluded_count += 1
            excluded_total += debit_sum - credit_sum
            continue

        displayed[group].setdefault(str(account.get("account_type")), []).append(
            (balance, str(account.get("code") or ""), str(account.get("name") or ""))
        )

    sections: list[dict] = []
    section_totals: dict[str, float] = {}
    for group in _SECTION_ORDER:
        subgroups_out: list[dict] = []
        section_total = 0.0
        for account_type, members in displayed[group].items():
            # Totals ALWAYS cover all member accounts, visible or not (M4.8).
            subgroup_total = sum(balance for balance, _code, _name in members)
            section_total += subgroup_total
            visible = [m for m in members if abs(m[0]) >= _ZERO_EPSILON]
            if not visible and abs(subgroup_total) < _ZERO_EPSILON:
                # All-zero subgroup — omitted; its zero total is already
                # counted in section_total (M4.8).
                continue
            # Accounts: |balance| descending, ties by code ascending (M4.8).
            visible.sort(key=lambda m: (-abs(m[0]), m[1]))
            subgroups_out.append(
                {
                    "account_type": account_type,
                    "label_ar": ACCOUNT_TYPE_LABELS_AR[account_type],
                    "total": subgroup_total,  # rounded after sorting below
                    "accounts": [
                        {"code": code, "name": name, "balance": _round2(balance)}
                        for balance, code, name in visible
                    ],
                }
            )
        # Subgroups: |total| descending on FULL precision, ties by
        # account_type ascending (M4.8) — then round for serialization.
        subgroups_out.sort(key=lambda s: (-abs(s["total"]), s["account_type"]))
        for subgroup in subgroups_out:
            subgroup["total"] = _round2(subgroup["total"])
        section_totals[group] = section_total
        sections.append(
            {
                "group": group,
                "label_ar": SECTION_LABELS_AR[group],
                "total": _round2(section_total),
                "subgroups": subgroups_out,
            }
        )

    assets = section_totals["asset"]
    liabilities = section_totals["liability"]
    equity = section_totals["equity"]
    rhs = liabilities + equity + unallocated_result
    difference = assets - rhs
    balanced = abs(difference) < _BALANCE_TOLERANCE

    if not balanced:
        logger.warning(f"Balance sheet NOT balanced: difference={difference:.6f} EGP")

    return {
        "generated_at": datetime.now(_LA_VERDE_TZ).isoformat(timespec="seconds"),
        "currency": _CURRENCY,
        "banner_ar": BANNER_AR,
        "totals": {
            "assets": _round2(assets),
            "liabilities": _round2(liabilities),
            "equity": _round2(equity),
            "unallocated_result": _round2(unallocated_result),
            "liabilities_plus_equity_plus_result": _round2(rhs),
            "difference": _round2(difference),
            "balanced": balanced,
        },
        "excluded_off_balance": {"count": excluded_count, "total": _round2(excluded_total)},
        "sections": sections,
        "rpc_duration_ms": rpc_ms,
    }
