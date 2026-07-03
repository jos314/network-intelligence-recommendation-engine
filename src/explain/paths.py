"""§5.1 graph side — the paths that carried the most propagated risk.

Stage C's max-path tracer already stores, per node, the highest-scoring path
from a bad seed (risk_path). Here we collect the strongest ones touching the
subject's neighbourhood so the UI can highlight them and the evidence pack
can cite them: e.g. subject -> mule A -> sanctioned entity B.
"""


def key_paths(ego, top_k: int = 3) -> list:
    seed = ego.graph["seed"]
    candidates = []
    seen = set()
    # the subject's own inbound risk path first
    for n, attrs in sorted(ego.nodes(data=True),
                           key=lambda t: t[1].get("prop_risk", 0.0), reverse=True):
        path = attrs.get("risk_path")
        if not path or len(path) < 2:
            continue
        if n != seed and seed not in path:
            continue  # only paths that involve the subject's case
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "path": path,
            "prop_risk": round(attrs.get("prop_risk", 0.0), 4),
            "endpoint": n,
        })
        if len(candidates) >= top_k:
            break
    return candidates
