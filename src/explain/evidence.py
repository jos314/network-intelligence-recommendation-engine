"""§5.2 — the evidence pack: one machine-written object per case, the single
structured hand-off consumed by the conclusion prompt, the UI, and any audit.
"""
from .. import config
from ..decision.rules import case_decision
from .drivers import node_drivers
from .paths import key_paths


def _label(ego, n) -> str:
    name = ego.nodes[n].get("name")
    return "%s (%s)" % (name, n) if name else n


def build_evidence_pack(ego, case_id) -> dict:
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

    return {
        "case_id": case_id,
        "subject_id": seed,
        "subject_name": subject.get("name"),
        "lob": subject.get("lob"),
        "decision": case["decision"],
        "calibrated_score": round(case["calibrated_score"], 4),
        "decision_reasons": case["reasons"],
        "top_drivers": node_drivers(subject),
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
        "governance": {
            "propagation_method": ego.graph.get("prop_method"),
            "thresholds": {"t1": config.DECISION_T1, "t2": config.DECISION_T2},
        },
    }
