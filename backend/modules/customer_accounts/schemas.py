"""
Pydantic response schemas for Customer Accounts KPIs.

KPI A — Total Customer Receivables (M3-S2).
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
