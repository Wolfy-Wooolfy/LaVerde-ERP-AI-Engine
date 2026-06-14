# Marketing Attribution — Live-Data Discovery (numbers)

**Status:** Live read-only discovery run **2026-06-14** against `laverde.odoo.com`
(`crm.lead`). This document resolves the `[OPEN — needs live discovery]` items in
[MARKETING_ATTRIBUTION_DISCOVERY.md](MARKETING_ATTRIBUTION_DISCOVERY.md) with
real numbers. **Discovery only** — no build, no KPI design, no product decision.

**Method:** throwaway script
[scripts/discover_marketing_attribution.py](../scripts/discover_marketing_attribution.py),
READ-ONLY (`fields_get` / `search_count` / `read_group` / `search_read` only —
`ALLOWED_METHODS` untouched). All free-text classified **in Python**; only
aggregates, distinct *codes*, sanitized *shape* examples, and internal staff
labels are shown — **no customer PII**. AI cost = **$0.00**. Live data shifts
between runs, so the small counts (e.g. Lost ±a few) are a point-in-time snapshot.

> **Note on numbers:** the live DB mutates continuously. Counts in §3/§4 are from
> a single consistent run; §5 stage counts drift by a handful between runs. Treat
> all figures as "as of 2026-06-14", reproducible via the printed domains.

---

## 1. Field resolution (UI label → technical field)

`fields_get('crm.lead')` returned **172 fields**. Mapping of every vision-doc
label to its real technical field:

| UI label | technical field | type | target model / note |
|---|---|---|---|
| Campaign | `campaign_id` | many2one | `utm.campaign` |
| Campaign Name | `campaign_name` | char | free-text (the studio convention field) |
| Medium | `medium_id` | many2one | `utm.medium` |
| Source | `source_id` | many2one | `utm.source` |
| Channel | `channel_id` | many2one | `utm.channel` |
| **Media Buyer** | `direct_media_buyer_id` ⚠ | many2one | `res.users` — **2 fields share this label** |
| **Media Buyer Manager** | `direct_media_buyer_manager_id` ⚠ | many2one | `res.users` — **2 fields share this label** |
| Adset Name | `adset_name` | char | free-text |
| **Referred By** | `referred` ⚠ | char | also a `referred_by_id` (many2one `res.users`) shares the label |
| Sales Team | `team_id` | many2one | `crm.team` |
| Days to Assign | `day_open` | float | |
| Days to Close | `day_close` | float | |
| Stage | `stage_id` | many2one | `crm.stage` |

**Core semantics confirmed:** `stage_id` (many2one → `crm.stage`), `type`
(selection), `active` (boolean).

⚠ **FLAGS — ambiguous labels (do not guess; coverage decides — see §4):**
- **Media Buyer** has TWO fields labelled "Media Buyer": `direct_media_buyer_id`
  **and** `media_buyer_id` (both many2one → `res.users`).
- **Media Buyer Manager** likewise: `direct_media_buyer_manager_id` **and**
  `media_buyer_manager_id`.
- **Referred By** likewise: `referred` (char) **and** `referred_by_id` (many2one).

The script picked the first candidate (`direct_*`) as the nominal resolution, but
§4 measures coverage for **all** candidates because the choice is not obvious from
the label alone.

---

## 2. Population baseline

`search_count`, with the exact domain beside each number:

| population | count | domain |
|---|---:|---|
| active leads+opps (default) | 60,813 | `[]` |
| &nbsp;&nbsp;type = lead (active) | **0** | `[('type','=','lead')]` |
| &nbsp;&nbsp;type = opportunity (active) | 60,813 | `[('type','=','opportunity')]` |
| archived only | 86,001 | `[('active','=',False)]` |
| **ALL incl. archived** | **146,814** | `[('active','in',[True,False])]` |
| &nbsp;&nbsp;type = lead (incl. archived) | **0** | `[('active','in',[True,False]),('type','=','lead')]` |
| &nbsp;&nbsp;type = opportunity (incl. archived) | 146,814 | `[('active','in',[True,False]),('type','=','opportunity')]` |

**Key facts:**
- **Every record is `type = opportunity`** — there are **0** records with
  `type = lead`. The lead/opportunity split is therefore a non-distinction here;
  use the word "lead" loosely (= a `crm.lead` row).
- **Archived dominates:** 86,001 of 146,814 (58.6%) are `active = False`. These
  are mostly Lost/dead rows but still carry attribution **and** a stage.

**Chosen population for §3–§5:** **ALL incl. archived**
(`[('active','in',[True,False])]`, total **146,814**). Rationale: outcome analysis
must include archived (Lost/dead) leads — excluding them would hide exactly the
poor outcomes the domain is meant to surface. **FLAGGED for Khaled:** the
alternative is active-only (60,813); the final population choice is a product
decision, not made here. All §3–§5 numbers use the 146,814 population.

---

## 3. Campaign Name coverage & convention (the core)

Population = 146,814. `campaign_name` fetched as a **single field** (no PII
fields alongside); classification done in Python.

| metric | count | % |
|---|---:|---:|
| Campaign Name **non-empty** | **48,609** | **33.1%** of 146,814 |
| Campaign Name empty / false | 98,205 | 66.9% |
| **matches convention** `^[A-Za-z]{1,4}\s*-\s*.+` | **14,050** | **28.9%** of non-empty · **9.6%** of population |
| non-empty but does **not** match | 34,559 | 71.1% of non-empty |

So only **~1 in 10 leads** carries a Campaign Name that follows the
initials-then-dash convention. Two-thirds of all leads have no Campaign Name at
all, and of the third that do, **71% break the convention**.

### 3a. Initials → frequency (the real codes in use)

Over the 14,050 convention-matched values (part before the first `-`, trimmed +
upper-cased): **28 distinct codes**, sorted desc.

| code | leads | % of matched |
|---|---:|---:|
| YM | 10,821 | 77.0% |
| EEA | 1,000 | 7.1% |
| GGC | 401 | 2.9% |
| GCC | 333 | 2.4% |
| UAE | 255 | 1.8% |
| EXPO | 243 | 1.7% |
| AY | 164 | 1.2% |
| MIX | 164 | 1.2% |
| AD | 139 | 1.0% |
| NEW | 122 | 0.9% |
| CBO | 110 | 0.8% |
| ABO | 72 | 0.5% |
| EU | 67 | 0.5% |
| HM | 47 | 0.3% |
| KSA | 41 | 0.3% |
| ADS | 32 | 0.2% |
| LA | 6 | 0.0% |
| USA | 6 | 0.0% |
| PIN | 5 | 0.0% |
| TEST | 4 | 0.0% |
| LIVE | 3 | 0.0% |
| UK | 3 | 0.0% |
| NILE | 3 | 0.0% |
| CORE | 3 | 0.0% |
| SWED | 2 | 0.0% |
| EE | 2 | 0.0% |
| EX | 1 | 0.0% |
| GOLF | 1 | 0.0% |

**Observation (not a decision):** many "codes" are clearly **not initials** —
`EXPO`, `UAE`, `KSA`, `USA`, `UK`, `NEW`, `MIX`, `ADS`, `LIVE`, `TEST`, `CORE`,
`NILE`, `GOLF`, `SWED` read as places / campaign-type tags, not people. The
convention regex matches them structurally (1–4 letters + dash) but they do not
map to a Media Buyer. Only `YM` (77%) is a known buyer (Yomna Mosaad). This means
even within the "matching" set, the initials are **not uniformly a buyer code**.

### 3b. Non-match shape categories

Of the 34,559 non-empty values that fail the convention (mutually-exclusive,
checked in order). Examples are PII-sanitized; `mask` shows the character shape
(A=letter, 9=digit):

| category | count | % of non-match | sanitized example | mask |
|---|---:|---:|---|---|
| `no_dash` (no `-` at all) | 22,607 | 65.4% | `KSA EXPO` | `AAA AAAA` |
| `long_or_multiword_before_dash` (before-dash isn't 1–4 letters) | 11,869 | 34.3% | `jadda - righad expo AY` | `AAAAA - AAAAAA AAAA AA` |
| `dash_no_text_after` | 53 | 0.2% | `EEA-` | `AAA-` |
| `leading_digits` | 27 | 0.1% | `2 - Copy` | `9 - AAAA` |
| `other` (e.g. leading dash) | 3 | 0.0% | `- righad expo AY - NEW WAVE` | `- AAAAAA AAAA AA - AAA AAAA` |

The two dominant failure modes — **no dash (65%)** and **a long/multi-word phrase
before the dash (34%)** — are exactly the operator free-text drift the vision doc
warned about. Note the `no_dash` example `KSA EXPO` and the multi-word example
both still appear to encode a *campaign* but **no parseable buyer initials**.

### 3c. Proposed `initials → Media Buyer` mapping skeleton

Only `YM` is pre-filled (from the vision doc). **All others left blank for Khaled**
— do not assume the non-`YM` codes are people (see 3a):

| code | leads | Media Buyer (Khaled to fill) |
|---|---:|---|
| YM | 10,821 | **Yomna Mosaad** |
| EEA | 1,000 | _________ |
| GGC | 401 | _________ |
| GCC | 333 | _________ |
| UAE | 255 | _________ |
| EXPO | 243 | _________ |
| AY | 164 | _________ |
| MIX | 164 | _________ |
| AD | 139 | _________ |
| NEW | 122 | _________ |
| CBO | 110 | _________ |
| ABO | 72 | _________ |
| EU | 67 | _________ |
| HM | 47 | _________ |
| KSA | 41 | _________ |
| ADS | 32 | _________ |
| LA | 6 | _________ |
| USA | 6 | _________ |
| PIN | 5 | _________ |
| TEST | 4 | _________ |
| LIVE | 3 | _________ |
| UK | 3 | _________ |
| NILE | 3 | _________ |
| CORE | 3 | _________ |
| SWED | 2 | _________ |
| EE | 2 | _________ |
| EX | 1 | _________ |
| GOLF | 1 | _________ |

---

## 4. Dedicated field coverage (the alternative to parsing)

One coverage fetch over the 146,814 population (no customer PII fields). "% " =
populated / population.

| dimension | field | populated | % | distinct |
|---|---|---:|---:|---:|
| Media Buyer (primary) | `direct_media_buyer_id` | 38,453 | 26.2% | 5 |
| Media Buyer Manager (primary) | `direct_media_buyer_manager_id` | 366 | 0.2% | 1 |
| Adset Name | `adset_name` | 47,365 | 32.3% | 1,702 |
| Campaign (UTM) | `campaign_id` | 146,799 | **100.0%** | 212 |
| Medium | `medium_id` | 146,691 | **99.9%** | 42 |
| Source | `source_id` | 146,764 | **100.0%** | 33 |
| Channel | `channel_id` | 146,787 | **100.0%** | 2 |
| Referred By | `referred` | 4 | 0.0% | 3 |
| Sales Team | `team_id` | 140,269 | 95.5% | 9 |

### 4a. Ambiguous-label candidates (both fields measured)

| candidate field | populated | % | distinct |
|---|---:|---:|---:|
| `direct_media_buyer_id` | 38,453 | 26.2% | 5 |
| **`media_buyer_id`** | **50,953** | **34.7%** | **6** |
| `direct_media_buyer_manager_id` | 366 | 0.2% | 1 |
| `media_buyer_manager_id` | 1 | 0.0% | 1 |
| `referred` | 4 | 0.0% | 3 |
| `referred_by_id` | 0 | 0.0% | 0 |

**Critical:** the *other* Media Buyer field, **`media_buyer_id`**, has the
**highest single-signal coverage at 34.7%** (50,953 leads) — better than both
`direct_media_buyer_id` (26.2%) and the convention match (9.6% of population).
The Manager fields and Referred-By fields are effectively **unpopulated**.

### 4b. Top values (internal staff labels — safe to show)

`direct_media_buyer_id` — 5 distinct:

| leads | Media Buyer |
|---:|---|
| 18,929 | Ahmed Aymen |
| 8,320 | Yomna Musaad |
| 7,440 | Abdallah Maher |
| 3,683 | Ali shaban |
| 81 | Mahmoud Mohsen |

`direct_media_buyer_manager_id` — 1 distinct: Hagar Elsayed (366).

> Note: "Yomna Musaad" appears here as a structured buyer **and** as the `YM`
> Campaign-Name code — the two signals overlap for her but not identically
> (8,320 structured rows vs 10,821 `YM` convention rows).

**Attribution dimensions usable today:** the structured UTM fields — `campaign_id`
(100%), `source_id` (100%), `channel_id` (100%, 2 values), `medium_id` (99.9%) —
are essentially **fully populated**. `team_id` is 95.5%. `adset_name` 32.3%.

---

## 5. Stage availability & end-to-end overlap

### 5a. Stage distribution (`read_group` on `stage_id`, population 146,814)

**18 distinct stages** present (matches the documented 18), plus 1,416 rows with
no stage:

| stage | leads |
|---|---:|
| Re-Distribution | 68,019 |
| New | 31,142 |
| Lost | 14,014 |
| Contact in the Future | 10,855 |
| Follow up | 5,978 |
| Unqualified | 5,763 |
| No Answer | 4,765 |
| New X | 2,227 |
| _(no stage)_ | 1,416 |
| Down Payment Confirm & Contracted | 1,155 |
| Interested | 922 |
| Wrong Number | 349 |
| Initial Reservation | 61 |
| Cancel Reservation | 57 |
| Reservation | 52 |
| Cancel Contract | 29 |
| Unavailable Request | 8 |
| Bought Out | 2 |
| **TOTAL** | **146,814** |

Stage coverage is excellent: **145,398 / 146,814 = 99.0%** of leads have a stage.
(Stage counts drift by a few between runs as the DB mutates.)

### 5b. End-to-end overlap (usable buyer signal AND a stage)

"Usable buyer signal" = convention-matching Campaign Name **OR** a populated
Media Buyer field. Two definitions of the Media-Buyer field:

| definition | signal-bearing leads | % of pop | also have a stage | % of pop |
|---|---:|---:|---:|---:|
| **A** — `direct_media_buyer_id` only | 45,709 | 31.1% | 45,648 | 31.1% |
| **B** — best (conv OR `direct_media_buyer_id` OR `media_buyer_id`) | 56,763 | 38.7% | **56,678** | **38.6%** |

Component counts (definition A): convention-matching Campaign Name = 14,050;
`direct_media_buyer_id` populated = 38,453.

**So the most we can do Media-Buyer × stage analysis on today is ~56,678 leads
(38.6% of all leads)** even when we combine *every* available buyer signal. The
other ~61% have a stage but **no usable buyer attribution at all**. Of the leads
that DO have a buyer signal, **99.9% also have a stage** — i.e. when a buyer
signal exists, the outcome (stage) is almost always available; the bottleneck is
the buyer signal, not the stage.

---

## 6. Verdict — answering THE DECISIVE QUESTION

> *Is `Campaign Name` reliable enough to attribute leads to Media Buyers?*

**No — `Campaign Name` alone is not reliable, and the dedicated field is the
stronger (but still partial) signal. Recommendation for Khaled to review (not a
decision executed here):**

`Campaign Name` follows the initials convention on only **14,050 leads — 9.6% of
the population** (28.9% of the third that even carry the field). Within that
matching set, **77% is a single code (`YM`)** and many other "codes"
(`EXPO`, `UAE`, `KSA`, `NEW`, `MIX`…) are places/campaign tags, **not buyers** —
so the *true* buyer-mappable share is smaller still and needs Khaled's
code→person confirmation. The free text is dominated by two failure modes (65%
have no dash, 34% have a long/multi-word prefix), confirming the operator-drift
risk. By contrast the **dedicated `media_buyer_id` field covers 34.7% (50,953
leads)** with only 6 clean distinct staff values — a materially stronger and
cleaner signal than parsing, though it too leaves most leads unattributed. The
manager and referred-by fields are unusable (≈0%). Combining **all** signals
(convention OR either media-buyer field) maxes out at **56,678 leads (38.6%) with
both a buyer and a stage** — that is the realistic ceiling for a Media-Buyer ×
outcome analysis on current data. **Recommendation:** treat **`media_buyer_id` as
the primary attribution key**, use convention-parsed `Campaign Name` only as a
*fallback* for the ~7k leads it adds, confirm the `YM`→Yomna and the
code→person map for the rest, and explicitly scope any future KPI to the
~38–39% of leads that are attributable — surfacing the un-attributable ~61% as a
data-quality gap for Operations rather than silently dropping it. The fully
populated UTM fields (`campaign_id`/`source_id`/`channel_id`/`medium_id`, ~100%)
are reliable for campaign/channel views but do **not** identify the Media Buyer.

---

*Discovery only. No KPI designed, no product decision made, no Odoo write. Read-only,
aggregates only, $0.00 AI.*
