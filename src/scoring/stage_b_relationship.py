"""Stage B — relationship risk of each DIRECT (level-1) counterparty to the
subject (§4.B). Shared-identity attributes dominate: a shared address plus a
high volume share is the classic nominee/shell signature.

capital_ratio_shared uses the proposed proxy from open question Q8:
concentration of the subject's total flow on this counterparty.
"""
import pandas as pd

from .. import config
from ..graph.build import TXN


def _edges_between(ego, a, b):
    for src, dst in ((a, b), (b, a)):
        data = ego.get_edge_data(src, dst) or {}
        for d in data.values():
            yield src, dst, d


def score_relationship(ego) -> None:
    seed = ego.graph["seed"]
    seed_country = ego.nodes[seed].get("country")
    seed_flow = max(ego.nodes[seed].get("total_flow", 0.0) or 0.0, 1e-9)

    for n in ego.nodes:
        ego.nodes[n]["rel_components"] = {}
        ego.nodes[n]["rel_risk"] = 0.0
    for u in ego.nodes:
        if u == seed or ego.nodes[u].get("hop") != 1:
            continue
        pair_amount = 0.0
        shared_kinds = set()
        country_change = 0.0
        for _, _, d in _edges_between(ego, seed, u):
            if d.get("kind") == TXN:
                pair_amount += d["total_amount_base"]
                cpty_country = d.get("dst_country") if d.get("src_country") == seed_country else d.get("src_country")
                for c in (d.get("src_country"), d.get("dst_country")):
                    if pd.notna(c) and c and seed_country and c != seed_country:
                        country_change = 1.0
            else:
                shared_kinds.add(d.get("kind"))
        volume_share = min(pair_amount / seed_flow, 1.0)
        comps = {
            "shared_attribute": 1.0 if shared_kinds else 0.0,
            "volume_share": volume_share,
            "country_change": country_change,
            "capital_ratio_shared": volume_share,  # proxy, see Q8
        }
        ego.nodes[u]["rel_components"] = comps
        ego.nodes[u]["rel_shared_kinds"] = sorted(shared_kinds)
        ego.nodes[u]["rel_risk"] = sum(config.STAGE_B_WEIGHTS[k] * v for k, v in comps.items())
