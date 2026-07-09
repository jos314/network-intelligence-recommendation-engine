# Data model

The app has **two ingestion paths**, chosen automatically by
`src/data_access.py`:

1. **Prebuilt masked graph** (`GRAPH_NODES` + `GRAPH_EDGES` parquet) — the
   format of the real masked HBUS extract, and **what the app runs on today**.
   The ID crosswalk and shared-contact resolution are already baked into
   these files, so ingestion is a load, not a build.
2. **Six raw relational tables** — the original from-scratch path that builds
   the graph itself (crosswalk → entity resolution → graph). Used for the
   in-code demo fixture and the test suite.

`DataAccess` picks **prebuilt** whenever `GRAPH_NODES.parquet` +
`GRAPH_EDGES.parquet` are found in `data/` or `data/synthetic/`, otherwise it
falls back to the demo fixture (`NIRE_DATA_SOURCE` overrides; tests force
`demo`).

---

# Path 1 — the prebuilt masked graph (what runs)

Eight parquet files. Profiled shapes from the full-scale extract / its
synthetic twin:

| Table | Rows × cols | Used at runtime? | Role |
|---|---|---|---|
| **GRAPH_NODES** | 434,046 × 11 | ✅ **primary** | node master, keyed on `MASKED_CUSTOMER_ID`; every graph entity + risk flags |
| **GRAPH_EDGES** | 802,061 × 24 | ✅ **primary** | directed edges with features pre-joined; the money-flow + identity-link graph |
| **CASE_CUSTOMERS** | 6 × 3 | ✅ | the case subjects / graph seeds (the case picker) |
| **CUSTOMERS** | 398,390 × 7 | ✅ | KYC enrichment (name, PEP, CRR) for the inspector & Stage A |
| **ALERTS** | 692 × 1 | ✅ | TM-alert membership → the red "alerted" rings |
| **COUNTRY** | 272 × 3 | ✅ | country-risk lookup + per-node home country |
| **CUSTOMERS_ACCOUNT_LINK** | ~1.05M × 4 | ⚠️ counts only | account-count in the inspector; optional (tolerated absent) |
| **TRANSACTIONS** | 802,061 × 9 | ❌ **unused** | raw edge list; its detail is already denormalized into `GRAPH_EDGES` |

Generate the full-scale synthetic twin (identical schema/distributions) with:

```bash
.venv/bin/python scripts/generate_synthetic_aml_data.py --out data/synthetic
```

### GRAPH_NODES (the node master)
`MASKED_CUSTOMER_ID` (str, `CUS_`+12 digits — **the canonical graph id**),
`ORIGINAL_CUSTOMER_ID` (str, zero-padded-9 for real customers / `PSEUDO_n`
for externals — **the crosswalk column**), `ADDRESS`, `PEP_FLAG`,
`PHONE_NUMBER`, `EMAIL_ADDRESS`, `CRR` (all null for PSEUDO), `IS_PSEUDO`
(bool), `IS_HBUS` (bool), `HAS_PREV_TM_ALERT` (0/1),
`RELATIONSHIP_START_DATE` (datetime).

### GRAPH_EDGES (the graph itself)
`SRC`, `DST` (both `CUS_` masked ids — the endpoints), `EDGE_DIRECTION`
(`ORG_to_BEN` | `BEN_to_ORG` — money-flow orientation), `CREDIT_DEBIT_CODE`,
`ORIGINATOR_KEY` / `BENEFICIARY_KEY`, `TOTAL_AMOUNT_BASE` (float),
`TXN_COUNT` (int), `FIRST_RUN_DATE` / `LAST_RUN_DATE`,
`ORIGINATOR_COUNTRY` / `BENEFICIARY_COUNTRY` + `..._COUNTRY_RISK`,
`SRC_COUNTRY` / `DST_COUNTRY` + `..._RISK`, `SAME_COUNTRY_TXN` (0/1),
`SAME_COUNTRY_FLOW` (0/1), `SHARED_ADDRESS` / `SHARED_PHONE` /
`SHARED_EMAIL` / `ANY_SHARED_CONTACT` (0/1). The shared-contact flags become
the graph's **identity edges** — entity resolution is already done here.

### The ID crosswalk (built into GRAPH_NODES)
The same customer appears three ways; `PrebuiltGraphSource.resolve()` accepts
any of them and returns the `CUS_` node id:

| Where | Raw form | Resolution |
|---|---|---|
| `CASE_CUSTOMERS.CUSTOMER_ID` | `49504810` (plain int) | `str(int).zfill(9)` → `ORIGINAL_CUSTOMER_ID` → its `MASKED_CUSTOMER_ID` |
| `CUSTOMERS`, originator keys | `000031155` (zero-pad-9) | → `MASKED_CUSTOMER_ID` |
| external beneficiaries, some `ALERTS` | `PSEUDO_101595944` | → `MASKED_CUSTOMER_ID` (external, no KYC) |
| the graph itself | `CUS_000000000001` | already canonical |

**Skipping the crosswalk returns a size-1 graph** — the seeds are plain ints,
the edges are keyed on `CUS_` ids.

### Two node types
`IS_HBUS` = real customer (has KYC). `IS_PSEUDO` = external counterparty with
**no KYC at all** (`ADDRESS`/`PHONE_NUMBER`/`EMAIL_ADDRESS`/`CRR`/`PEP_FLAG`
null). ~74k HBUS vs ~360k PSEUDO — most nodes are external and can only be
scored on graph structure. Externals render as dashed rectangles in the app.

### Placeholders are NOT signal (`src/ingest/prebuilt.py: _clean_nodes`)
Cleaned to null on load, excluded from shared-attribute matching:
`ADDRESS == 'UNKNOWN'` (~68% of rows), `PHONE_NUMBER == '0000000000'`,
`CRR in {'N','0000'}`. `CRR`: `H`→high, `L`→low, else unknown. `PEP_FLAG`:
only `'Y'` is a PEP.

### Hub-scale reality — bounded egos
Depth-3 ego-nets reach ~95% of the 434k-node graph (the seeds are super-hubs;
hop-1 alone is 15k–128k nodes). `PrebuiltGraphSource.ego_graph()` therefore
does a **bounded prioritized BFS**: top-K counterparties by flow per hop, but
**all** alerted / PEP / high-CRR neighbours are always retained, capped at
`EGO_MAX_NODES`. Any truncation is recorded on the ego and disclosed in the
UI caption and in `governance.scoring_scope`. Heavy-tailed numerics
(`TOTAL_AMOUNT_BASE` median ~6k / max ~7.5B; `TXN_COUNT` median 4 / max ~58k)
are log/robust-scaled, never used raw.

---

# Path 2 — the six raw tables (demo / from-scratch build)

The original P0/P1 pipeline, still live for the demo fixture and tests. It
builds the graph from scratch: `TRANSACTIONS`, `CUSTOMERS`,
`CUSTOMER_ACCOUNT_LINK`, `ALERTS`, `COUNTRY`, `CASE_CUSTOMERS`
(`src/ingest/loaders.py`). Here the crosswalk and entity resolution run in
code rather than being pre-baked.

### The identity problem and the crosswalk (P0)
The same customer appears three different ways:

| Where | Raw form | Canonical rule |
|---|---|---|
| CUSTOMERS, TRANSACTIONS originator | `000031155` | strip leading zeros → `"31155"` |
| CUSTOMER_ACCOUNT_LINK, CASE_CUSTOMERS | `31155` (int) | cast to string → `"31155"` |
| TRANSACTIONS beneficiary, some ALERTS | `PSEUDO_101595944` | keep verbatim; `node_type = external_pseudo` |

`src/ingest/crosswalk.py` builds a registry
`canonical_id → {raw_forms, node_type, source_tables}` from every id column of
all six tables. **Nothing joins on raw ids anywhere in the codebase.**

### Shared-attribute entity resolution (P0)
`src/ingest/entity_resolution.py` normalizes `PHONE_NUMBER` (digits only,
≥7), `EMAIL_ADDRESS` (lowercase, must contain `@`), `ADDRESS` (lowercase,
punctuation stripped, whitespace collapsed), then links any two parties
sharing a normalized value, with an over-linking guard (a corporate HQ
address must not weld hundreds of parties together):

| Parties sharing the value | Treatment |
|---|---|
| ≤ 5 (`ER_STRONG_GROUP`) | strong link, weight 1.0 |
| 6–20 (`ER_MAX_GROUP`) | down-weighted 1/log₂(n) |
| > 20 | dropped as noise |

On the prebuilt path this same resolution is already encoded in the
`SHARED_ADDRESS/PHONE/EMAIL` edge flags.

### Table quirks (raw path)
* **TRANSACTIONS** — pre-aggregated edge list (one row per
  originator→beneficiary pair). `CREDIT_DEBIT_CODE`: **DEBIT** ⇒ funds flow
  originator→beneficiary, **CREDIT** ⇒ reverse; edges oriented along money
  flow. Carries `txn_count`, `total_amount_base`, `first_run_date`,
  `last_run_date`.
* **CUSTOMERS** — sparse KYC by design (`PEP_FLAG` ~17%, `PHONE` ~16%,
  `EMAIL` ~18%, `CRR` ~19%). Presence is a strong signal; absence is
  *unknown*, and opacity itself scores as a small risk component, never
  as safety.
* **CUSTOMER_ACCOUNT_LINK** — `ACCOUNT_ID` suffix encodes the product (HDD,
  DDA, MVI, INV, HTD, TDA…); `TO_DATE = 2099-12-31` means still active. Row
  count near 2²⁰−1 (Excel's limit) → possible truncation (open question Q4).
* **ALERTS** — single column, **mixed formats**: plain ints and `PSEUDO_`.
  No alert dates → recency unknown (the UI says so explicitly).
* **COUNTRY** — `COUNTRY_RISK` bands **HIGH / MEDIUM / STANDARD** (spec said
  LOW — Q2; the scorer maps STANDARD and LOW identically).
* **CASE_CUSTOMERS** — `CASE_ID`, `CUSTOMER_ID` (plain int), `LOB`
  (CMB / WPB); these six are the seeds.

### Data-quality checklist
`src/ingest/quality.py` runs on every raw ingest: `COUNTRY_RISK` band set
(Q2), account-link row count vs the Excel limit (Q4), presence of CREDIT rows
(Q3), beneficiary-country coverage, KYC field coverage. Findings are printed,
never silently swallowed.

The demo fixture (`src/ingest/synthetic.py`) replicates every quirk above —
formats, sparsity, mixed ALERTS ids, risk bands, the six real subject ids —
and plants one detectable typology per case so every scoring stage has
something to find.

---

# Switching to real data

Drop the real files into `data/` (or `data/synthetic/`) and restart —
nothing else changes; the pipeline, scores, app, and docs all follow the data:

* **Preferred (prebuilt):** `GRAPH_NODES.parquet` + `GRAPH_EDGES.parquet`
  (+ the enrichment tables above). Their presence auto-selects the prebuilt
  path.
* **From-scratch:** the six raw tables named `TRANSACTIONS`, `CUSTOMERS`,
  `CUSTOMER_ACCOUNT_LINK`, `ALERTS`, `COUNTRY`, `CASE_CUSTOMERS` with any of
  `.parquet` / `.csv` / `.xlsx` — `src/ingest/loaders.py` picks them up and
  builds the graph.
* ID columns are read as strings so zero-padding and `PSEUDO_` forms survive;
  the crosswalk handles the rest.
