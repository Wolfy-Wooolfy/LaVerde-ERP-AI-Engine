"""
Collections KPI endpoints.

GET /api/v1/collections/kpi/late-uncollected  — KPI 2: Late Uncollected
"""

from fastapi import APIRouter

router = APIRouter(prefix="/collections", tags=["collections"])
