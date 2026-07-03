"""P1 — unified heterogeneous graph (§3 of the build plan).

One nx.MultiDiGraph over canonical ids, two edge families:
  * kind="txn": directed along MONEY FLOW. DEBIT => originator -> beneficiary;
    CREDIT => beneficiary -> originator.
  * kind="same_phone"/"same_email"/"same_address": identity links from §2.2,
    stored once per pair with undirected=True (traversal treats them both ways).

Nodes carry KYC (sparse — missingness is a signal, not zero-risk), country,
alert / case-subject flags, and later the scoring columns.
"""
import networkx as nx
import pandas as pd

from ..ingest.crosswalk import Crosswalk, build_crosswalk, canonical_id, node_type
from ..ingest.entity_resolution import shared_attribute_links

TXN = "txn"


def build_unified_graph(tables: dict) -> "tuple[nx.MultiDiGraph, Crosswalk]":
    xw = build_crosswalk(tables)
    g = nx.MultiDiGraph()

    country_risk = tables["COUNTRY"].set_index("COUNTRY_CODE")["COUNTRY_RISK"].to_dict()

    # ---- nodes ------------------------------------------------------------
    for canon in xw.ids():
        g.add_node(canon, node_type=node_type(canon))

    customers = tables["CUSTOMERS"].copy()
    customers["_canon"] = customers["CUSTOMER_ID"].map(canonical_id)
    for _, r in customers.iterrows():
        if r["_canon"] is None:
            continue
        g.nodes[r["_canon"]].update({
            "name": r.get("CUSTOMER_NAME"),
            "address": r.get("ADDRESS"),
            "phone": r.get("PHONE_NUMBER"),
            "email": r.get("EMAIL_ADDRESS"),
            "pep_flag": r.get("PEP_FLAG"),
            "crr": r.get("CRR"),
        })

    link = tables["CUSTOMER_ACCOUNT_LINK"].copy()
    link["_canon"] = link["CUSTOMER_ID"].map(canonical_id)
    for canon, grp in link.groupby("_canon"):
        if canon in g:
            accounts = grp["ACCOUNT_ID"].astype(str).tolist()
            g.nodes[canon]["accounts"] = accounts
            g.nodes[canon]["account_types"] = sorted({a[-3:] for a in accounts if len(a) >= 3})

    alerted = {canonical_id(v) for v in tables["ALERTS"]["CUSTOMER_ID"]} - {None}
    for canon in alerted:
        if canon in g:
            g.nodes[canon]["alerted"] = True

    for _, r in tables["CASE_CUSTOMERS"].iterrows():
        canon = canonical_id(r["CUSTOMER_ID"])
        if canon in g:
            g.nodes[canon]["is_case_subject"] = True
            g.nodes[canon]["case_id"] = int(r["CASE_ID"])
            g.nodes[canon]["lob"] = r.get("LOB")

    # ---- transaction edges (oriented along money flow) ---------------------
    txn = tables["TRANSACTIONS"].copy()
    txn["_orig"] = txn["ORIGINATOR_KEY"].map(canonical_id)
    txn["_benef"] = txn["BENEFICIARY_KEY"].map(canonical_id)
    node_country = {}
    for _, r in txn.iterrows():
        o, b = r["_orig"], r["_benef"]
        if o is None or b is None:
            continue
        code = str(r["CREDIT_DEBIT_CODE"]).strip().upper()
        src, dst = (b, o) if code == "CREDIT" else (o, b)
        g.add_edge(src, dst, kind=TXN,
                   txn_count=int(r["txn_count"]),
                   total_amount_base=float(r["total_amount_base"]),
                   first_run_date=r["first_run_date"],
                   last_run_date=r["last_run_date"],
                   src_country=r.get("ORIGINATOR_COUNTRY") if src == o else r.get("BENEFICIARY_COUNTRY"),
                   dst_country=r.get("BENEFICIARY_COUNTRY") if dst == b else r.get("ORIGINATOR_COUNTRY"))
        # majority-vote node country from txn rows (CUSTOMERS has no country col)
        for canon, c in ((o, r.get("ORIGINATOR_COUNTRY")), (b, r.get("BENEFICIARY_COUNTRY"))):
            if pd.notna(c) and c:
                node_country.setdefault(canon, {})
                node_country[canon][c] = node_country[canon].get(c, 0) + 1

    for canon, counts in node_country.items():
        if canon in g:
            best = max(counts, key=counts.get)
            g.nodes[canon]["country"] = best
            g.nodes[canon]["country_risk"] = country_risk.get(best)

    # ---- total flow per node, then per-edge volume share -------------------
    total_flow = {}
    for u, v, d in g.edges(data=True):
        if d["kind"] != TXN:
            continue
        amt = d["total_amount_base"]
        total_flow[u] = total_flow.get(u, 0.0) + amt
        total_flow[v] = total_flow.get(v, 0.0) + amt
    for n, t in total_flow.items():
        g.nodes[n]["total_flow"] = t
    for u, v, k, d in g.edges(keys=True, data=True):
        if d["kind"] == TXN:
            base = max(total_flow.get(u, 0.0), 1e-9)
            d["volume_share_pct"] = 100.0 * d["total_amount_base"] / base

    # ---- shared-attribute identity edges -----------------------------------
    er = shared_attribute_links(tables["CUSTOMERS"])
    for _, r in er.iterrows():
        if r["src"] in g and r["dst"] in g:
            g.add_edge(r["src"], r["dst"], kind=r["kind"], value=r["value"],
                       weight=float(r["weight"]), undirected=True)
    return g, xw
