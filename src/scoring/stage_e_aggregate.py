"""Stage E — aggregation into one entity risk (§4.E).

raw_risk = wA*base + wB*rel + wC*prop + wD*struct, weights in config.
The four parts stay stored separately so the UI and the driver attribution
can decompose the score. THIS IS THE SEAM FOR STAGE H: replace this weighted
sum with a learned model over the same inputs/outputs and nothing else
changes (see stage_h_learned.py).
"""
from .. import config


def score_aggregate(ego) -> None:
    for n, attrs in ego.nodes(data=True):
        parts = {
            "base": attrs.get("base_risk", 0.0),
            "rel": attrs.get("rel_risk", 0.0),
            "prop": attrs.get("prop_risk", 0.0),
            "struct": attrs.get("struct_risk", 0.0),
        }
        raw = sum(config.STAGE_E_WEIGHTS[k] * v for k, v in parts.items())
        ego.nodes[n]["risk_parts"] = parts
        ego.nodes[n]["raw_risk"] = min(max(raw, 0.0), 1.0)
