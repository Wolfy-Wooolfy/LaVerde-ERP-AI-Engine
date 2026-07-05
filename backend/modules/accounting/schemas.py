"""
Pydantic response schemas for the Accounting module (Module 4 · Phase 1).

Balance Sheet — GET /api/v1/accounting/balance-sheet (Decision M4.10).
No cache_status field anywhere: this endpoint is deliberately uncached (M4.3).
"""

from typing import Literal

from pydantic import BaseModel


class BalanceSheetAccount(BaseModel):
    code: str
    name: str
    balance: float  # rounded to 2 decimals at serialization (M4.9)


class BalanceSheetSubgroup(BaseModel):
    account_type: str
    label_ar: str
    total: float  # over ALL member accounts, visible or omitted (M4.8)
    accounts: list[BalanceSheetAccount]


class BalanceSheetSection(BaseModel):
    group: Literal["asset", "liability", "equity"]
    label_ar: str
    total: float
    subgroups: list[BalanceSheetSubgroup]


class BalanceSheetTotals(BaseModel):
    assets: float
    liabilities: float
    equity: float
    unallocated_result: float  # Σ(credit−debit) over income+expense (M4.4)
    liabilities_plus_equity_plus_result: float
    difference: float
    balanced: bool  # |difference| < 0.01, evaluated pre-rounding


class ExcludedOffBalance(BaseModel):
    count: int
    total: float


class BalanceSheetResponse(BaseModel):
    generated_at: str  # ISO-8601, Africa/Cairo
    currency: Literal["EGP"]
    banner_ar: str
    totals: BalanceSheetTotals
    excluded_off_balance: ExcludedOffBalance
    sections: list[BalanceSheetSection]
    rpc_duration_ms: int
