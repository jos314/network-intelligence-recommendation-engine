# Data model

The engine runs on six relational tables (HBUS PoC schema). This page
documents each table, the identity problems between them, and how the
crosswalk + entity resolution fix those problems before any join or edge
is built.

## The six tables

Profiled shapes from the real PoC extract:

| Table | Rows | Role |
|---|---|---|
| `TRANSACTIONS` | 802,061 | **pre-aggregated edge list** — one row per originator→beneficiary pair, not raw transactions |
| `CUSTOMER_ACCOUNT_LINK` | 1,048,575 | customer ↔ account holdings |
| `CUSTOMERS` | 398,390 | party/entity master (KYC attributes) |
| `COUNTRY` | 272 | jurisdiction risk lookup |
| `ALERTS` | 692 | TM alert flag (L2–L3; no dates carried — recency unknown) |
| `CASE_CUSTOMERS` | 6 | the case subjects / graph seeds |

### CUSTOMERS
| Column | Notes |
|---|---|
| `CUSTOMER_ID` | **zero-padded string**, e.g. `000031155` |
| `CUSTOMER_NAME` | used for watchlist matching (source still open — Q5) |
| `ADDRESS` | ~99% populated — shared-address identity links |
| `PEP_FLAG` | **~17% populated** |
| `PHONE_NUMBER` | **~16%** — shared-phone links; may be `<NA>` |
| `EMAIL_ADDRESS` | **~18%** — shared-email links |
| `CRR` | **~19%** — KYC Customer Risk Rating |

KYC is sparse by design of the extract: presence is a strong signal,
absence is *unknown* — and opacity itself scores as a (small) risk
component, never as safety.

### TRANSACTIONS
| Column | Notes |
|---|---|
| `CREDIT_DEBIT_CODE` | **DEBIT** ⇒ funds flow originator→beneficiary; **CREDIT** ⇒ beneficiary→originator. Edges are oriented along **money flow**. |
| `ORIGINATOR_KEY` | zero-padded, matches CUSTOMERS format |
| `BENEFICIARY_KEY` | **pseudonymized external**: `PSEUDO_101595944` |
| `ORIGINATOR_COUNTRY` / `BENEFICIARY_COUNTRY` | ~81% / **~32%** populated |
| `txn_count`, `total_amount_base` | edge multiplicity and value |
| `first_run_date`, `last_run_date` | activity window → time-decay in scoring |

### CUSTOMER_ACCOUNT_LINK
`ACCOUNT_ID` suffix encodes the product (HDD, DDA, MVI, INV, HTD, TDA…);
`CUSTOMER_ID` is a **plain int**; `TO_DATE = 2099-12-31` means still active.
Exactly 1,048,575 rows = 2²⁰−1 = Excel's row limit → possible truncation
(open question Q4; the quality check flags it on every ingest).

### ALERTS
Single column, **mixed formats**: plain ints (`576700078`) and pseudonyms
(`PSEUDO_719247954`). Presence = the entity has triggered a TM alert. The extract carries no
alert dates, so recency is unknown — the UI and drivers say so explicitly.

### COUNTRY
`COUNTRY_RISK` bands observed: **HIGH / MEDIUM / STANDARD** (spec said LOW —
open question Q2; the scorer maps STANDARD and LOW identically).

### CASE_CUSTOMERS
`CASE_ID`, `CUSTOMER_ID` (plain int), `LOB` (CMB / WPB). These six are the
seeds the app scores.

## The identity problem and the crosswalk (P0)

The same customer appears **three different ways** across tables:

| Where | Raw form | Canonical rule |
|---|---|---|
| CUSTOMERS, TRANSACTIONS originator | `000031155` | strip leading zeros → `"31155"` |
| CUSTOMER_ACCOUNT_LINK, CASE_CUSTOMERS | `31155` (int) | cast to string → `"31155"` |
| TRANSACTIONS beneficiary, some ALERTS | `PSEUDO_101595944` | keep verbatim; `node_type = external_pseudo` |

Canonical id = *integer value as a string* for real customers, `PSEUDO_…`
verbatim for externals. `src/ingest/crosswalk.py` builds a registry
`canonical_id → {raw_forms, node_type, source_tables}` from every id column
of all six tables. **Nothing joins on raw ids anywhere in the codebase.**

Structural consequence: beneficiaries are pseudonymized externals with no
KYC row, so most edges run *real customer → external counterparty*.
External nodes carry only graph-derived features; the scorer treats missing
KYC as its own signal.

## Shared-attribute entity resolution (P0)

`src/ingest/entity_resolution.py` normalizes `PHONE_NUMBER` (digits only,
≥7), `EMAIL_ADDRESS` (lowercase, must contain `@`), `ADDRESS` (lowercase,
punctuation stripped, whitespace collapsed), then links any two parties
sharing a normalized value.

**Over-linking guard** (a corporate HQ address or call-centre phone must not
weld hundreds of parties together):

| Parties sharing the value | Treatment |
|---|---|
| ≤ 5 (`ER_STRONG_GROUP`) | strong link, weight 1.0 |
| 6–20 (`ER_MAX_GROUP`) | down-weighted 1/log₂(n) |
| > 20 | dropped as noise |

## Data-quality checklist

`src/ingest/quality.py` runs on every ingest and reports:
`COUNTRY_RISK` band set (Q2), `CUSTOMER_ACCOUNT_LINK` row count vs the Excel
limit (Q4), presence of CREDIT rows (Q3), beneficiary-country coverage,
KYC field coverage. Findings are printed, never silently swallowed.

## Real data — how to switch off the demo fixture

Drop the six tables into `data/` named exactly
`TRANSACTIONS`, `CUSTOMERS`, `CUSTOMER_ACCOUNT_LINK`, `ALERTS`, `COUNTRY`,
`CASE_CUSTOMERS`, with any of `.parquet` / `.csv` / `.xlsx`.

* Loaders (`src/ingest/loaders.py`) pick them up automatically; the demo
  fixture is used **only** when one or more files are missing (a warning
  names which).
* ID columns are forced to string dtype at read time so zero-padding and
  `PSEUDO_` forms survive; the crosswalk handles the rest.
* Nothing else changes: pipeline, scores, app, and docs all follow the data.

The demo fixture (`src/ingest/synthetic.py`) replicates every quirk above —
formats, sparsity, mixed ALERTS ids, risk bands, the six real subject ids —
and plants one detectable typology per case so every scoring stage has
something to find. It exists so the whole system is testable end-to-end
before the real extract is available.
