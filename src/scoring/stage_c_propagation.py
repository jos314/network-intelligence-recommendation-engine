"""Stage C — risk propagation from bad seeds across the ego-network (§4.C).

Two interchangeable formalisms, both implemented:
  * "ppr"  — personalized PageRank / TrustRank (recommended, smoother):
             restart distribution concentrated on bad seeds weighted by
             base_risk, over a risk-weighted, time-decayed adjacency.
  * "khop" — bounded K-hop diffusion (simpler, fully traceable):
             prop(v) = max over paths from bad seeds of
             base(seed) * gamma^len * prod(edge weights).

The khop max-path tracer ALWAYS runs (even under ppr) because §5.1 needs the
path that carried the most risk for the rationale; under "khop" its scores
are also the prop_risk itself. Association spreads both ways, so edges are
traversed undirected; direction stays available to Stage D's flow typologies.
"""
import math

import networkx as nx
import pandas as pd

from .. import config
from ..graph.build import TXN


def _edge_weight(d, now) -> float:
    """0-1 weight combining edge kind/size with time decay."""
    if d.get("kind") == TXN:
        # log-scaled amount, saturating at ~1 for >= $1M edges
        amt = max(d.get("total_amount_base", 0.0), 1.0)
        w = min(math.log10(amt) / 6.0, 1.0)
        last = d.get("last_run_date")
        if pd.notna(last) and now is not None:
            age_days = max((now - pd.Timestamp(last)).days, 0)
            w *= math.exp(-age_days / config.TIME_DECAY_TAU_DAYS)
        return max(w, 0.01)
    # identity links: high-precision when present; frequency-based weight
    return max(float(d.get("weight", 1.0)), 0.01)


def _undirected_weighted(ego) -> nx.Graph:
    now = None
    dates = [d.get("last_run_date") for _, _, d in ego.edges(data=True)
             if pd.notna(d.get("last_run_date"))]
    if dates:
        now = max(pd.Timestamp(d) for d in dates)
    u = nx.Graph()
    u.add_nodes_from(ego.nodes)
    for a, b, d in ego.edges(data=True):
        w = _edge_weight(d, now)
        if u.has_edge(a, b):
            u[a][b]["weight"] = max(u[a][b]["weight"], w)
        else:
            u.add_edge(a, b, weight=w)
    return u


def bad_seeds(ego) -> dict:
    """Risk seeds: alerted / watchlist / high-CRR / high base_risk nodes."""
    seeds = {}
    for n, attrs in ego.nodes(data=True):
        br = attrs.get("base_risk", 0.0)
        if br >= config.BAD_SEED_THRESHOLD or attrs.get("alerted"):
            seeds[n] = max(br, 0.05)
    return seeds


def _ppr_scores(u: nx.Graph, seeds: dict) -> dict:
    total = sum(seeds.values())
    personalization = {n: seeds.get(n, 0.0) / total for n in u.nodes}
    return nx.pagerank(u, alpha=config.PPR_ALPHA, personalization=personalization,
                       weight="weight")


def _khop_trace(u: nx.Graph, seeds: dict, depth: int):
    """Best (max-score) risk path from any bad seed to each node.

    Returns (scores, paths): scores[v] = base(seed)*gamma^len*prod(w);
    paths[v] = [seed, ..., v]. Dijkstra-like on -log(score); ego nets are
    small so a simple label-correcting loop is fine.
    """
    gamma = config.KHOP_GAMMA
    best = {n: (0.0, None) for n in u.nodes}  # score, predecessor
    frontier = {}
    for s, br in seeds.items():
        if s in best:
            best[s] = (br, None)
            frontier[s] = br
    for _ in range(depth):
        nxt = {}
        for n, score in frontier.items():
            for nb in u.neighbors(n):
                cand = score * gamma * u[n][nb]["weight"]
                if cand > best[nb][0] + 1e-12:
                    best[nb] = (cand, n)
                    nxt[nb] = cand
        frontier = nxt
        if not frontier:
            break
    scores = {n: s for n, (s, _) in best.items()}
    paths = {}
    for n, (s, pred) in best.items():
        if s <= 0:
            continue
        path, cur = [n], pred
        while cur is not None:
            path.append(cur)
            cur = best[cur][1]
        paths[n] = list(reversed(path))
    return scores, paths


def score_propagation(ego, method: str = None) -> None:
    method = method or config.PROP_METHOD
    u = _undirected_weighted(ego)
    seeds = bad_seeds(ego)
    depth = ego.graph.get("depth", config.EGO_DEPTH_SCORE)

    if not seeds:
        for n in ego.nodes:
            ego.nodes[n].update(prop_risk=0.0, risk_path=None)
        ego.graph["prop_method"] = method
        return

    khop_scores, paths = _khop_trace(u, seeds, depth)

    if method == "ppr":
        raw = _ppr_scores(u, seeds)
    else:
        raw = khop_scores
    mx = max(raw.values()) or 1.0
    for n in ego.nodes:
        ego.nodes[n]["prop_risk"] = raw.get(n, 0.0) / mx
        ego.nodes[n]["risk_path"] = paths.get(n)
        ego.nodes[n]["is_bad_seed"] = n in seeds
    ego.graph["prop_method"] = method
