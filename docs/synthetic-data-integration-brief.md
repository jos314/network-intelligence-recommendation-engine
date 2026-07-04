# Task: wire the synthetic AML database into the app

I'm attaching `generate_synthetic_aml_data.py`. It generates a synthetic dataset that
**mimics our real (masked) HBUS AML data at full scale** — same schemas, same
distributions, same ID scheme. Use it as the dev/demo data source for the app.

**App goal (context):** an investigator enters a **Case ID or Customer ID**, and the
app builds the counterparty **network around that customer to depth 3**, scores every
connected entity for risk, assigns each a role (perpetrator / mule / victim / …), and
produces a SAR vs EDD recommendation. This task is only about **loading and consuming
the data correctly** — not the scoring model itself.

---

## 1. Generate the data
```bash
pip install pandas numpy pyarrow faker      # faker optional (nicer names)
python generate_synthetic_aml_data.py --out ./synthetic_data          # ~18s, full scale
python generate_synthetic_aml_data.py --out ./synthetic_data --scale 0.05   # fast smoke test
```
Writes **8 parquet files** to `./synthetic_data/`. Load with `pd.read_parquet(...)`.

---

## 2. The 8 tables (schema — dtypes and gotchas matter)

**Use `GRAPH_NODES` + `GRAPH_EDGES` as the primary graph** — they're pre-built (ID
crosswalk done, features pre-computed). The other tables are the raw sources for
reference/enrichment.

**GRAPH_NODES** (434,046 × 11) — the node master:
`MASKED_CUSTOMER_ID` (str, `CUS_`+12 digits — **the canonical graph id**), `ORIGINAL_CUSTOMER_ID`
(str, zero-padded-9 for real customers / `PSEUDO_n` for externals — **the crosswalk**),
`ADDRESS`, `PEP_FLAG`, `PHONE_NUMBER`, `EMAIL_ADDRESS`, `CRR` (all null for PSEUDO),
`IS_PSEUDO` (bool), `IS_HBUS` (bool), `HAS_PREV_TM_ALERT` (0/1), `RELATIONSHIP_START_DATE` (datetime).

**GRAPH_EDGES** (802,061 × 24) — directed edges with features already joined:
`SRC`, `DST` (both `CUS_` masked ids — the endpoints), `EDGE_DIRECTION` (`ORG_to_BEN` | `BEN_to_ORG`),
`CREDIT_DEBIT_CODE`, `ORIGINATOR_KEY`/`BENEFICIARY_KEY` (masked), `TOTAL_AMOUNT_BASE` (float),
`TXN_COUNT` (int), `FIRST_RUN_DATE`/`LAST_RUN_DATE`, `ORIGINATOR_COUNTRY`/`BENEFICIARY_COUNTRY` +
`..._COUNTRY_RISK`, `SRC_COUNTRY`/`DST_COUNTRY` + `..._RISK`, `SAME_COUNTRY_TXN` (0/1),
`SAME_COUNTRY_FLOW` (0/1), `SHARED_ADDRESS` (0/1), `SHARED_PHONE` (0/1), `SHARED_EMAIL` (0/1),
`ANY_SHARED_CONTACT` (0/1).

**CASE_CUSTOMERS** (6 × 3) — the seeds: `CASE_ID` (int), `CUSTOMER_ID` (**int64, plain** e.g. `49504810`), `LOB` (`CMB`|`WPB`).
**CUSTOMERS** (398,390 × 7) — `CUSTOMER_ID` (str, zero-pad-9), `CUSTOMER_NAME`, `ADDRESS`, `PEP_FLAG`, `PHONE_NUMBER`, `EMAIL_ADDRESS`, `CRR`.
**CUSTOMERS_ACCOUNT_LINK** (~1.05M × 4) — `ACCOUNT_ID` (str, 12 digits + 3-letter type suffix), `CUSTOMER_ID` (**int64, plain**), `FROM_DATE`, `TO_DATE` (`2099-12-31` = still open).
**TRANSACTIONS** (802,061 × 9) — raw edge list: `CREDIT_DEBIT_CODE`, `ORIGINATOR_KEY` (zero-pad-9), `BENEFICIARY_KEY` (`PSEUDO_n` or zero-pad-9), countries, `txn_count`, `total_amount_base`, `first_run_date`, `last_run_date`.
**COUNTRY** (272 × 3) — `COUNTRY_CODE` (1 null), `COUNTRY_NAME`, `COUNTRY_RISK` (`HIGH`|`MEDIUM`|`STANDARD`).
**ALERTS** (692 × 1) — `CUSTOMER_ID` (str, plain int or `PSEUDO_n`); presence = a TM alert fired.

---

## 3. Non-negotiable integration rules

1. **THE ID CROSSWALK — do this first.** The graph is keyed on `MASKED_CUSTOMER_ID`
   (`CUS_…`), but `CASE_CUSTOMERS.CUSTOMER_ID` is a **plain int**. To find a seed's node:
   `plain int → str(int).zfill(9)` = `ORIGINAL_CUSTOMER_ID` → look up its `MASKED_CUSTOMER_ID`
   in `GRAPH_NODES` → that string is the `SRC`/`DST` node. **If you skip this, ego-network
   lookups return nothing (size-1 graph).**
2. **Ego-network = BFS to depth 3** over `GRAPH_EDGES` (`SRC`↔`DST`). Treat it as
   **undirected for reachability**, but keep `EDGE_DIRECTION` for money-flow/roles. Never load
   the whole graph into the UI — only render one seed's depth-≤3 neighbourhood (it's small; the
   full graph is 800k edges).
3. **Two node types.** `IS_HBUS` = real customer (has KYC). `IS_PSEUDO` = external counterparty
   with **no KYC at all** (`ADDRESS`/`PHONE_NUMBER`/`EMAIL_ADDRESS`/`CRR`/`PEP_FLAG` are null).
   ~74k HBUS vs ~360k PSEUDO — most nodes are external and can only be scored on graph structure.
4. **Placeholders are NOT signal — treat as missing:** `ADDRESS == 'UNKNOWN'` (~68% of rows),
   `PHONE_NUMBER == '0000000000'`, `CRR in {'N','0000'}`. **Exclude these from shared-attribute
   matching.** Map `CRR`: `H`→high, `L`→low, everything else→unknown. `PEP_FLAG`: only `'Y'` is a PEP.
5. **Heavy-tailed numerics** — `TOTAL_AMOUNT_BASE` (median ~6k, max ~7.5B), `TXN_COUNT`
   (median 4, max ~58k), accounts/customer (median ~7, max ~10k). **Use log / robust scaling**, never raw magnitudes.
6. **Most customers aren't in the graph.** `CUSTOMERS` has 398k rows but only ~74k are "active"
   (in the graph, with KYC + accounts); the rest are name+address-only shells. Join to the graph via the crosswalk, not by assuming every customer is a node.

---

## 4. Which fields feed the risk signals (so you know what to surface)

- **Entity's own risk:** `HAS_PREV_TM_ALERT`, `PEP_FLAG=='Y'`, `CRR=='H'`, node's `..._COUNTRY_RISK=='HIGH'`. (An OFAC sanctions-list match will be added later — leave a hook.)
- **Relationship risk (edge):** `TOTAL_AMOUNT_BASE` (→ share of the subject's total volume), `SHARED_ADDRESS/PHONE/EMAIL/ANY_SHARED_CONTACT`, `SAME_COUNTRY_TXN/FLOW`, `EDGE_DIRECTION`.
- **Roles:** net sender vs receiver from `EDGE_DIRECTION` + `TOTAL_AMOUNT_BASE`, plus degree/betweenness in the ego-graph.
- **Alerts / prior SAR-EDD status:** `ALERTS` membership and `HAS_PREV_TM_ALERT`.

---

## 5. Starter loader (copy/adapt)

```python
import pandas as pd
from collections import defaultdict

DATA = "./synthetic_data"
nodes  = pd.read_parquet(f"{DATA}/GRAPH_NODES.parquet")
edges  = pd.read_parquet(f"{DATA}/GRAPH_EDGES.parquet")
cases  = pd.read_parquet(f"{DATA}/CASE_CUSTOMERS.parquet")

# --- crosswalk: plain int / original id -> masked graph id ---
orig2masked = dict(zip(nodes.ORIGINAL_CUSTOMER_ID, nodes.MASKED_CUSTOMER_ID))
def resolve(customer_id) -> str | None:
    """Accept a plain int (CASE_CUSTOMERS) or an original id, return the CUS_ node id."""
    key = str(int(customer_id)).zfill(9) if str(customer_id).isdigit() else str(customer_id)
    return orig2masked.get(key)

# --- adjacency for depth-K ego-networks (undirected reach) ---
adj = defaultdict(set)
for s, d in zip(edges.SRC.to_numpy(), edges.DST.to_numpy()):
    adj[s].add(d); adj[d].add(s)

def ego_nodes(masked_id, depth=3):
    seen, frontier = {masked_id}, {masked_id}
    for _ in range(depth):
        nxt = set().union(*(adj[u] for u in frontier)) - seen
        seen |= nxt; frontier = nxt
    return seen

# --- example: build one case's subgraph ---
seed = resolve(cases.CUSTOMER_ID.iloc[0])          # e.g. 49504810 -> 'CUS_000000000001'
keep = ego_nodes(seed, depth=3)
sub_nodes = nodes[nodes.MASKED_CUSTOMER_ID.isin(keep)]
sub_edges = edges[edges.SRC.isin(keep) & edges.DST.isin(keep)]
print(seed, "->", len(sub_nodes), "nodes,", len(sub_edges), "edges")
```

Depth-3 ego-nets here are large (the seeds reach most of the graph), so add filters
(min-risk, hop limit, edge type) and expand-on-click rather than rendering all at once.

---

## 6. Deliverable
A data-access layer that, given a Case ID or Customer ID, returns the resolved seed node,
its depth-3 ego subgraph (nodes + edges with the columns above), and the joined attributes,
ready for the scoring pipeline and the graph view. Handle nulls/placeholders per §3–4.
