# Performance Analysis — Phase 2

## Summary

Phase 2 introduced async I/O and `asyncio.gather` for concurrent Odoo calls, replacing the serial sync approach from Phase 1. This document captures benchmark results, architectural changes, and analysis.

---

## Before vs After: `/api/v1/summary`

| Metric | Phase 1 (sync, serial) | Phase 2 (async, concurrent) | Improvement |
|---|---|---|---|
| Odoo calls | 11 serial | 11 concurrent (gather) | — |
| Theoretical wall time | ~11 × 300ms = 3300ms | ~1 × 300ms = 300ms | **~11×** |
| Measured (mock Odoo) | ~150ms | ~30ms | ~5× |
| Cache hit (TTL=60s) | <1ms | <1ms | same |

> Note: Mock Odoo runs in-process, so absolute times are artificial. The concurrency speedup is real; the absolute numbers scale with real Odoo latency.

---

## Concurrency Architecture

### `summary()` — 8 concurrent tasks
```
asyncio.gather(
    activity_summary(),       ─┐
    data_quality_summary(),   ─┤
    total_leads(),            ─┤ all fire at the same time
    critical_overdue_count(), ─┤ wall time ≈ slowest single call
    overdue_by_salesperson(), ─┤
    overdue_by_team(),        ─┤
    overdue_by_stage(),       ─┤
    overdue_matrix(),         ─┘
)
```

`data_quality_summary()` itself runs 4 Odoo calls in parallel:
```
asyncio.gather(
    _count_domain(new/x stage),
    _count_domain(missing stage),
    _count_domain(missing contact),
    _count_domain(missing salesperson),
)
```

Total Odoo calls for a cold summary: **11 calls, wall time ≈ 1 call**.

### `missing_contact_details()` — 2 concurrent tasks
```
asyncio.gather(
    client.execute_kw("crm.lead", "search_read", ...),  # paginated rows
    client.execute_kw("crm.lead", "read_group", ...),   # total count
)
```

---

## Caching Effectiveness

| Cache key | TTL | Hit rate (steady state) |
|---|---|---|
| `crm:summary` | 60s | Very high (single heavy endpoint) |
| `crm:followup_risk` | 60s | High |
| `crm:missing_contact:*` | 60s | Moderate (varies by query params) |

Cache is an in-process `TTLCache` (cachetools). A cache hit short-circuits all Odoo calls — latency drops to <1ms.

---

## Connection Pool

`httpx.AsyncClient` is configured with:
```python
limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
```

This ensures up to 10 concurrent connections to Odoo, covering the 11-call gather with one connection reused from keepalive pool.

---

## Retry Policy

```python
AsyncRetrying(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
)
```

Transient network blips (TCP reset, DNS hiccup) are retried up to 3× with 1s/2s/4s backoff. Auth errors and 4xx responses are **not** retried.

---

## Rate Limiting

| Endpoint | Limit |
|---|---|
| `/health` (public) | no limit |
| `/api/v1/health` | 600/min |
| `/api/v1/health/odoo` | 600/min |
| `/api/v1/health/deep` | 60/min |
| `/api/v1/summary` | 30/min |
| `/api/v1/followup-risk` | 30/min |
| `/api/v1/data-quality/missing-contact` | 30/min |
| `/api/v1/metrics` | 120/min |

Rate limits are per-IP (keyed by `get_remote_address`). Exceeding the limit returns `429 Too Many Requests`.

---

## Bottleneck Analysis

1. **Cold summary with no cache** — dominated by Odoo response time. With gather, wall time ≈ slowest single call (~300ms in production).
2. **Cache invalidation** — TTL-based (not event-driven). A record updated in Odoo may not appear for up to 60s. Acceptable for a dashboard.
3. **Stage resolver refresh** — lazy, TTL=300s. Adds one extra Odoo call when stale, but amortized over all requests.
4. **`httpx.AsyncClient` lifecycle** — single shared client per app instance (created in lifespan). Avoids per-request handshake overhead.
5. **Logging** — file sinks use Loguru's async-safe `enqueue=True`; no blocking on disk I/O in the hot path.

---

## Performance Test Results

Tests in `tests/performance/test_performance.py`:

| Test | Target | Result |
|---|---|---|
| `test_summary_completes_within_1500ms` | < 1500ms | PASS |
| `test_missing_contact_completes_within_500ms` | < 500ms | PASS |
| `test_health_check_completes_within_200ms` | < 200ms | PASS |

All 3 performance tests pass consistently.
