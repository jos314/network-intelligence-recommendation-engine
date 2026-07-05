"""Graph source over the prebuilt masked extract (GRAPH_NODES/GRAPH_EDGES).

Produces per-case ego MultiDiGraphs with EXACTLY the same node/edge attribute
contract as src/graph/build.py + ego.py, so stages A-E, the decision layer,
the explainers, and the app run unchanged on either source.

Scale reality (measured on the full-scale synthetic twin): the case seeds
are super-hubs — hop-1 alone is 15k-128k counterparties and depth-3 reaches
~95% of all 434k nodes. Scoring "everything at depth 3" is therefore
replaced by PRIORITIZED BOUNDED EXPANSION, the standard hub-explosion
treatment in transaction-network tools:

  * each frontier node contributes its top-K counterparties by flow amount
    (K = EGO_TOPK_SEED for the subject, EGO_TOPK_PER_NODE further out);
  * ALL priority neighbours (TM-alerted, PEP, high-CRR) are retained
    regardless of K — risk signal is never dropped for size;
  * hard ceiling EGO_MAX_NODES per network.

The truncation is recorded on ego.graph["truncation"] and disclosed in the
UI caption and the evidence pack's governance block — never silent.
"""
import numpy as np
import pandas as pd

import networkx as nx

from .. import config
from ..ingest.crosswalk import NODE_CUSTOMER, NODE_EXTERNAL

_IDENTITY_FLAGS = [("SHARED_ADDRESS", "same_address"),
                   ("SHARED_PHONE", "same_phone"),
                   ("SHARED_EMAIL", "same_email")]


def _norm(v):
    return None if v is None or (pd.api.types.is_scalar(v) and pd.isna(v)) else v


class PrebuiltGraphSource:
    """Holds the dataframes + fast lookups; builds bounded ego graphs."""

    def __init__(self, tables: dict):
        self.nodes = tables["GRAPH_NODES"]
        self.edges = tables["GRAPH_EDGES"]
        self.cases = tables["CASE_CUSTOMERS"]
        self.country = tables["COUNTRY"]

        n = self.nodes
        self._attrs = n.set_index("MASKED_CUSTOMER_ID")
        self._orig2masked = dict(zip(n["ORIGINAL_CUSTOMER_ID"],
                                     n["MASKED_CUSTOMER_ID"]))

        # ---- names (CUSTOMERS is keyed on the zero-padded original id) ----
        cust = tables["CUSTOMERS"]
        name_by_orig = dict(zip(cust["CUSTOMER_ID"].astype(str),
                                cust["CUSTOMER_NAME"].astype(str)))
        self._name = {m: name_by_orig.get(o)
                      for o, m in self._orig2masked.items() if o in name_by_orig}

        # ---- account counts (link table is keyed on the PLAIN INT id) ----
        self._n_accounts = {}
        link = tables.get("CUSTOMER_ACCOUNT_LINK")
        if link is not None:
            counts = link.groupby("CUSTOMER_ID").size()
            for orig, m in self._orig2masked.items():
                if orig.isdigit():
                    c = counts.get(int(orig))
                    if c is not None:
                        self._n_accounts[m] = int(c)

        # ---- alerted = node flag OR raw ALERTS membership (mixed formats) --
        alerted = set(n.loc[n["HAS_PREV_TM_ALERT"] == 1, "MASKED_CUSTOMER_ID"])
        for raw in tables["ALERTS"]["CUSTOMER_ID"].astype(str):
            m = self.resolve(raw)
            if m:
                alerted.add(m)
        self._alerted = alerted

        # ---- priority nodes: risk signal that must survive truncation -----
        pep = set(n.loc[n["PEP_FLAG"].astype("object") == "Y", "MASKED_CUSTOMER_ID"])
        crr_h = set(n.loc[n["CRR"].astype("object").isin(["H", "HIGH"]),
                          "MASKED_CUSTOMER_ID"])
        self._priority = alerted | pep | crr_h
        self._priority_arr = np.array(sorted(self._priority), dtype=object)

        # ---- country risk lookup + per-node home country (majority vote) --
        self._country_risk = dict(zip(self.country["COUNTRY_CODE"],
                                      self.country["COUNTRY_RISK"]))
        e = self.edges
        votes = pd.concat([
            pd.DataFrame({"node": e["SRC"], "cc": e["SRC_COUNTRY"]}),
            pd.DataFrame({"node": e["DST"], "cc": e["DST_COUNTRY"]}),
            pd.DataFrame({"node": e["SRC"], "cc": e["ORIGINATOR_COUNTRY"]}),
            pd.DataFrame({"node": e["DST"], "cc": e["BENEFICIARY_COUNTRY"]}),
        ]).dropna()
        top = (votes.groupby(["node", "cc"]).size().reset_index(name="k")
               .sort_values("k", ascending=False).drop_duplicates("node"))
        self._home = dict(zip(top["node"], top["cc"]))

        # ---- global total flow per node --------------------------------
        amt = e["TOTAL_AMOUNT_BASE"].astype(float)
        flow = (pd.concat([pd.DataFrame({"n": e["SRC"], "a": amt}),
                           pd.DataFrame({"n": e["DST"], "a": amt})])
                .groupby("n")["a"].sum())
        self._total_flow = flow.to_dict()

        # ---- adjacency sorted by aggregated pair amount (descending) ----
        pairs = (pd.concat([pd.DataFrame({"u": e["SRC"], "v": e["DST"], "a": amt}),
                            pd.DataFrame({"u": e["DST"], "v": e["SRC"], "a": amt})])
                 .groupby(["u", "v"], sort=False)["a"].sum().reset_index()
                 .sort_values(["u", "a"], ascending=[True, False]))
        self._adj = {}
        for u, grp_v, grp_a in zip(
                pairs["u"].to_numpy(),
                pairs["v"].to_numpy(),
                pairs["a"].to_numpy()):
            self._adj.setdefault(u, ([], []))
            self._adj[u][0].append(grp_v)
            self._adj[u][1].append(grp_a)

        # case subject lookup
        self._case_meta = {}
        for _, r in self.cases.iterrows():
            masked = self.resolve(r["CUSTOMER_ID"])
            self._case_meta[int(r["CASE_ID"])] = {
                "customer_id": r["CUSTOMER_ID"], "masked": masked,
                "lob": r.get("LOB"),
                "name": self._name.get(masked) or str(r["CUSTOMER_ID"]),
            }
        self._subject_ids = {m["masked"] for m in self._case_meta.values()}

    # ------------------------------------------------------------ resolve
    def resolve(self, any_id) -> str:
        """Case/customer id in ANY format -> masked graph id (or None).

        Accepts: plain int (CASE_CUSTOMERS), digit string, zero-padded-9
        original, PSEUDO_n original, or an already-masked CUS_ id."""
        if any_id is None:
            return None
        s = str(any_id).strip()
        if not s:
            return None
        if s.upper().startswith("CUS_"):
            return s if s in self._attrs.index else None
        if s.upper().startswith("PSEUDO_"):
            return self._orig2masked.get(s)
        if s.isdigit():
            return self._orig2masked.get(s.zfill(9))
        try:
            return self._orig2masked.get(str(int(float(s))).zfill(9))
        except ValueError:
            return None

    def case_meta(self) -> dict:
        return self._case_meta

    # -------------------------------------------------------- ego builder
    def _pick_neighbours(self, u: str, seen: set, k: int):
        nbrs, amts = self._adj.get(u, ([], []))
        if not nbrs:
            return [], 0
        picked, skipped = [], 0
        arr = np.array(nbrs, dtype=object)
        prio = np.isin(arr, self._priority_arr)
        for i, v in enumerate(nbrs):
            if v in seen:
                continue
            if len(picked) < k or prio[i]:
                picked.append(v)
            else:
                skipped += 1
        return picked, skipped

    def ego_graph(self, seed: str, depth: int = None) -> nx.MultiDiGraph:
        depth = depth or config.EGO_DEPTH_SCORE
        if seed not in self._attrs.index:
            raise KeyError("seed %r not in GRAPH_NODES" % seed)

        hops = {seed: 0}
        frontier = [seed]
        skipped_total = 0
        capped = False
        for level in range(1, depth + 1):
            nxt = []
            for u in frontier:
                k = config.EGO_TOPK_SEED if u == seed else config.EGO_TOPK_PER_NODE
                picked, skipped = self._pick_neighbours(u, hops, k)
                skipped_total += skipped
                for v in picked:
                    if v in hops:
                        continue
                    if len(hops) >= config.EGO_MAX_NODES and v not in self._priority:
                        capped = True
                        continue
                    hops[v] = level
                    nxt.append(v)
            frontier = nxt
            if not frontier:
                break

        keep = set(hops)
        e = self.edges
        mask = e["SRC"].isin(keep) & e["DST"].isin(keep)
        sub = e.loc[mask]

        ego = nx.MultiDiGraph()
        for m in keep:
            a = self._attrs.loc[m]
            is_pseudo = bool(a["IS_PSEUDO"])
            cc = self._home.get(m)
            ego.add_node(
                m,
                original_id=a["ORIGINAL_CUSTOMER_ID"],
                node_type=NODE_EXTERNAL if is_pseudo else NODE_CUSTOMER,
                name=self._name.get(m),
                address=_norm(a["ADDRESS"]), phone=_norm(a["PHONE_NUMBER"]),
                email=_norm(a["EMAIL_ADDRESS"]), crr=_norm(a["CRR"]),
                pep_flag=_norm(a["PEP_FLAG"]),
                alerted=m in self._alerted,
                country=cc, country_risk=self._country_risk.get(cc),
                total_flow=float(self._total_flow.get(m, 0.0)),
                n_accounts=self._n_accounts.get(m, 0),
                hop=hops[m],
                is_case_subject=m in self._subject_ids,
            )
        for cid, meta in self._case_meta.items():
            if meta["masked"] in ego:
                ego.nodes[meta["masked"]]["case_id"] = cid
                ego.nodes[meta["masked"]]["lob"] = meta["lob"]

        ident_seen = set()
        for r in sub.itertuples(index=False):
            # money-flow orientation: SRC is always the originator; the
            # EDGE_DIRECTION field says which way the funds moved
            if r.EDGE_DIRECTION == "BEN_to_ORG":
                u, v = r.DST, r.SRC
                u_cc, v_cc = r.DST_COUNTRY, r.SRC_COUNTRY
            else:
                u, v = r.SRC, r.DST
                u_cc, v_cc = r.SRC_COUNTRY, r.DST_COUNTRY
            ego.add_edge(u, v, kind="txn",
                         txn_count=int(r.TXN_COUNT),
                         total_amount_base=float(r.TOTAL_AMOUNT_BASE),
                         first_run_date=r.FIRST_RUN_DATE,
                         last_run_date=r.LAST_RUN_DATE,
                         src_country=_norm(u_cc), dst_country=_norm(v_cc))
            # precomputed shared-contact flags -> identity-link edges
            # (self-rows are skipped: an entity sharing a phone with itself
            # is not a link, and it renders as a confusing dashed loop)
            for flag, kind in _IDENTITY_FLAGS:
                if getattr(r, flag, 0) == 1 and r.SRC != r.DST:
                    key = (min(r.SRC, r.DST), max(r.SRC, r.DST), kind)
                    if key not in ident_seen:
                        ident_seen.add(key)
                        ego.add_edge(r.SRC, r.DST, kind=kind, value=None,
                                     weight=1.0, undirected=True)

        ego.graph["seed"] = seed
        ego.graph["depth"] = depth
        ego.graph["truncation"] = {
            "strategy": "top-%d flows for the subject, top-%d per further node; "
                        "all alerted/PEP/high-CRR neighbours always retained"
                        % (config.EGO_TOPK_SEED, config.EGO_TOPK_PER_NODE),
            "nodes_scored": len(keep),
            "max_nodes": config.EGO_MAX_NODES,
            "neighbours_skipped": int(skipped_total),
            "cap_hit": capped,
        }
        return ego
