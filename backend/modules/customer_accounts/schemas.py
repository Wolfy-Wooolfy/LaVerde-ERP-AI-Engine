"""
Pydantic response schemas for Customer Accounts KPIs.

KPI A — Total Customer Receivables    (M3-S2).
KPI B — Top Overdue Customers         (M3-S3).
KPI C — Unallocated Wallet Balance    (M3-S4).
Refunds — Alert section summary       (M3-S4).
"""

from typing import Literal

from pydantic import BaseModel


class TotalReceivablesResponse(BaseModel):
    value: float                            # EGP SUM(due_amount) across all posted customers
    customer_count: int                     # distinct partner_id groups from read_group
    record_count: int                       # total posted installment count (sum of __count per group)
    currency: Literal["EGP"]
    as_of: str                              # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                    # 0 when served from cache
    domain: list                            # [('state', '=', 'post')]


# ── KPI B ─────────────────────────────────────────────────────────────────────

class OverdueCustomerRow(BaseModel):
    rank: int                               # 1-indexed, sorted by due_amount descending
    customer_id: int
    customer_name: str
    due_amount: float                       # EGP remaining balance for this partner
    installment_count: int                  # __count: overdue installments for this partner


class ConcentrationInfo(BaseModel):
    n: int                                  # number of customers in the top-N group
    amount: float                           # EGP SUM(due_amount) for those N customers
    pct: float                              # amount / total_overdue * 100, rounded to 2dp


class TopOverdueCustomersResponse(BaseModel):
    total_overdue: float                    # EGP SUM(due_amount) ALL overdue customers
    overdue_customer_count: int             # distinct overdue partner_id groups
    record_count: int                       # total overdue installments (sum of __count)
    top_n_concentration: ConcentrationInfo
    top_customers: list[OverdueCustomerRow] # up to 20, sorted due_amount desc
    currency: Literal["EGP"]
    as_of: str
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    domain: list


# ── KPI C ─────────────────────────────────────────────────────────────────────

class UnallocatedWalletBalanceResponse(BaseModel):
    value: float                            # EGP SUM(residual_amount) — positive-only (residual_amount>0)
    customer_count: int                     # distinct partner_id groups with positive residual
    record_count: int                       # total reconcile records matching domain (sum of __count)
    currency: Literal["EGP"]
    as_of: str
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    domain: list


# ── Refunds alert section ─────────────────────────────────────────────────────

class RefundsSummaryResponse(BaseModel):
    total_refunds: float                    # EGP SUM(amount) — negative (amount<0 records)
    refund_count: int                       # total refund records (sum of __count per group)
    null_partner_count: int                 # records where partner_id = False (currently 0, per M3-S1)
    currency: Literal["EGP"]
    as_of: str
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    domain: list
