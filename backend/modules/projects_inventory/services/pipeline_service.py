"""
Projects Inventory — contracts PIPELINE service (read-only).

The board view of deals still in flight: every NON-cancel rs.contract grouped by the
stage it currently sits in, with how long it has been sitting there. Read-only —
_assert_read_only() runs at entry and only search_read is ever issued.

Grouping (the 6 non-cancel states, exhaustively):
  draft                          → awaiting_action  (no desk owns it yet)
  legal / finance / engineering   → under_review     (a named desk owns it)
  confirm                        → confirmed_count  (counts only)
  delivered                      → delivered_count  (counts only)
A non-cancel state outside that vocabulary raises UnknownContractStateError — the SAME
strictness rule the bucket classifier applies on the contract axis. Σ groups ==
total_non_cancel is an explicit raise (survives python -O).

DAYS IN STAGE (Cairo-aware). write_date is NEVER consulted: 31 of the 33 live pipeline
contracts share one bulk-edit stamp, so it dates the edit, not the stage entry. The
stage-entry date is the LATEST mail.message whose tracking rows record a change to
rs.contract.state; a contract that never changed state falls back to create_date —
the normal path for a draft, and for 32 of the 33 live pipeline rows. `today` is
injectable so the day arithmetic is testable without freezing the clock.

Odoo timestamps come back naive UTC; they are converted to a Cairo-local calendar date
before subtracting, because the board thinks in Cairo days and the two differ by one
either side of midnight.

Exactly THREE batched fetch groups on a cold cache, none of them per contract:
  (1) non-cancel rs.contract [id, name, state, unit_id, create_date];
  (2) mail.message for the pipeline-stage contract ids (one call);
  (3) mail.tracking.value for those messages, state changes only, filtered SERVER-side
      by the technical field name (one call).
Groups 2 and 3 are skipped entirely when the funnel is empty, so an all-confirm
portfolio costs ONE round-trip rather than three.
"""

import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.projects_inventory.domain import (
    CONTRACT_CANCEL_STATE,
    CONTRACT_CONFIRM_STATE,
    CONTRACT_DELIVERED_STATE,
    CONTRACT_DRAFT_STATE,
    CONTRACT_MODEL,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    MAIL_MESSAGE_DATE_FIELD,
    MAIL_MESSAGE_MODEL,
    MAIL_MESSAGE_MODEL_FIELD,
    MAIL_MESSAGE_RES_ID_FIELD,
    PIPELINE_REVIEW_STAGES,
    PIPELINE_STAGE_STATES,
    TRACKING_FIELD_MODEL_PATH,
    TRACKING_FIELD_NAME_PATH,
    TRACKING_MESSAGE_FIELD,
    TRACKING_MODEL,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    UnknownContractStateError,
    _assert_read_only,
    _m2o,
    _paged_search_read,
)
from backend.shared.odoo.client import OdooClient

# Its OWN cache key prefix — the pipeline never shares a payload with the board.
_CACHE_KEY_PREFIX = "projects_inventory:pipeline"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")

# create_date is the days-in-stage FALLBACK. write_date is deliberately absent — it is
# never fetched, so it cannot be consulted even by accident.
_CONTRACT_FIELDS = [
    "id", "name", CONTRACT_STATE_FIELD, CONTRACT_UNIT_FIELD, "create_date",
]
_MESSAGE_FIELDS = ["id", MAIL_MESSAGE_RES_ID_FIELD, MAIL_MESSAGE_DATE_FIELD]
_TRACKING_FIELDS = [TRACKING_MESSAGE_FIELD]


def _cairo_date(odoo_dt) -> Optional[date]:
    """Odoo returns a naive UTC 'YYYY-MM-DD HH:MM:SS'. Read it as UTC and convert to a
    Cairo-local calendar date. Returns None for an empty/unparseable stamp."""
    if not odoo_dt:
        return None
    try:
        naive = datetime.strptime(str(odoo_dt)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc).astimezone(_CAIRO_TZ).date()


def _days_since(stage_day: Optional[date], today: date) -> int:
    """Whole Cairo days from the stage entry to today. Floors at 0: a future stamp is
    clock skew, and an undatable contract (no tracking AND no create_date — not seen
    live) reports 0 rather than inventing an age."""
    if stage_day is None:
        return 0
    return max(0, (today - stage_day).days)


def _res_id_of(value) -> Optional[int]:
    """mail.message.res_id is a many2one_reference — a plain int. Tolerate an m2o pair
    in case a future Odoo renders it that way."""
    if isinstance(value, (list, tuple)):
        return _m2o(value)[0]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


async def _fetch_stage_entry_dates(
    client: OdooClient, contract_ids: list[int]
) -> dict[int, date]:
    """contract_id → Cairo date of its LATEST rs.contract.state change, from chatter.

    Fetch groups 2 and 3 — both batched, neither per contract. A contract that never
    changed state simply does not appear in the result, and the caller falls back to
    create_date. Skipped entirely (zero RPCs) when the funnel is empty.

    The state filter is applied SERVER-side on the TECHNICAL field name via a dotted
    path, so a translated field label can never widen or narrow it.
    """
    if not contract_ids:
        return {}

    messages = await _paged_search_read(
        client,
        MAIL_MESSAGE_MODEL,
        [
            (MAIL_MESSAGE_MODEL_FIELD, "=", CONTRACT_MODEL),
            (MAIL_MESSAGE_RES_ID_FIELD, "in", contract_ids),
        ],
        _MESSAGE_FIELDS,
    )
    if not messages:
        return {}

    tracking = await _paged_search_read(
        client,
        TRACKING_MODEL,
        [
            (TRACKING_MESSAGE_FIELD, "in", [m["id"] for m in messages]),
            (TRACKING_FIELD_NAME_PATH, "=", CONTRACT_STATE_FIELD),
            (TRACKING_FIELD_MODEL_PATH, "=", CONTRACT_MODEL),
        ],
        _TRACKING_FIELDS,
    )

    # The messages that actually carry a state change.
    state_msg_ids: set[int] = set()
    for t in tracking:
        mid = t.get(TRACKING_MESSAGE_FIELD)
        mid = _m2o(mid)[0] if isinstance(mid, (list, tuple)) else mid
        if isinstance(mid, int) and not isinstance(mid, bool):
            state_msg_ids.add(mid)

    # Latest wins. Odoo's 'YYYY-MM-DD HH:MM:SS' sorts correctly as a string, so the
    # comparison keeps full timestamp precision and only then collapses to a date.
    latest_raw: dict[int, str] = {}
    for m in messages:
        if m["id"] not in state_msg_ids:
            continue
        raw = m.get(MAIL_MESSAGE_DATE_FIELD)
        if not raw:
            continue
        res_id = _res_id_of(m.get(MAIL_MESSAGE_RES_ID_FIELD))
        if res_id is None:
            continue
        current = latest_raw.get(res_id)
        if current is None or str(raw) > current:
            latest_raw[res_id] = str(raw)

    out: dict[int, date] = {}
    for res_id, raw in latest_raw.items():
        day = _cairo_date(raw)
        if day is not None:
            out[res_id] = day
    return out


def _group_contracts(
    contracts: list[dict], stage_days: dict[int, date], today: date
) -> tuple[list[dict], list[dict], int, int]:
    """Split the non-cancel contracts into (awaiting_action, under_review, confirmed,
    delivered). Pure — no I/O. Both lists come back sorted by days_in_stage desc, with
    contract_id as a deterministic tiebreak.

    Raises UnknownContractStateError if a non-cancel state cannot be placed, naming
    every offender with its count.
    """
    awaiting_action: list[dict] = []
    under_review: list[dict] = []
    confirmed_count = 0
    delivered_count = 0
    unknown: dict[str, int] = defaultdict(int)

    for c in contracts:
        state = c.get(CONTRACT_STATE_FIELD)
        if state == CONTRACT_CONFIRM_STATE:
            confirmed_count += 1
            continue
        if state == CONTRACT_DELIVERED_STATE:
            delivered_count += 1
            continue
        if state not in PIPELINE_STAGE_STATES:
            unknown[str(state)] += 1
            continue

        # Chatter first, create_date second. write_date is never even fetched.
        stage_day = stage_days.get(c["id"])
        if stage_day is None:
            stage_day = _cairo_date(c.get("create_date"))

        unit_id, unit_name = _m2o(c.get(CONTRACT_UNIT_FIELD))
        entry = {
            "contract_id": c["id"],
            "name": c.get("name") or "",
            "unit_id": unit_id if unit_id is not None else 0,
            "unit_name": unit_name or "—",
            "days_in_stage": _days_since(stage_day, today),
            "stage": None,
            "stage_label": None,
        }
        if state == CONTRACT_DRAFT_STATE:
            awaiting_action.append(entry)
        else:
            entry["stage"] = state
            entry["stage_label"] = PIPELINE_REVIEW_STAGES[state]
            under_review.append(entry)

    if unknown:
        raise UnknownContractStateError(
            f"{CONTRACT_MODEL} carries non-cancel state value(s) the pipeline cannot "
            f"place — known: {sorted(set(PIPELINE_STAGE_STATES) | {CONTRACT_CONFIRM_STATE, CONTRACT_DELIVERED_STATE})}, "
            f"offending: {dict(unknown)}. Refusing to return a pipeline that would "
            f"silently drop these contracts."
        )

    def _oldest_first(e: dict) -> tuple[int, int]:
        return (-e["days_in_stage"], e["contract_id"])

    awaiting_action.sort(key=_oldest_first)
    under_review.sort(key=_oldest_first)
    return awaiting_action, under_review, confirmed_count, delivered_count


async def get_contracts_pipeline(
    client: Optional[OdooClient] = None, today: Optional[date] = None
) -> dict:
    """Return the contracts pipeline — the pre-confirm funnel by stage.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).
        today: optional Cairo-local date to measure days_in_stage against. A TEST SEAM
            — production always uses Cairo today, which is exactly what the cache key
            is scoped to.

    Returns a dict matching schemas.ContractsPipeline.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
        UnknownContractStateError: if a non-cancel contract state cannot be placed.
        RuntimeError: if the groups fail to reconcile to total_non_cancel (explicit
            raise so it survives python -O).
    """
    _assert_read_only()

    cairo_today = today if today is not None else datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Pipeline cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
    logger.info(f"Pipeline cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        contracts = await _paged_search_read(
            _client,
            CONTRACT_MODEL,
            [(CONTRACT_STATE_FIELD, "!=", CONTRACT_CANCEL_STATE)],
            _CONTRACT_FIELDS,
        )
        # Only the in-funnel contracts need a stage-entry date; confirm/delivered are
        # counts, so their chatter is never read.
        pipeline_ids = [
            c["id"] for c in contracts
            if c.get(CONTRACT_STATE_FIELD) in PIPELINE_STAGE_STATES
        ]
        stage_days = await _fetch_stage_entry_dates(_client, pipeline_ids)
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_contracts_pipeline() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # Grouped OUTSIDE the try: an unplaceable state is a data verdict, not an RPC
    # failure, and must surface as itself rather than as OdooQueryError.
    awaiting_action, under_review, confirmed_count, delivered_count = _group_contracts(
        contracts, stage_days, cairo_today
    )

    total_non_cancel = len(contracts)
    grouped = (
        len(awaiting_action) + len(under_review) + confirmed_count + delivered_count
    )
    if grouped != total_non_cancel:
        raise RuntimeError(
            f"Pipeline reconciliation FAILED: Σ grouped contracts {grouped} != "
            f"total non-cancel {total_non_cancel}."
        )

    logger.info(
        f"Contracts pipeline: {total_non_cancel:,} non-cancel | "
        f"awaiting={len(awaiting_action):,} under_review={len(under_review):,} "
        f"confirmed={confirmed_count:,} delivered={delivered_count:,} | "
        f"stage dates from chatter for {len(stage_days):,} of {len(pipeline_ids):,} "
        f"in-funnel | RPC in {rpc_ms}ms | cache_key={cache_key}"
    )

    result: dict = {
        "awaiting_action": awaiting_action,
        "awaiting_action_count": len(awaiting_action),
        "under_review": under_review,
        "under_review_count": len(under_review),
        "confirmed_count": confirmed_count,
        "delivered_count": delivered_count,
        "total_non_cancel": total_non_cancel,
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
