# Marketing Attribution — Domain Discovery (Vision)

**Status:** Vision captured 2026-06-14. Live-data discovery NOT yet done —
all items tagged `[OPEN — needs live discovery]` are unverified against the
Odoo instance and must not be treated as facts until a dedicated read-only
discovery session produces the numbers.

**Module classification:** New analytics domain (Marketing / Media-Buyer
Performance). This is NOT a small task — it follows the full module path:
vision doc → live-data discovery → product decisions → build → identity-equal
verification. Read-only intelligence layer only; never writes to Odoo.

---

## 1. Purpose — the question this domain must answer

La Verde's leads are produced by the Marketing team — specifically the
**Media Buyers**, who run paid campaigns on social media that generate the
leads the sales floor works.

The board-level question is NOT "who generated the most leads" (volume).
It is **"what do each Media Buyer's leads turn into?"** (outcome / quality):

- Of the leads a given Media Buyer brought in, how are they distributed
  across the CRM stages?
- How many became genuinely interested, how many reached purchase
  (Reservation / Down Payment Confirm & Contracted), and how many produced
  no result (No Answer, Wrong Number, Lost, etc.)?
- Which campaigns produced good *outcomes* (so Marketing can repeat the
  ones that actually convert, not just the ones that generate noise)?

A Media Buyer who brings 1,000 leads that are all "No Answer" is worse than
one who brings 200 leads where a meaningful share buy. Volume alone is
misleading; this domain measures **conversion outcome**, not headcount of
leads.

---

## 2. How leads enter the system (the workflow)

1. Media Buyers run paid social-media campaigns and collect the resulting
   leads.
2. They hand the leads to the Operations team as sheets.
3. Operations uploads them into Odoo **in bulk**, filling in the campaign /
   attribution metadata for each lead.
4. That metadata appears inside each lead under the **Extra Information**
   tab.

This means attribution data is **operator-entered free text uploaded in
bulk** — a critical fact for data quality (see §5).

---

## 3. The UTM structure in Odoo (Link Tracker → UTMs)

Odoo's Link Tracker app exposes four UTM dimensions. Observed from the live
UI (counts are point-in-time references, not load-bearing facts):

- **Channels** — only two values: `Digital` and `Offline`.
- **Campaigns** — the organised UTM campaign records (the UI showed ~296
  records; each campaign has a Responsible and a linked set of
  opportunities, e.g. one campaign showed 271 opportunities). A campaign
  record also carries `Mediums` (e.g. "Exhibitions").
- **Mediums** — ~72 values (e.g. Dubizzle, Google Adwords, LinkedIn, Cold
  Calls, Banner, Broker, …).
- **Sources** — ~58 values, each mapped to a Channel (e.g. Facebook →
  Digital, Google → Digital, Property Finder → Digital, …).

`[OPEN — needs live discovery]` exact current counts and the full
value lists for each dimension.

---

## 4. The lead's attribution fields (Extra Information tab)

Inside a CRM lead, the **Extra Information** tab carries the marketing
attribution. Fields observed:

- **MARKETING:** Campaign, Medium, Source, Channel, Referred By.
- **MEDIA BUYING:** Media Buyer, Media Buyer Manager, Adset Name,
  Campaign Name.
- **TRACKING:** Sales Team, Days to Assign, Days to Close.

The lead also exposes related tabs not central to this domain but noted for
context: Internal Notes, Stage History (Changed By / Current Stage / Next
Stage / Date / Time Spent), SalePerson History (reassignment trail),
Recommended Units, Client Legal (attachments).

`[OPEN — needs live discovery]` the real Odoo field names / technical
identifiers behind each of the above labels (the build will need the actual
model + field names, not the UI labels), and which fields are reliably
populated vs mostly empty (the observed lead had `Media Buyer` and
`Media Buyer Manager` blank while `Campaign Name` was populated).

---

## 5. CRITICAL distinction — `Campaign` is NOT `Campaign Name`

These are two different fields and the difference is the crux of this
domain:

| Field | Meaning | Example |
|---|---|---|
| **Campaign** | The structured UTM campaign record | `FB-LA` |
| **Campaign Name** | A free-text label: the Media Buyer's initials + the real social-media campaign name | `YM-GCC ABO LAVERDE` |

In **Campaign Name**, the segment before the first `-` is the **Media
Buyer's initials**, and the remainder is the actual campaign name as run on
social media:

- `YM-GCC ABO LAVERDE` → `YM` = **Yomna Mosaad** (the Media Buyer who
  brought this bulk); `GCC ABO LAVERDE` = the campaign name run on social
  media (kept so a well-performing campaign can be repeated).

So the Media Buyer attribution is, in practice, **encoded inside the
free-text `Campaign Name` via an initials convention** — not (reliably) in
the dedicated `Media Buyer` field, which was observed empty.

---

## 6. The core data-quality risk (must be quantified before any build)

Extracting "which Media Buyer" from `Campaign Name` depends on a
**convention** (initials, then `-`, then campaign name). This convention is
operator-entered and therefore may not hold uniformly:

- Some rows may not follow the `XX-...` shape at all (missing initials,
  different separator, free spelling).
- The same Media Buyer may appear under more than one initials code.
- Historical rows may use older or inconsistent formats.
- The dedicated `Media Buyer` field may be empty even when `Campaign Name`
  encodes a buyer.

This is exactly the class of trap that live data reveals and theory hides.
**No KPI may be designed on top of `Campaign Name` parsing until a
read-only discovery has measured, on the live instance:**

`[OPEN — needs live discovery]`
- How many leads have a non-empty `Campaign Name`.
- Of those, how many match the `XX-...` initials convention.
- The full set of distinct initials codes actually in use, and a
  proposed initials → Media Buyer name mapping for Khaled to confirm.
- Whether the dedicated `Media Buyer` field is ever populated and, if so,
  how its coverage compares to the `Campaign Name` convention.
- Whether stage data needed for outcome analysis is reliably present on
  the same leads.

---

## 7. The intended analysis (outcome, not volume)

Once attribution is trustworthy, the target is a **Media Buyer × outcome**
view:

- For each Media Buyer: the distribution of their leads across CRM stages
  (the 18 real stages — see docs/PHASE_5_BUG_HUNT.md / the live stage list:
  New, New X, Lost, No Answer, Wrong Number, Follow up, Interested, Contact
  in the Future, Re-Distribution, Unqualified, Unavailable Request, Cancel
  Reservation, Bought Out, Cancel Contract, Draft Reservation, Initial
  Reservation, Reservation, Down Payment Confirm & Contracted).
- Outcome buckets to be defined with Khaled, conceptually grouping stages
  into e.g. "no result", "engaged/interested", "purchased"
  `[OPEN — grouping needs Khaled's product decision]`.
- A campaign-level outcome view (which real social campaigns convert), so
  Marketing repeats what works.

Arabic terminology rule applies to any UI: always "موظف مبيعات" /
"موظفي مبيعات", never "مندوب". (Media Buyer terminology to be settled with
Khaled when UI work begins.)

---

## 8. Next steps (sequenced — do not skip)

1. **This doc** — vision captured. ✅
2. **Live-data discovery session (read-only)** — resolve every
   `[OPEN — needs live discovery]` item above: real model/field names,
   `Campaign Name` coverage + convention match rate, distinct initials
   codes, `Media Buyer` field coverage, stage availability. Output a
   numbers report + a proposed initials→buyer map for Khaled to confirm.
3. **Product decisions** — confirm the initials→buyer map, define the
   outcome buckets (stage groupings), confirm Media-Buyer-Manager rollups.
4. **Build + identity-equal verification** — per the project's standard KPI
   discipline (live verification mandatory; card == list == direct).

**Read-only guarantee:** every step above is read-only against Odoo. This
domain never writes to Odoo.
