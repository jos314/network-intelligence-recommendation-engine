"""Stage G — decision layer -> {No action, EDD, SAR} (§4.G).

Order of application, fully auditable:
  1. hard override rules (sanctions hit -> SAR candidate; active alert AND
     high propagated risk -> at least EDD), each recorded with its reason;
  2. threshold bands on the calibrated probability (t1, t2 in config, set by
     cost-sensitive reasoning: a missed SAR costs far more than a false EDD).

The CASE-level decision aggregates the subject's own band with the evidence
in its network (alerted/sanctioned neighbours, worst risk path), never one
node in isolation.
"""
from .. import config


def node_decision(attrs: dict) -> dict:
    """Decision + reasons for one node, from its calibrated score + flags.

    Reasons are written as case-narrative sentences (an investigator should
    be able to paste them into a case file); the driving numbers stay in
    parentheses for auditability."""
    p = attrs.get("final_risk", 0.0)
    reasons = []

    # 1 — hard overrides
    if attrs.get("base_components", {}).get("watchlist_match", 0.0) >= 1.0:
        return {"decision": config.DECISION_SAR, "p": p,
                "reasons": ["Override: watchlist/sanctions name match — SAR"]}
    floor = None
    if attrs.get("alerted") and attrs.get("prop_risk", 0.0) >= config.OVERRIDE_PROP_RISK:
        floor = config.DECISION_EDD
        reasons.append("Override: entity has an active TM alert and sits close to "
                       "other high-risk entities (proximity %.2f ≥ %.2f) — at least EDD"
                       % (attrs.get("prop_risk", 0.0), config.OVERRIDE_PROP_RISK))

    # 2 — threshold bands
    p_txt = ("%.3f" if p > 0.985 or p < 0.015 else "%.2f") % p  # no false 1.00
    if p >= config.DECISION_T2:
        band = config.DECISION_SAR
        reasons.append("Risk score %s is at or above the SAR threshold (%.2f)"
                       % (p_txt, config.DECISION_T2))
    elif p >= config.DECISION_T1:
        band = config.DECISION_EDD
        reasons.append("Risk score %s falls in the enhanced-due-diligence band "
                       "(%.2f–%.2f)" % (p_txt, config.DECISION_T1, config.DECISION_T2))
    else:
        band = config.DECISION_NO_ACTION
        reasons.append("Risk score %s is below the EDD threshold (%.2f)"
                       % (p_txt, config.DECISION_T1))

    order = [config.DECISION_NO_ACTION, config.DECISION_EDD, config.DECISION_SAR]
    if floor and order.index(band) < order.index(floor):
        band = floor
    return {"decision": band, "p": p, "reasons": reasons}


def apply_decisions(ego) -> None:
    for n, attrs in ego.nodes(data=True):
        d = node_decision(attrs)
        ego.nodes[n]["decision"] = d["decision"]
        ego.nodes[n]["decision_reasons"] = d["reasons"]


def _names(ego, nodes, limit=3) -> str:
    """Human list of entity names for a reason sentence."""
    labels = [ego.nodes[n].get("name") or n for n in nodes[:limit]]
    extra = len(nodes) - len(labels)
    return ", ".join(labels) + (" and %d more" % extra if extra > 0 else "")


def case_decision(ego) -> dict:
    """Aggregate the subject's own call with its network evidence."""
    seed = ego.graph["seed"]
    subject = ego.nodes[seed]
    d = node_decision(subject)
    band, reasons = d["decision"], list(d["reasons"])
    order = [config.DECISION_NO_ACTION, config.DECISION_EDD, config.DECISION_SAR]

    alerted_close = [n for n, a in ego.nodes(data=True)
                     if a.get("alerted") and 0 < a.get("hop", 99) <= 2]
    sanctioned = [n for n, a in ego.nodes(data=True)
                  if a.get("base_components", {}).get("watchlist_match", 0.0) >= 1.0 and n != seed]
    worst_path_risk = max((a.get("prop_risk", 0.0) for n, a in ego.nodes(data=True) if n != seed),
                          default=0.0)

    if sanctioned and order.index(band) < order.index(config.DECISION_SAR):
        band = config.DECISION_SAR
        reasons.append("Escalated to SAR: watchlisted entity in the network (%s)"
                       % _names(ego, sanctioned))
    if len(alerted_close) >= 2 and order.index(band) < order.index(config.DECISION_EDD):
        band = config.DECISION_EDD
        reasons.append("Escalated to EDD: %d alerted counterparties within 2 hops (%s)"
                       % (len(alerted_close), _names(ego, alerted_close)))
    subject_prop = subject.get("prop_risk", 0.0)
    if (alerted_close and subject_prop >= config.CASE_PROP_ESCALATION
            and order.index(band) < order.index(config.DECISION_EDD)):
        band = config.DECISION_EDD
        reasons.append("Escalated to EDD: subject is strongly connected to alerted "
                       "counterparty %s (network proximity %.2f ≥ %.2f)"
                       % (_names(ego, alerted_close), subject_prop, config.CASE_PROP_ESCALATION))

    return {
        "decision": band,
        "calibrated_score": subject.get("final_risk", 0.0),
        "reasons": reasons,
        "alerted_neighbors": alerted_close,
        "sanctioned_neighbors": sanctioned,
        "worst_path_risk": worst_path_risk,
    }
