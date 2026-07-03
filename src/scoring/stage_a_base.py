"""Stage A — node base risk: transparent scorecard over the entity's own
characteristics (§4.A). Each component is 0-1; base_risk = sum(w_i * c_i)
with weights from config (sum to 1, so base_risk is bounded in [0, 1]).

External PSEUDO_ nodes have no KYC row; their KYC-missingness component fires
(opacity != safety) and everything else about them must come from the graph.
"""
import pandas as pd

from .. import config

_KYC_FIELDS = ("pep_flag", "crr", "phone", "email")


def _present(v) -> bool:
    return v is not None and not (pd.api.types.is_scalar(v) and pd.isna(v))


def base_components(attrs: dict) -> dict:
    crr = attrs.get("crr")
    crr_c = config.CRR_MAP.get(str(crr).strip().upper(), 0.0) if _present(crr) else 0.0
    cr = attrs.get("country_risk")
    cr_c = config.COUNTRY_RISK_MAP.get(str(cr).strip().upper(), 0.0) if _present(cr) else 0.0
    missing = sum(0 if _present(attrs.get(f)) else 1 for f in _KYC_FIELDS) / len(_KYC_FIELDS)
    wl = attrs.get("watchlist_match")
    return {
        "alerted": 1.0 if attrs.get("alerted") else 0.0,
        "watchlist_match": float(wl) if _present(wl) else 0.0,  # fuzzy score if a list exists (Q5)
        "pep": 1.0 if _present(attrs.get("pep_flag")) and str(attrs["pep_flag"]).strip().upper() == "Y" else 0.0,
        "crr": crr_c,
        "country_risk": cr_c,
        "kyc_missing": missing,
    }


def score_base(ego) -> None:
    """Annotate every ego node with base_risk + its components."""
    for n, attrs in ego.nodes(data=True):
        comps = base_components(attrs)
        ego.nodes[n]["base_components"] = comps
        ego.nodes[n]["base_risk"] = sum(
            config.STAGE_A_WEIGHTS[k] * v for k, v in comps.items()
        )
