"""
Route/RBAC tests for the Projects Inventory HTML page —
GET /projects-inventory/dashboard.

Mirrors the marketing-attribution HTML page gating: 302 (unauthenticated, → /login),
403 (authenticated but without the module grant), 200 (with the module). Uses
dependency overrides + a mocked user_repo and patches get_inventory_overview, so no
session bootstrap or Odoo connection is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_html
from backend.auth.models import UserRecord
from backend.main import app

_URL = "/projects-inventory/dashboard"
_VALUE_URL = "/projects-inventory/value-area"
_OUTLIERS_URL = "/projects-inventory/pricing-outliers"

# A user explicitly granted the projects_inventory module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["projects_inventory"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user WITHOUT the module (only hr) — must be 403.
_OTHER_MODULE_RECORD = UserRecord(
    username="other", password_hash="", modules=["hr"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# An admin user (modules=['*'], is_admin=True) — required for the admin-only DQ page.
_ADMIN_RECORD = UserRecord(
    username="admin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# Six-bucket payload (domain.BUCKET_ORDER). sold_pct = (contracted + delivered) ÷ total.
_MOCK_DATA = {
    "total_units": 23,
    "buckets": [
        {"key": "available", "count": 11, "pct": 47.83},
        {"key": "reserved", "count": 2, "pct": 8.70},
        {"key": "under_review", "count": 1, "pct": 4.35},
        {"key": "contracted", "count": 8, "pct": 34.78},
        {"key": "delivered", "count": 1, "pct": 4.35},
        {"key": "unclassified", "count": 0, "pct": 0.0},
    ],
    "sold_pct": 39.13,
    "projects": [
        {
            "project_id": 1, "project_name": "Project#New Capital", "total_units": 10,
            "buckets": [
                {"key": "available", "count": 2, "pct": 20.0},
                {"key": "reserved", "count": 1, "pct": 10.0},
                {"key": "under_review", "count": 1, "pct": 10.0},
                {"key": "contracted", "count": 5, "pct": 50.0},
                {"key": "delivered", "count": 1, "pct": 10.0},
                {"key": "unclassified", "count": 0, "pct": 0.0},
            ],
            "sold_pct": 60.0, "is_early_stage": False,
        },
        {
            "project_id": 3, "project_name": "Project#La puerta", "total_units": 5,
            "buckets": [
                {"key": "available", "count": 5, "pct": 100.0},
                {"key": "reserved", "count": 0, "pct": 0.0},
                {"key": "under_review", "count": 0, "pct": 0.0},
                {"key": "contracted", "count": 0, "pct": 0.0},
                {"key": "delivered", "count": 0, "pct": 0.0},
                {"key": "unclassified", "count": 0, "pct": 0.0},
            ],
            "sold_pct": 0.0, "is_early_stage": True,
        },
    ],
    "project_count": 2,
    "reference_date": "2026-06-18",
    "as_of": "2026-06-18T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 50,
}


def _client_with(record: UserRecord) -> TestClient:
    app.dependency_overrides[get_current_user_html] = lambda: record.username
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = record
    app.state.user_repo = mock_repo
    return TestClient(app, raise_server_exceptions=True, follow_redirects=False)


def _cleanup() -> None:
    app.dependency_overrides.pop(get_current_user_html, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


# ── 302 unauthenticated ────────────────────────────────────────────────────────


def test_unauthenticated_redirects_to_login() -> None:
    """No session → 302 to /login, before the handler body runs."""
    c = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
    r = c.get(_URL)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ── 403 without the module grant ───────────────────────────────────────────────


def test_403_without_module_grant() -> None:
    """Authenticated but lacking projects_inventory → 403 (rendered as HTML)."""
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
        assert "text/html" in r.headers.get("content-type", "")
    finally:
        _cleanup()


# ── 200 with the module grant ──────────────────────────────────────────────────


def test_200_with_scoped_module_grant() -> None:
    """A non-admin user granted the module gets the rendered page with the key labels."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_inventory_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Page title / KPI labels (EN default).
        assert "Inventory &amp; Availability" in body or "Inventory & Availability" in body
        assert "Total units" in body
        assert "By project" in body
        # All SIX status labels (domain.BUCKET_ORDER), EN default.
        assert "Available" in body
        assert "Reserved" in body
        assert "Under Review" in body
        assert "Contracted" in body
        assert "Delivered" in body
        assert "Unclassified" in body
        # Project rows rendered from the mock.
        assert "Project#New Capital" in body
        assert "Project#La puerta" in body
        assert "60.0" in body                 # New Capital sold_pct, round(1)
        # Early-stage badge for the shell project.
        assert "Early stage" in body
        # Sidebar entry present for a user with the module.
        assert 'href="/projects-inventory/dashboard"' in body
    finally:
        _cleanup()


def test_six_buckets_render_with_distinct_colours() -> None:
    """Every one of the six buckets renders its own label, its own bar/dot colour and
    its own KPI card — no bucket borrows another's. The three NEW buckets are the ones
    the old three-branch chain used to mislabel, so they are asserted explicitly."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_inventory_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        body = r.text
        # Each bucket's segment/dot colour is present exactly as the BUCKET_UI map
        # declares it — under_review is sky, delivered is a DARKER success step, and
        # unclassified is neutral grey (never the contracted green).
        assert "bg-primary-500 dark:bg-primary-600" in body      # available
        assert "bg-warning-400 dark:bg-warning-500" in body      # reserved
        assert "bg-sky-500 dark:bg-sky-600" in body              # under_review
        assert "bg-success-500 dark:bg-success-600" in body      # contracted
        assert "bg-success-700 dark:bg-success-800" in body      # delivered
        assert "bg-neutral-400 dark:bg-neutral-500" in body      # unclassified
        # The KPI row is the 7-card layout (Total + six buckets).
        assert "xl:grid-cols-7" in body
        # Bar-segment titles carry the right label against the right count: the mock's
        # under_review is 1 unit at 4.35%, delivered 1 at 4.35% — the old chain painted
        # BOTH of these green and called them "Contracted".
        assert 'title="Under Review: 1 (4.3%)"' in body
        assert 'title="Delivered: 1 (4.3%)"' in body
        # A zero-count bucket keeps its KPI card and its legend entry (the legend's
        # title is the bare label) but is skipped as a bar segment — a segment title
        # is the "Label: count (pct%)" form, and unclassified has none.
        assert 'title="Unclassified">Unclassified</span>' in body
        assert 'title="Unclassified:' not in body
        # The strings object hands the drill panel all six labels.
        for key in ("available", "reserved", "under_review", "contracted",
                    "delivered", "unclassified"):
            assert f"{key}:" in body, f"PROJINV_STRINGS missing bucket label: {key!r}"
    finally:
        _cleanup()


def test_unknown_bucket_key_renders_grey_with_raw_key() -> None:
    """No else-fallthrough anywhere: a bucket key the template has never seen renders
    neutral grey labelled with the RAW key — never green, never 'Contracted'. This is
    the regression the six-branch mapping exists to prevent."""
    payload = {
        **_MOCK_DATA,
        "total_units": 24,
        "buckets": _MOCK_DATA["buckets"] + [
            {"key": "escrow", "count": 1, "pct": 4.17},
        ],
    }
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_inventory_overview",
            new=AsyncMock(return_value=payload),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        body = r.text
        # Labelled with the raw key, and its bar segment is grey.
        assert 'title="escrow: 1 (4.2%)"' in body
        assert "bg-neutral-400 dark:bg-neutral-500" in body
        # It did NOT inherit the contracted label.
        assert 'title="Contracted: 1 (4.2%)"' not in body
    finally:
        _cleanup()


# ── Slice 1b — drill-down wiring rendered into the page ────────────────────────


def test_drill_wiring_present_in_page() -> None:
    """Project cards are drill triggers; the panel partial, controller JS, the strings
    object, and the breadcrumb root are all rendered."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_inventory_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        body = r.text
        # Each project card carries the drill trigger data-attributes.
        assert 'data-pi-drill-level="project"' in body
        assert 'data-pi-drill-id="1"' in body
        assert 'data-pi-drill-id="3"' in body
        assert 'data-pi-drill-name="Project#New Capital"' in body
        # The drill panel partial is rendered (portal block).
        assert 'id="pi-dd-panel"' in body
        assert 'id="pi-dd-breadcrumb"' in body
        # The breadcrumb root crumb + its label.
        assert 'id="pi-dd-root-crumb"' in body
        assert "Portfolio" in body
        # The controller JS is included and the strings object injected.
        assert "/static/js/projects_inventory_drill.js" in body
        assert "window.PROJINV_STRINGS" in body
        # A level label string is present in the strings block.
        assert "Phases" in body
    finally:
        _cleanup()


# ── Slice 2 — Value & Area HTML page ───────────────────────────────────────────

_MOCK_VALUE = {
    "total_units": 1735, "available_units_count": 287, "sold_units_count": 1400,
    "sold_units_with_contract_count": 1395, "sold_units_below_list_count": 664,
    "no_contract_count": 5,
    "available_list_value": 4_606_666_395.0, "available_area": 67_724.07,
    "sold_realized_value": 5_709_600_379.98, "sold_contracted_area": 286_960.70,
    "sold_list_value": 6_345_001_260.75,
    "sold_with_contract_list_value": 6_270_305_215.75,
    "sold_with_contract_area": 285_755.70, "no_contract_list_value": 74_696_045.0,
    "gap_abs": 560_704_835.77, "gap_pct": 8.94,
    "capture_pct": 91.06, "pct_units_below_list": 47.60,
    "avg_price_per_m2_realized": 19_980.71, "sold_pct_units": 80.69,
    "projects": [
        {
            "project_id": 1, "project_name": "New Capital",
            "total_units": 1401, "available_units_count": 201, "sold_units_count": 1166,
            "sold_units_with_contract_count": 1163, "sold_units_below_list_count": 538,
            "no_contract_count": 3,
            "available_list_value": 2_572_283_895.0, "available_area": 45_003.07,
            "sold_realized_value": 3_404_246_935.98, "sold_contracted_area": 214_856.70,
            "sold_list_value": 3_752_961_960.75,
            "sold_with_contract_list_value": 3_748_291_960.75,
            "sold_with_contract_area": 214_416.70, "no_contract_list_value": 4_670_000.0,
            "gap_abs": 344_045_024.77, "gap_pct": 9.18,
            "capture_pct": 90.82, "pct_units_below_list": 46.26,
            "avg_price_per_m2_realized": 15_876.78, "sold_pct_units": 83.23,
        },
        {
            "project_id": 2, "project_name": "Cassette",
            "total_units": 334, "available_units_count": 86, "sold_units_count": 234,
            "sold_units_with_contract_count": 232, "sold_units_below_list_count": 126,
            "no_contract_count": 2,
            "available_list_value": 2_034_382_500.0, "available_area": 22_721.0,
            "sold_realized_value": 2_305_353_444.0, "sold_contracted_area": 72_104.0,
            "sold_list_value": 2_592_039_300.0,
            "sold_with_contract_list_value": 2_522_013_255.0,
            "sold_with_contract_area": 71_339.0, "no_contract_list_value": 70_026_045.0,
            "gap_abs": 216_659_811.0, "gap_pct": 8.59,
            "capture_pct": 91.41, "pct_units_below_list": 54.31,
            "avg_price_per_m2_realized": 32_315.47, "sold_pct_units": 70.06,
        },
    ],
    "project_count": 2,
    "reference_date": "2026-06-19", "as_of": "2026-06-19T10:00:00+00:00",
    "cache_status": "fresh", "rpc_duration_ms": 80,
}


def test_value_area_200_with_scoped_module_grant() -> None:
    """A non-admin user granted the module gets the rendered Value & Area page."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_value_area_overview",
            new=AsyncMock(return_value=_MOCK_VALUE),
        ):
            r = c.get(_VALUE_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Page title + KPI labels (EN default).
        assert "Value &amp; Area" in body or "Value & Area" in body
        assert "actual value" in body
        assert "if at list price" in body
        # Both scoped projects rendered; La Puerta absent.
        assert "New Capital" in body
        assert "Cassette" in body
        assert "La puerta" not in body
        # Realized-as-contracted caveat is present (honesty requirement).
        assert "contracted value" in body
        # Sidebar entry for the new page present for a user with the module.
        assert 'href="/projects-inventory/value-area"' in body
    finally:
        _cleanup()


def test_value_area_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_VALUE_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
    finally:
        _cleanup()


def test_value_area_unauthenticated_redirects_to_login() -> None:
    c = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
    r = c.get(_VALUE_URL)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ── Slice 2.5 — Pricing Outliers HTML page (module-gated, NOT admin) ───────────

_MOCK_OUTLIERS = {
    "section_a": [
        {"unit_id": 6, "code": "P1-6", "project_id": 1, "project_name": "New Capital",
         "zone_name": "Zone#10", "unit_type_name": "Type#20",
         "vintage_bucket_label": "2022–2023", "sale_date": "2023-07-01",
         "realized_pm2": 35_000.0, "group_median_pm2": 20_250.0,
         "deviation_pct": 72.84, "direction": "above", "is_confirmed": True},
    ],
    "section_b": [
        {"unit_id": 25, "code": "GD-6", "project_id": 1, "project_name": "New Capital",
         "unit_type_name": "Type#21", "sale_date": "2022-01-01",
         "list_total": 1_250_000.0, "realized_total": 750_000.0,
         "discount_pct": 40.0, "peer_median_discount_pct": 25.0, "kind": "deep",
         "is_confirmed": False},
        {"unit_id": 7, "code": "S-7", "project_id": 1, "project_name": "New Capital",
         "unit_type_name": "Type#21", "sale_date": "2022-02-01",
         "list_total": 2_000_000.0, "realized_total": 1_000_000.0,
         "discount_pct": 40.0, "peer_median_discount_pct": None, "kind": "deep",
         "is_confirmed": False},
    ],
    "section_a_count": 1, "section_a_below_count": 0, "section_a_above_count": 1,
    "section_b_count": 2, "section_b_deep_count": 2, "section_b_premium_count": 0,
    "confirmed_count": 1,
    "insufficient_peers_count": 3, "eligible_group_count": 1, "population_count": 9,
    "projects": [
        {"project_id": 1, "project_name": "New Capital",
         "section_a_count": 1, "section_b_count": 2, "confirmed_count": 1},
        {"project_id": 2, "project_name": "Cassette",
         "section_a_count": 0, "section_b_count": 0, "confirmed_count": 0},
    ],
    "project_count": 2,
    "thresholds": {"min_group_size": 5, "iqr_mult": 1.5, "min_dev_pct": 15.0,
                   "deep_discount_pct": 25.0, "premium_pct": -10.0, "vintage_bucket_years": 2},
    "reference_date": "2026-06-22", "as_of": "2026-06-22T10:00:00+00:00",
    "cache_status": "fresh", "rpc_duration_ms": 90,
}


def test_pricing_outliers_200_with_scoped_module_grant() -> None:
    """A non-admin user granted the module gets the rendered Pricing Outliers page."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_pricing_outliers_overview",
            new=AsyncMock(return_value=_MOCK_OUTLIERS),
        ):
            r = c.get(_OUTLIERS_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Page title + section labels (EN default).
        assert "Pricing Outliers" in body
        assert "peer price/m² outliers" in body
        assert "discount outliers vs own list" in body
        # Flagged rows + the confirmed badge rendered from the mock.
        assert "P1-6" in body
        assert "S-7" in body
        assert "2022–2023" in body
        assert "Above peers" in body
        assert "Deep discount" in body
        # New Section-B peer-median-discount column: header + a % for the eligible-group
        # deep row, and the small-group hint for the None row.
        assert "Peer median discount" in body
        assert "25.0%" in body
        assert "small group" in body
        # CSV export wiring (reuses window.exportTableCSV by table id) + the contracted caveat.
        assert "exportTableCSV('po-section-a-table')" in body
        assert "exportTableCSV('po-section-b-table')" in body
        assert "contracted value" in body
        # Sidebar entry for the new page present for a user with the module.
        assert 'href="/projects-inventory/pricing-outliers"' in body
    finally:
        _cleanup()


def test_pricing_outliers_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_OUTLIERS_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
    finally:
        _cleanup()


def test_pricing_outliers_unauthenticated_redirects_to_login() -> None:
    c = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
    r = c.get(_OUTLIERS_URL)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ── Inventory Data Quality HTML page (admin only) ──────────────────────────────

_DQ_URL = "/projects-inventory/data-quality"

_MOCK_DQ = {
    "checks": [
        {"key": "no_contract", "count": 1, "items": [
            {"unit_id": 3637, "code": "AF135-7-404", "project_name": "New Capital",
             "defect_type": "no_contract", "detail": "amount 1,620,000"},
        ]},
        {"key": "broken_hierarchy", "count": 1, "items": [
            {"unit_id": 4321, "code": "AF155-3-702", "project_name": "New Capital",
             "defect_type": "zone_phase", "detail": "zone 26 'Zone#1' phase 4; unit phase_id=2"},
        ]},
        {"key": "no_list_price", "count": 0, "items": []},
    ],
    "total_issues": 2,
    "check_d": {
        "key": "implausible_list_price",
        "count": 2,
        "items": [
            {"unit_id": 5501, "code": "HS-STUDIO-12", "project_name": "New Capital",
             "unit_type_name": "HS-Studio", "state": "sold", "list_pm2": 65000.0,
             "meter_price": 65000.0, "anchor_realized_pm2": 20000.0, "ratio": 3.25,
             "list_total": 3_250_000.0, "signal": "peer"},
            {"unit_id": 5502, "code": "BF255-9-203", "project_name": "New Capital",
             "unit_type_name": "BF-Apartment", "state": "unsold", "list_pm2": 3_508_000.0,
             "meter_price": 3_508_000.0, "anchor_realized_pm2": 46800.0, "ratio": 74.96,
             "list_total": 3_508_000.0, "signal": "impossible"},
        ],
        "tier1_count": 1, "tier2a_count": 0, "tier2b_count": 1,
        "evaluated_count": 1734, "unevaluable_count": 84,
        "thresholds": {"list_trust_k": 2.0, "type_k": 3.0, "type_spread_max": 2.5,
                       "impossible_k": 5.0, "min_group_size": 5},
    },
    "reference_date": "2026-06-19",
    "as_of": "2026-06-19T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 70,
}


def test_data_quality_200_with_admin() -> None:
    """An admin gets the rendered Data Quality page with the section labels + rows."""
    c = _client_with(_ADMIN_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_data_quality_overview",
            new=AsyncMock(return_value=_MOCK_DQ),
        ):
            r = c.get(_DQ_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Page title + section names (EN default).
        assert "Inventory Data Quality" in body
        assert "Sold units without a contract" in body
        assert "Broken hierarchy chains" in body
        # Flagged rows rendered from the mock.
        assert "AF135-7-404" in body
        assert "AF155-3-702" in body
        # A localized defect summary + the technical detail line.
        assert "Zone not under" in body
        assert "phase 4; unit phase_id=2" in body
        # CSV export wiring (embedded JSON + button label).
        assert "window.DQ_ROWS" in body
        assert "Download CSV" in body
        # Check D section: title, rows, signal labels + the CSV-by-table-id wiring.
        assert "Implausible list price" in body
        assert "HS-STUDIO-12" in body
        assert "BF255-9-203" in body
        assert "Possible area error" in body   # Tier 2b label softened from "Impossible"
        assert "exportTableCSV('dq-d-table')" in body
        # Check D footnote notes (meter-price fix + area-error guidance) + unevaluable count.
        assert "correct the meter price" in body
        assert "Check the area first" in body
        assert "could not be evaluated" in body
        # Admin-only sidebar entry present.
        assert 'href="/projects-inventory/data-quality"' in body
    finally:
        _cleanup()


def test_data_quality_403_without_admin() -> None:
    """A non-admin (even with the module) is denied the admin-only page."""
    c = _client_with(_SCOPED_RECORD)
    try:
        r = c.get(_DQ_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
    finally:
        _cleanup()


def test_data_quality_unauthenticated_redirects_to_login() -> None:
    c = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
    r = c.get(_DQ_URL)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")
