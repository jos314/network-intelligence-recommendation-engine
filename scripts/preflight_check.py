"""Preflight check for a real/prebuilt parquet drop in ./data.

Run this BEFORE launching the app to confirm the data is wired correctly:
    python -m scripts.preflight_check

It verifies (against whatever DataAccess would actually load):
  1. the prebuilt dir is found and holds the 8 expected files
  2. every table carries the columns the app reads
  3. all case customers resolve plain-int -> masked node (the crosswalk)
  4. one real ego-network builds with >1 node

Exit code 0 = good to launch; 1 = a problem is printed with the fix.
"""
import sys

from src.ingest.prebuilt import (find_prebuilt_dir, load_prebuilt_tables,
                                  PREBUILT_TABLES, _ACCOUNT_LINK_NAMES)
from src.graph.prebuilt_source import PrebuiltGraphSource

# columns the app actually accesses (KeyError if missing) vs nice-to-have
REQUIRED = {
    "GRAPH_NODES": ["MASKED_CUSTOMER_ID", "ORIGINAL_CUSTOMER_ID", "IS_PSEUDO",
                    "ADDRESS", "PHONE_NUMBER", "EMAIL_ADDRESS", "CRR",
                    "PEP_FLAG", "HAS_PREV_TM_ALERT"],
    "GRAPH_EDGES": ["SRC", "DST", "EDGE_DIRECTION", "SRC_COUNTRY", "DST_COUNTRY",
                    "ORIGINATOR_COUNTRY", "BENEFICIARY_COUNTRY", "TXN_COUNT",
                    "TOTAL_AMOUNT_BASE", "FIRST_RUN_DATE", "LAST_RUN_DATE"],
    "CASE_CUSTOMERS": ["CASE_ID", "CUSTOMER_ID", "LOB"],
    "CUSTOMERS": ["CUSTOMER_ID", "CUSTOMER_NAME"],
    "COUNTRY": ["COUNTRY_CODE", "COUNTRY_RISK"],
    "ALERTS": ["CUSTOMER_ID"],
}
OPTIONAL = {  # missing = a feature degrades, not a crash
    "GRAPH_EDGES": ["SHARED_ADDRESS", "SHARED_PHONE", "SHARED_EMAIL"],
    "CUSTOMERS_ACCOUNT_LINK": ["CUSTOMER_ID"],  # only account counts
}


def main():
    problems = []

    base = find_prebuilt_dir()
    if base is None:
        print("FAIL: no GRAPH_NODES.parquet + GRAPH_EDGES.parquet found.")
        print("      Put the 8 parquet files in ./data (repo root).")
        return 1
    print("data dir: %s" % base)

    # 1. files present
    wanted = list(PREBUILT_TABLES) + [_ACCOUNT_LINK_NAMES[0]]
    for name in wanted:
        exists = (base / ("%s.parquet" % name)).exists()
        alt = name in _ACCOUNT_LINK_NAMES[:1] and any(
            (base / ("%s.parquet" % a)).exists() for a in _ACCOUNT_LINK_NAMES)
        mark = "ok" if (exists or alt) else "MISSING"
        if not (exists or alt) and name not in _ACCOUNT_LINK_NAMES:
            problems.append("missing file: %s.parquet" % name)
        print("  [%s] %s.parquet" % (mark, name))

    tables = load_prebuilt_tables(base)

    # 2. columns
    for tname, cols in REQUIRED.items():
        have = set(tables[tname].columns)
        miss = [c for c in cols if c not in have]
        if miss:
            problems.append("%s missing required columns: %s" % (tname, miss))
            print("  [COLS] %s missing %s" % (tname, miss))
    for tname, cols in OPTIONAL.items():
        t = tables.get(tname)
        if t is None:
            continue
        miss = [c for c in cols if c not in t.columns]
        if miss:
            print("  [warn] %s missing optional %s (feature degrades)"
                  % (tname, miss))

    # 3 + 4. crosswalk + one ego
    gs = PrebuiltGraphSource(tables)
    unresolved = []
    for _, r in gs.cases.iterrows():
        m = gs.resolve(r["CUSTOMER_ID"])
        tag = m or "*** None ***"
        print("  case %s  cust %s -> %s" % (r["CASE_ID"], r["CUSTOMER_ID"], tag))
        if not m:
            unresolved.append(r["CUSTOMER_ID"])
    if unresolved:
        problems.append("cases did not resolve: %s" % unresolved)
        sample = list(tables["GRAPH_NODES"]["ORIGINAL_CUSTOMER_ID"].head(3))
        print("      ORIGINAL_CUSTOMER_ID sample: %s" % sample)
        print("      -> the crosswalk in prebuilt_source.resolve() needs the "
              "padding these use")
    else:
        first = gs.cases["CUSTOMER_ID"].iloc[0]
        ego = gs.ego_graph(gs.resolve(first))
        print("  ego of case-1 subject: %d nodes / %d edges"
              % (ego.number_of_nodes(), ego.number_of_edges()))
        if ego.number_of_nodes() <= 1:
            problems.append("case-1 ego is size-1 (no edges matched the seed)")

    print("-" * 56)
    if problems:
        print("PREFLIGHT FAILED:")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("PREFLIGHT OK — launch with:  python -m src.app.app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
