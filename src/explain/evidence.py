"""§5.2 — the evidence pack: one machine-written object per case, the single
structured hand-off consumed by the conclusion prompt, the UI, and any audit.
"""
from datetime import datetime, timezone

from .. import config
from ..decision.rules import case_decision
from ..graph.ego import node_flow_summary
from .drivers import node_drivers
from .paths import key_paths


def _label(ego, n) -> str:
    name = ego.nodes[n].get("name")
    return "%s (%s)" % (name, n) if name else n


def _iso(ts):
    return None if ts is None else str(ts)[:10]


def build_evidence_pack(ego, case_id, calibration=None) -> dict:
    seed = ego.graph["seed"]
    subject = ego.nodes[seed]
    case = case_decision(ego)

    shared_links = []
    for u, v, d in ego.edges(data=True):
        if d.get("kind", "").startswith("same_") and seed in (u, v):
            other = v if u == seed else u
            shared_links.append({"counterparty": _label(ego, other),
                                 "kind": d["kind"], "value": d.get("value")})

    flags = []
    if subject.get("in_cycle"):
        flags.append("cycle")
    if subject.get("struct_components", {}).get("community", 0.0) > 0.2:
        flags.append("dense_group")
    if subject.get("struct_components", {}).get("centrality", 0.0) > 0.5:
        flags.append("bridge")

    # case-level activity window + subject flow (investigators think in
    # amounts and dates; the pack must carry both)
    first, last = None, None
    for _, _, d in ego.edges(data=True):
        if d.get("kind") != "txn":
            continue
        f, l = d.get("first_run_date"), d.get("last_run_date")
        if f is not None and (first is None or f < first):
            first = f
        if l is not None and (last is None or l > last):
            last = l

    ranked = sorted((n for n in ego.nodes if n != seed),
                    key=lambda n: ego.nodes[n].get("final_risk", 0.0), reverse=True)
    top_counterparties = []
    for n in ranked[:10]:
        a = ego.nodes[n]
        flow = node_flow_summary(ego, n)
        top_counterparties.append({
            "id": n, "name": a.get("name"), "hop": a.get("hop"),
            "final_risk": round(a.get("final_risk", 0.0), 4),
            "decision": a.get("decision"), "alerted": bool(a.get("alerted")),
            "node_type": a.get("node_type"),
            "total_amount_base": round(flow["total_amount"], 2),
            "txn_count": flow["txn_count"],
            "first_seen": _iso(flow["first_seen"]), "last_seen": _iso(flow["last_seen"]),
        })

    return {
        "case_id": case_id,
        "subject_id": seed,
        "subject_name": subject.get("name"),
        "lob": subject.get("lob"),
        "decision": case["decision"],
        "calibrated_score": round(case["calibrated_score"], 4),
        "decision_reasons": case["reasons"],
        "subject_total_flow": round(subject.get("total_flow", 0.0), 2),
        "activity_window": {"first": _iso(first), "last": _iso(last)},
        "top_drivers": node_drivers(subject),
        "top_counterparties": top_counterparties,
        "key_paths": [
            {"path": [_label(ego, n) for n in p["path"]], "prop_risk": p["prop_risk"]}
            for p in key_paths(ego)
        ],
        "alerted_neighbors": [_label(ego, n) for n in case["alerted_neighbors"]],
        "sanctioned_neighbors": [_label(ego, n) for n in case["sanctioned_neighbors"]],
        "shared_attribute_links": shared_links,
        "structural_flags": flags,
        "network_size": {"nodes": ego.number_of_nodes(), "edges": ego.number_of_edges(),
                         "depth": ego.graph.get("depth")},
        "risk_parts": {k: round(v, 4) for k, v in subject.get("risk_parts", {}).items()},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "governance": {
            "propagation_method": ego.graph.get("prop_method"),
            "thresholds": {"t1": config.DECISION_T1, "t2": config.DECISION_T2},
            "calibration": calibration or {"calibrated": None, "method": "unknown"},
            "sources": {"watchlist_connected": config.WATCHLIST_CONNECTED},
            # bounded-expansion disclosure on hub-scale graphs (None = full)
            "scoring_scope": ego.graph.get("truncation"),
        },
    }
