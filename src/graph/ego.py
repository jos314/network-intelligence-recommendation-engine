"""P1 — ego-network extraction (§3.3).

BFS to depth K from a seed over the UNION of both edge families; reachability
is treated as undirected (association risk spreads both ways) while edge
direction survives as metadata for flow typologies. Hop distance from the
seed is stored on each node (drives decay and the ring layout).
"""
from collections import deque

import networkx as nx

from .. import config


def node_flow_summary(ego: nx.MultiDiGraph, node: str) -> dict:
    """Aggregate a node's transaction edges (both directions) within the
    ego-network: total amount, transaction count, activity window. Feeds
    the counterparty table, the node inspector, and the evidence pack."""
    total, count, first, last = 0.0, 0, None, None
    for _, _, d in list(ego.out_edges(node, data=True)) + list(ego.in_edges(node, data=True)):
        if d.get("kind") != "txn":
            continue
        total += float(d.get("total_amount_base", 0.0))
        count += int(d.get("txn_count", 0))
        f, l = d.get("first_run_date"), d.get("last_run_date")
        if f is not None and (first is None or f < first):
            first = f
        if l is not None and (last is None or l > last):
            last = l
    return {"total_amount": total, "txn_count": count,
            "first_seen": first, "last_seen": last}


def ego_network(g: nx.MultiDiGraph, seed: str, depth: int = None) -> nx.MultiDiGraph:
    if depth is None:
        depth = config.EGO_DEPTH_SCORE
    if seed not in g:
        raise KeyError("seed %r not in graph" % seed)
    hops = {seed: 0}
    q = deque([seed])
    while q:
        n = q.popleft()
        if hops[n] >= depth:
            continue
        for nb in set(g.successors(n)) | set(g.predecessors(n)):
            if nb not in hops:
                hops[nb] = hops[n] + 1
                q.append(nb)
    sub = g.subgraph(hops.keys()).copy()
    for n, h in hops.items():
        sub.nodes[n]["hop"] = h
    sub.graph["seed"] = seed
    sub.graph["depth"] = depth
    return sub
