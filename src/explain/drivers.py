"""§5.1 tabular side — ranked feature contributions for a node's score.

The MVP scorer is additive (weighted sums of weighted sums), so each
feature's exact Shapley value IS its weight x component value — no sampling
approximation needed; these are the same numbers SHAP's LinearExplainer
would return. When Stage H replaces the aggregator with a learned model,
swap this for shap.Explainer over the same feature frame.
"""
from .. import config

_STAGE_LABEL = {
    "alerted": "TM alert (last 3 months)",
    "watchlist_match": "Watchlist / sanctions match",
    "pep": "PEP flag",
    "crr": "High KYC risk rating (CRR)",
    "country_risk": "Country risk",
    "kyc_missing": "KYC opacity (missing fields)",
    "shared_attribute": "Shared phone/email/address with subject",
    "volume_share": "Share of subject's total volume",
    "country_change": "Cross-border flow vs subject",
    "capital_ratio_shared": "Capital concentration on this counterparty",
    "prop": "Proximity to high-risk entities (propagated)",
    "cycle": "Participates in a circular flow",
    "community": "Member of a high-risk dense group",
    "centrality": "Hub / broker position in the network",
}


def node_drivers(attrs: dict, top_k: int = 8) -> list:
    """Exact additive contributions to raw_risk, ranked by magnitude."""
    contribs = []
    wE = config.STAGE_E_WEIGHTS
    for feat, comp in (attrs.get("base_components") or {}).items():
        contribs.append((feat, wE["base"] * config.STAGE_A_WEIGHTS[feat] * comp))
    for feat, comp in (attrs.get("rel_components") or {}).items():
        contribs.append((feat, wE["rel"] * config.STAGE_B_WEIGHTS[feat] * comp))
    contribs.append(("prop", wE["prop"] * attrs.get("prop_risk", 0.0)))
    for feat, comp in (attrs.get("struct_components") or {}).items():
        contribs.append((feat, wE["struct"] * config.STAGE_D_WEIGHTS[feat] * comp))

    ranked = sorted(contribs, key=lambda t: abs(t[1]), reverse=True)[:top_k]
    return [
        {"feature": _STAGE_LABEL.get(f, f), "direction": "+" if m >= 0 else "-",
         "magnitude": round(float(m), 4)}
        for f, m in ranked if abs(m) > 1e-6
    ]
