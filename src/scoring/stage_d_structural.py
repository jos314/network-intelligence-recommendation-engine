"""Stage D — structural / typology features over the ego-network (§4.D).

  * cycle: node participates in a directed circular flow (layering) on
    transaction edges — bounded simple cycles.
  * community: greedy-modularity communities on the undirected view; a node
    in a tight group (size >= MIN_COMMUNITY_SIZE) inherits the group's mean
    base risk (dense groups moving funds together, FlowScope-style).
  * centrality: mean of degree + betweenness centrality — hubs, brokers,
    bridge nodes (nominee signature).
"""
import networkx as nx

from .. import config
from ..graph.build import TXN


def _txn_digraph(ego) -> nx.DiGraph:
    d = nx.DiGraph()
    d.add_nodes_from(ego.nodes)
    for u, v, data in ego.edges(data=True):
        if data.get("kind") == TXN:
            d.add_edge(u, v)
    return d


def score_structural(ego) -> None:
    txn_dg = _txn_digraph(ego)
    und = nx.Graph(txn_dg.to_undirected())
    und.add_nodes_from(ego.nodes)

    # cycles. Enumerating cycles costs O(#cycles), which explodes
    # combinatorially through hub nodes — so the test is SCC-based
    # (linear): a node lies on SOME directed cycle iff its strongly
    # connected component has size >= 2 (or it has a self-loop). Short
    # cycles (<= MAX_CYCLE_LEN) are only enumerated inside SMALL SCCs,
    # where the count cannot blow up.
    in_cycle = set()
    for comp in nx.strongly_connected_components(txn_dg):
        if len(comp) < 2:
            continue
        if len(comp) <= config.CYCLE_SCC_ENUM_MAX:
            sub = txn_dg.subgraph(comp)
            try:
                for cyc in nx.simple_cycles(sub, length_bound=config.MAX_CYCLE_LEN):
                    if len(cyc) >= 2:
                        in_cycle.update(cyc)
            except TypeError:  # older networkx: no length_bound
                for cyc in nx.simple_cycles(sub):
                    if 2 <= len(cyc) <= config.MAX_CYCLE_LEN:
                        in_cycle.update(cyc)
        else:
            # hub-scale SCC: flag membership (a circular flow exists
            # through these nodes) without enumerating
            in_cycle.update(comp)

    # communities on the full undirected view (both edge families);
    # label propagation (near-linear) once the ego outgrows modularity
    full_und = nx.Graph()
    full_und.add_nodes_from(ego.nodes)
    for u, v in ego.edges():
        full_und.add_edge(u, v)
    community_risk = {}
    community_id = {}
    if full_und.number_of_edges() > 0:
        if len(full_und) > config.STAGE_D_COMMUNITY_LP_N:
            communities = nx.algorithms.community.label_propagation_communities(full_und)
        else:
            communities = nx.algorithms.community.greedy_modularity_communities(full_und)
        cid = 0
        for com in communities:
            if len(com) < config.MIN_COMMUNITY_SIZE:
                continue
            mean_base = sum(ego.nodes[n].get("base_risk", 0.0) for n in com) / len(com)
            for n in com:
                community_risk[n] = mean_base
                community_id[n] = cid  # membership feeds the cluster view
            cid += 1

    deg = nx.degree_centrality(full_und) if len(full_und) > 1 else {}
    if len(full_und) <= 2:
        btw = {}
    elif len(full_und) > config.STAGE_D_BTW_EXACT_N:
        # sampled betweenness: unbiased estimate, bounded cost on big egos
        btw = nx.betweenness_centrality(
            full_und, k=min(config.STAGE_D_BTW_SAMPLE, len(full_und)), seed=42)
    else:
        btw = nx.betweenness_centrality(full_und)

    for n in ego.nodes:
        comps = {
            "cycle": 1.0 if n in in_cycle else 0.0,
            "community": min(community_risk.get(n, 0.0), 1.0),
            "centrality": min((deg.get(n, 0.0) + btw.get(n, 0.0)) / 2.0, 1.0),
        }
        ego.nodes[n]["struct_components"] = comps
        ego.nodes[n]["struct_risk"] = sum(
            config.STAGE_D_WEIGHTS[k] * v for k, v in comps.items()
        )
        ego.nodes[n]["in_cycle"] = n in in_cycle
        ego.nodes[n]["community_id"] = community_id.get(n)
