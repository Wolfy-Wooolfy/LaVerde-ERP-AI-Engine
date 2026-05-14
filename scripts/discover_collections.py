"""
discover_collections.py — Module 2 Phase 1 Discovery
READ-ONLY: search_read, read_group, search_count, fields_get ONLY.
No create/write/unlink. No OpenAI. Zero cost.

Run full discovery:
    python scripts/discover_collections.py

Run section 1 dry-run only:
    python scripts/discover_collections.py --section 1
"""

import os
import uuid
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

ODOO_URL  = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB   = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USERNAME"]
ODOO_KEY  = os.environ["ODOO_API_KEY"]

SEP  = "=" * 72
SEP2 = "-" * 72


# ── RPC helpers ───────────────────────────────────────────────────────────────

def rpc(client, service, method, args):
    r = client.post(
        ODOO_URL,
        json={"jsonrpc": "2.0", "method": "call", "id": str(uuid.uuid4()),
              "params": {"service": service, "method": method, "args": args}},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Odoo RPC error: {data['error']}")
    return data["result"]


def execute(client, uid, model, method, args, kwargs=None):
    return rpc(client, "object", "execute_kw",
               [ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {}])


def search_read(client, uid, model, domain, fields, limit=None, order=None):
    kw = {"fields": fields}
    if limit:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return execute(client, uid, model, "search_read", [domain], kw)


def search_count(client, uid, model, domain):
    return execute(client, uid, model, "search_count", [domain])


def fields_get(client, uid, model):
    return execute(client, uid, model, "fields_get", [],
                   {"attributes": ["string", "type", "relation", "help", "required"]})


def read_group(client, uid, model, domain, fields, groupby, limit=None):
    kw = {"lazy": False}
    if limit:
        kw["limit"] = limit
    return execute(client, uid, model, "read_group",
                   [domain, fields, groupby], kw)


# ── Output helpers ────────────────────────────────────────────────────────────

def header(n, title):
    print(f"\n{SEP}")
    print(f"  SECTION {n}: {title}")
    print(SEP)


def subheader(title):
    print(f"\n  --- {title} ---")


def sanitize(value, field_name=""):
    """Sanitize PII before printing or writing to doc."""
    if value is None or value is False:
        return value

    sensitive_fields = {
        "name", "partner_name", "customer_name", "display_name",
        "phone", "mobile", "email", "vat", "id_number",
        "street", "street2", "city",
    }

    field_lower = field_name.lower()
    if any(s in field_lower for s in sensitive_fields):
        if isinstance(value, str) and value:
            return f"[REDACTED:{field_name}]"
        if isinstance(value, list) and len(value) == 2:
            return [value[0], f"[REDACTED:{field_name}]"]

    return value


# ── Model helpers ─────────────────────────────────────────────────────────────

def safe_count(client, uid, model, domain=None):
    try:
        return search_count(client, uid, model, domain or [])
    except Exception as e:
        return f"ERROR: {e}"


def try_fields_get(client, uid, model):
    try:
        return fields_get(client, uid, model)
    except Exception as e:
        print(f"    fields_get failed: {e}")
        return {}


def print_fields(flds, highlight=None):
    highlight = highlight or []
    print(f"  {'FIELD':<40} {'TYPE':<15} {'RELATION':<35} LABEL")
    print(f"  {'-'*40} {'-'*15} {'-'*35} {'-'*30}")
    for name, meta in sorted(flds.items()):
        flag = " <<<" if name in highlight else ""
        rel  = meta.get("relation", "")
        print(f"  {name:<40} {meta['type']:<15} {rel:<35} {meta.get('string','')}{flag}")


def try_read_group_by(client, uid, model, groupby_field, label):
    subheader(f"read_group by {groupby_field} ({label})")
    try:
        rows = read_group(client, uid, model, [], ["__count"], [groupby_field])
        for r in rows:
            val = r.get(groupby_field, "?")
            cnt = r.get("__count", 0)
            print(f"    {str(val):<40} {cnt:>8}")
    except Exception as e:
        print(f"    Could not group by {groupby_field}: {e}")


def model_exists(client, uid, model_name):
    try:
        rows = search_read(client, uid, "ir.model",
                           [["model", "=", model_name]], ["model"], limit=1)
        return len(rows) > 0
    except Exception:
        return False


def pick_model(candidates, found_model_names, client, uid):
    for c in candidates:
        if c in found_model_names:
            return c
    for c in candidates:
        if model_exists(client, uid, c):
            return c
    return None


def deep_dive(client, uid, model_name, section_label,
              state_fields=None, type_field=None, crm_link_fields=None):
    if not model_exists(client, uid, model_name):
        print(f"  Model '{model_name}' NOT FOUND in ir.model — skipping.")
        return {}

    cnt = safe_count(client, uid, model_name)
    print(f"\n  Model: {model_name}")
    print(f"  Total records: {cnt}")

    subheader("fields_get")
    flds = try_fields_get(client, uid, model_name)
    highlight = list(crm_link_fields or [])
    if state_fields:
        highlight += list(state_fields)
    if type_field:
        highlight.append(type_field)
    print_fields(flds, highlight=highlight)

    subheader("3 sample records (search_read)")
    try:
        samples = search_read(client, uid, model_name, [],
                              list(flds.keys())[:25], limit=3)
        for i, rec in enumerate(samples, 1):
            print(f"\n  --- Sample {i} ---")
            for k, v in list(rec.items())[:25]:
                print(f"    {k:<40} = {sanitize(v, k)}")
    except Exception as e:
        print(f"    search_read failed: {e}")

    for sf in (state_fields or []):
        if sf in flds:
            try_read_group_by(client, uid, model_name, sf, "state distribution")

    if type_field and type_field in flds:
        try_read_group_by(client, uid, model_name, type_field, "type distribution")

    return {"model": model_name, "count": cnt, "fields": flds}


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main(stop_after_section=None):
    import sys
    from io import StringIO

    output_buffer = StringIO()

    class Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
        def flush(self):
            for s in self.streams:
                s.flush()

    sys.stdout = Tee(sys.__stdout__, output_buffer)

    # Model variables — initialized here so Section 11 is always clean
    inst_model     = None
    res_model      = None
    contract_model = None
    inst_info      = {}

    record_counts = {}

    print(SEP)
    print("  Module 2 — Collections Discovery Phase 1")
    print(f"  Run at: {datetime.now().isoformat()}")
    print("  READ-ONLY: search_read, read_group, search_count, fields_get")
    print("  No writes. No OpenAI.")
    if stop_after_section:
        print(f"  DRY-RUN MODE: stopping after Section {stop_after_section}")
    print(SEP)

    with httpx.Client() as client:

        # ── Auth ──────────────────────────────────────────────────────────────
        print("\n[AUTH] Authenticating...")
        uid = rpc(client, "common", "authenticate",
                  [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
        if not uid:
            raise RuntimeError("Auth failed — check .env")
        print(f"  OK uid={uid}")

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 1: Installed Custom Apps Discovery
        # ═════════════════════════════════════════════════════════════════════
        header(1, "Installed Custom Apps Discovery")

        name_patterns = [
            "real_estate", "realestate", "rs_", "collections",
            "installment", "reservation", "contract", "amendment",
        ]

        all_installed = search_read(
            client, uid, "ir.module.module",
            [["state", "=", "installed"]],
            ["name", "shortdesc", "application", "state"],
            limit=500,
        )
        matched = [
            m for m in all_installed
            if any(p in m["name"].lower() for p in name_patterns)
        ]
        print(f"\n  Found {len(matched)} matching installed modules:\n")
        print(f"  {'NAME':<45} {'APP':>5}  SHORT DESCRIPTION")
        print(f"  {'-'*45} {'-'*5}  {'-'*40}")
        for m in sorted(matched, key=lambda x: x["name"]):
            app = "YES" if m.get("application") else "no"
            print(f"  {m['name']:<45} {app:>5}  {m.get('shortdesc','')[:60]}")

        if stop_after_section == 1:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 2: Model Inventory by Pattern Match
        # ═════════════════════════════════════════════════════════════════════
        header(2, "Model Inventory by Pattern Match")

        model_patterns = [
            "installment", "reservation", "contract", "amendment",
            "payment_term", "payment_plan", "check", "penalty",
            "discount", "real_estate", "real.estate", "rs.",
        ]
        prefix_patterns = ["x_", "real.estate.", "rs."]

        all_models = search_read(
            client, uid, "ir.model",
            [],
            ["model", "transient", "state", "name"],
            limit=2000,
        )

        def model_matches(m):
            mn = m["model"].lower()
            return (any(p in mn for p in model_patterns) or
                    any(mn.startswith(p) for p in prefix_patterns))

        matched_models = [m for m in all_models if model_matches(m)]
        print(f"\n  Found {len(matched_models)} matching models:\n")
        print(f"  {'MODEL':<55} {'TRANS':>5}  {'STATE':<10}  LABEL")
        print(f"  {'-'*55} {'-'*5}  {'-'*10}  {'-'*40}")
        for m in sorted(matched_models, key=lambda x: x["model"]):
            trans = "YES" if m.get("transient") else "no"
            print(f"  {m['model']:<55} {trans:>5}  {m.get('state',''):<10}  {m.get('name','')[:50]}")

        found_model_names = {m["model"] for m in matched_models}

        if stop_after_section == 2:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 3: Installment Model Deep Dive
        # ═════════════════════════════════════════════════════════════════════
        header(3, "Installment Model Deep Dive")

        installment_candidates = [
            "real.estate.installment",
            "rs.installment",
            "account.installment",
            "collection.installment",
            "real.estate.payment.installment",
        ]
        inst_model = pick_model(installment_candidates, found_model_names, client, uid)
        if not inst_model:
            inst_model = next((m for m in found_model_names
                               if "installment" in m), None)
        print(f"\n  Identified installment model: {inst_model or 'NOT FOUND'}")

        crm_link_fields = [
            "user_id", "partner_id", "crm_lead_id", "opportunity_id",
            "salesperson_id", "team_id",
        ]
        state_fields_inst = ["state", "payment_state", "payment_status",
                             "accounting_state", "status"]
        if inst_model:
            inst_info = deep_dive(
                client, uid, inst_model, "Installment",
                state_fields=state_fields_inst,
                type_field="installment_type",
                crm_link_fields=crm_link_fields,
            )
            record_counts[inst_model] = inst_info.get("count", 0)

        if stop_after_section == 3:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 4: Reservation Model Deep Dive
        # ═════════════════════════════════════════════════════════════════════
        header(4, "Reservation Model Deep Dive")

        res_candidates = [
            "real.estate.reservation",
            "rs.reservation",
            "sale.reservation",
            "real.estate.sale.reservation",
        ]
        res_model = pick_model(res_candidates, found_model_names, client, uid)
        if not res_model:
            res_model = next((m for m in found_model_names
                              if "reservation" in m), None)
        print(f"\n  Identified reservation model: {res_model or 'NOT FOUND'}")

        res_link_fields = [
            "user_id", "partner_id", "crm_lead_id", "opportunity_id",
            "unit_id", "project_id", "building_id", "payment_term_id",
        ]
        if res_model:
            res_info = deep_dive(
                client, uid, res_model, "Reservation",
                state_fields=["state", "stage_id"],
                crm_link_fields=res_link_fields,
            )
            record_counts[res_model] = res_info.get("count", 0)

        if stop_after_section == 4:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 5: Contract Model Deep Dive
        # ═════════════════════════════════════════════════════════════════════
        header(5, "Contract Model Deep Dive")

        contract_candidates = [
            "real.estate.contract",
            "rs.contract",
            "sale.contract",
            "real.estate.sale.contract",
        ]
        contract_model = pick_model(contract_candidates, found_model_names, client, uid)
        if not contract_model:
            contract_model = next(
                (m for m in found_model_names
                 if "contract" in m and "amendment" not in m), None
            )
        print(f"\n  Identified contract model: {contract_model or 'NOT FOUND'}")

        contract_link_fields = [
            "user_id", "partner_id", "reservation_id", "unit_id",
            "salesperson_id", "crm_lead_id",
        ]
        if contract_model:
            contract_info = deep_dive(
                client, uid, contract_model, "Contract",
                state_fields=["state"],
                crm_link_fields=contract_link_fields,
            )
            record_counts[contract_model] = contract_info.get("count", 0)

        if stop_after_section == 5:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 6: Payment Term & Payment Plan Models
        # ═════════════════════════════════════════════════════════════════════
        header(6, "Payment Term & Payment Plan Models")

        plan_candidates = [
            "real.estate.payment.term",
            "rs.payment.term",
            "real.estate.payment.plan",
            "rs.payment.plan",
            "real.estate.special.payment.plan",
        ]
        for candidate in plan_candidates:
            if model_exists(client, uid, candidate):
                cnt = safe_count(client, uid, candidate)
                record_counts[candidate] = cnt
                print(f"\n  {candidate}: {cnt} records")
                subheader("fields_get (approval/state fields only)")
                flds = try_fields_get(client, uid, candidate)
                approval_keys = [k for k in flds
                                 if any(w in k for w in
                                        ["state", "status", "approval", "stage"])]
                for k in approval_keys:
                    print(f"    {k:<40} {flds[k]['type']:<15} {flds[k].get('string','')}")
                for sf in approval_keys:
                    try_read_group_by(client, uid, candidate, sf,
                                      "approval state distribution")

        if stop_after_section == 6:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 7: Check Models (RS Accounting)
        # ═════════════════════════════════════════════════════════════════════
        header(7, "Check Models (RS Accounting)")

        check_candidates = [
            "rs.check",
            "real.estate.check",
            "account.check",
            "rs.receivable.check",
            "rs.payable.check",
            "rs.suspension.check",
            "rs.check.lot",
            "real.estate.receivable.check",
        ]
        seen_check_models = set()
        for candidate in check_candidates:
            if model_exists(client, uid, candidate):
                seen_check_models.add(candidate)
                cnt = safe_count(client, uid, candidate)
                record_counts[candidate] = cnt
                print(f"\n  {candidate}: {cnt} records")
                flds = try_fields_get(client, uid, candidate)
                state_keys = [k for k in flds if "state" in k or "status" in k]
                for sf in state_keys:
                    try_read_group_by(client, uid, candidate, sf, "check state")

        # Pattern sweep for any remaining check models
        for cm in sorted(found_model_names):
            if "check" in cm and cm not in seen_check_models:
                seen_check_models.add(cm)
                cnt = safe_count(client, uid, cm)
                record_counts[cm] = cnt
                print(f"\n  (pattern match) {cm}: {cnt} records")
                flds = try_fields_get(client, uid, cm)
                state_keys = [k for k in flds if "state" in k or "status" in k]
                for sf in state_keys[:2]:
                    try_read_group_by(client, uid, cm, sf, "state")

        if stop_after_section == 7:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 8: Payment Models
        # ═════════════════════════════════════════════════════════════════════
        header(8, "Payment Models")

        subheader("account.payment — counts by payment_type and state")
        ap_cnt = safe_count(client, uid, "account.payment")
        record_counts["account.payment"] = ap_cnt
        print(f"  account.payment total: {ap_cnt}")
        try_read_group_by(client, uid, "account.payment", "payment_type", "type")
        try_read_group_by(client, uid, "account.payment", "state", "state")

        payment_candidates = [
            "rs.payment",
            "real.estate.payment",
            "rs.cash.payment",
            "rs.bank.payment",
        ]
        for candidate in payment_candidates:
            if model_exists(client, uid, candidate):
                cnt = safe_count(client, uid, candidate)
                record_counts[candidate] = cnt
                print(f"\n  {candidate}: {cnt} records")
                flds = try_fields_get(client, uid, candidate)
                inst_link = [k for k in flds
                             if "installment" in k or "invoice" in k]
                print(f"  Installment-link fields: {inst_link}")

        if stop_after_section == 8:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 9: Penalty and Discount Models
        # ═════════════════════════════════════════════════════════════════════
        header(9, "Penalty and Discount Models")

        seen_penalty_models = set()
        penalty_candidates = [
            "real.estate.penalty",
            "rs.penalty",
            "real.estate.discount",
            "rs.discount",
        ]
        pattern_extras = [
            m for m in found_model_names
            if ("penalty" in m or "discount" in m)
        ]
        for candidate in penalty_candidates + pattern_extras:
            if candidate in seen_penalty_models:
                continue
            seen_penalty_models.add(candidate)
            if model_exists(client, uid, candidate):
                cnt = safe_count(client, uid, candidate)
                record_counts[candidate] = cnt
                print(f"\n  {candidate}: {cnt} records")
                flds = try_fields_get(client, uid, candidate)
                print_fields(flds)

        # Installment type=penalty count
        if inst_model and inst_info.get("fields"):
            subheader("Installment penalty-type record count")
            inst_flds = inst_info["fields"]
            type_field = next(
                (f for f in inst_flds if "type" in f and "installment" in f),
                None
            )
            if type_field:
                for val in ["penalty", "8", "غرامة", "penalties"]:
                    try:
                        cnt = search_count(client, uid, inst_model,
                                           [[type_field, "=", val]])
                        if cnt:
                            print(f"  {inst_model} where {type_field}='{val}': {cnt}")
                    except Exception:
                        pass

        if stop_after_section == 9:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 10: Project Structure Hierarchy
        # ═════════════════════════════════════════════════════════════════════
        header(10, "Project Structure Hierarchy")

        hierarchy_candidates = {
            "Project":  ["real.estate.project", "rs.project"],
            "Phase":    ["real.estate.phase", "rs.phase",
                         "real.estate.project.phase"],
            "Zone":     ["real.estate.zone", "rs.zone",
                         "real.estate.phase.zone"],
            "Building": ["real.estate.building", "rs.building",
                         "real.estate.zone.building"],
            "Unit":     ["real.estate.unit", "rs.unit",
                         "real.estate.building.unit",
                         "real.estate.property"],
        }

        for level, candidates in hierarchy_candidates.items():
            found = pick_model(candidates, found_model_names, client, uid)
            if not found:
                found = next(
                    (m for m in found_model_names if level.lower() in m), None
                )
            if found:
                cnt = safe_count(client, uid, found)
                record_counts[found] = cnt
                print(f"\n  {level}: {found} — {cnt} records")
                flds = try_fields_get(client, uid, found)
                parent_fields = [k for k in flds
                                 if any(p in k for p in
                                        ["parent", "phase_id", "zone_id",
                                         "building_id", "project_id"])]
                print(f"  Parent-link fields: {parent_fields}")
                try:
                    samples = search_read(client, uid, found, [],
                                          ["id", "name"] + parent_fields[:3], limit=2)
                    for rec in samples:
                        for k, v in rec.items():
                            print(f"    {k:<40} = {sanitize(v, k)}")
                except Exception as e:
                    print(f"  search_read failed: {e}")
            else:
                print(f"\n  {level}: NOT FOUND (tried {candidates})")

        if stop_after_section == 10:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 11: CRM <-> Collections Linkage (Critical)
        # ═════════════════════════════════════════════════════════════════════
        header(11, "CRM <-> Collections Linkage (Critical)")

        crm_fields_to_check = [
            "crm_lead_id", "opportunity_id", "lead_id",
            "user_id", "salesperson_id", "sale_team_id", "team_id",
        ]

        for label, model in [
            ("Installment", inst_model),
            ("Reservation", res_model),
            ("Contract",    contract_model),
        ]:
            if not model:
                print(f"\n  {label}: model unknown — skipping")
                continue
            print(f"\n  {label} ({model}):")
            try:
                flds = fields_get(client, uid, model)
                found_crm = {k: flds[k] for k in crm_fields_to_check if k in flds}
                if found_crm:
                    for k, meta in found_crm.items():
                        print(f"    FOUND: {k:<40} type={meta['type']:<15}"
                              f" relation={meta.get('relation','')}")
                    for k in found_crm:
                        try:
                            rows = search_read(client, uid, model,
                                               [[k, "!=", False]], [k], limit=1)
                            if rows:
                                print(f"    Sample {k} value: {sanitize(rows[0][k], k)}")
                        except Exception:
                            pass
                else:
                    print("    NONE of the CRM link fields found directly.")
                    indirect = [k for k in flds
                                if "reservation" in k or "contract" in k]
                    print(f"    Indirect path fields: {indirect}")
            except Exception as e:
                print(f"    Error reading fields: {e}")

        if stop_after_section == 11:
            _finish(sys, output_buffer, dry_run=True)
            return

        # ═════════════════════════════════════════════════════════════════════
        # SECTION 12: Final Summary — Record Counts & Pagination Strategy
        # ═════════════════════════════════════════════════════════════════════
        header(12, "Final Summary — Record Counts and Pagination Strategy")

        PAGINATION_THRESHOLD = 5_000
        print(f"\n  {'MODEL':<55} {'COUNT':>12}  PAGINATE?")
        print(f"  {'-'*55} {'-'*12}  {'-'*9}")
        for model_name, cnt in sorted(record_counts.items()):
            if isinstance(cnt, int):
                pag = "YES" if cnt > PAGINATION_THRESHOLD else "no"
                cnt_str = f"{cnt:,}"
            else:
                pag = "ERROR"
                cnt_str = str(cnt)[:12]
            print(f"  {model_name:<55} {cnt_str:>12}  {pag}")

    print(f"\n{SEP}")
    print("  Discovery complete. READ-ONLY. No writes. No OpenAI.")
    print(f"  Finished at: {datetime.now().isoformat()}")
    print(SEP)

    _finish(sys, output_buffer, dry_run=False)


def _finish(sys_mod, buffer, dry_run=False):
    sys_mod.stdout = sys_mod.__stdout__
    output_path = "scripts/discover_collections_output.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(buffer.getvalue())
    label = "Dry-run output" if dry_run else "Full output"
    print(f"\n  {label} saved to: {output_path}")


if __name__ == "__main__":
    import sys as _sys
    section_arg = None
    if "--section" in _sys.argv:
        idx = _sys.argv.index("--section")
        try:
            section_arg = int(_sys.argv[idx + 1])
        except (IndexError, ValueError):
            print("Usage: python scripts/discover_collections.py [--section N]")
            _sys.exit(1)
    main(stop_after_section=section_arg)
