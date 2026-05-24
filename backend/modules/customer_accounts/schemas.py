"""
Pydantic response schemas for Customer Accounts KPIs.

KPI A — Total Customer Receivables    (M3-S2).
KPI B — Top Overdue Customers         (M3-S3).
KPI C — Unallocated Wallet Balance    (M3-S4).
Refunds — Alert section summary       (M3-S4).
Refunds — Per-record detail           (M3-S8).
Customer Drill-Down                   (M3-S6).
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


# ── Refunds detail (M3-S8) ───────────────────────────────────────────────────

class RefundsDetailRow(BaseModel):
    record_id: int
    customer_id: int                    # partner_id[0]; 0 if partner_id is False
    customer_name: str                  # partner_id[1]; "غير معروف" if partner_id is False
    amount: float                       # negative — the refund amount from Odoo
    date: str                           # YYYY-MM-DD


class RefundsDetailResponse(BaseModel):
    items: list[RefundsDetailRow]
    total_amount: float                 # SUM(amount) — negative
    record_count: int
    currency: Literal["EGP"]
    as_of: str
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    domain: list


# ── Customer Drill-Down (M3-S6) ───────────────────────────────────────────────

class DrilldownInstallmentRow(BaseModel):
    record_id: int
    date: str                       # YYYY-MM-DD (due date)
    installment_type_id: int
    installment_type_name_ar: str
    payment_state: str              # 'unpaid' | 'partial'
    timing: str                     # 'late' | 'future' — computed Python-side
    amount: float                   # face value EGP
    due_amount: float               # remaining balance EGP


class DrilldownExposure(BaseModel):
    total_due_egp: float            # إجمالي عليه = late + future (متأخر + مستقبلي)
    late_due_egp: float             # منها متأخر (date < today)
    future_due_egp: float           # منها مستقبلي (date >= today)
    paid_cash_egp: float            # دفع — x_studio_actual_paid_amount (cash only)
    total_original_egp: float       # الإجمالي الأصلي — SUM(amount) all posted installments
    total_installments: int         # عدد كل أقساط العميل (posted, all states)
    unpaid_installment_count: int   # عدد الأقساط غير المدفوعة (late + future)


class DrilldownBehavior(BaseModel):
    payment_ratio_pct: float        # نسبة السداد = x_studio_actual_paid / amount × 100
    wallet_balance_egp: float       # رصيد المحفظة (rs.account.payment.reconcile residual)
    wallet_record_count: int        # عدد سجلات المحفظة للعميل (residual > 0)


class DrilldownHeader(BaseModel):
    partner_id: int
    customer_name: str


class DrilldownInstallmentPage(BaseModel):
    items: list[DrilldownInstallmentRow]
    total_count: int                # total unpaid installments (for pagination display)
    cursor_current: str | None
    cursor_next: str | None
    has_next: bool


class CustomerDrilldownData(BaseModel):
    header: DrilldownHeader
    exposure: DrilldownExposure
    behavior: DrilldownBehavior
    installments: DrilldownInstallmentPage


class CustomerDrilldownMeta(BaseModel):
    request_id: str
    as_of: str                      # ISO 8601 UTC
    rpc_duration_ms: int
    today: str                      # YYYY-MM-DD — the boundary used for late/future split
    page_size: int
    sort_by: str
    sort_dir: str


class CustomerDrilldownResponse(BaseModel):
    version: str
    data: CustomerDrilldownData
    meta: CustomerDrilldownMeta
