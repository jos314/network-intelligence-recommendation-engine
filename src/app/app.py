"""P4 — the analyst screen (§6): Obsidian-style force-directed ego-graph,
one subject at a time, plus the ranked counterparty table.

Encodings (per the build plan):
  * node SIZE   = final_risk (bigger = riskier)
  * node COLOUR = decision (red = SAR, yellow = EDD, neutral = No action)
  * ring/border = TM-alerted; diamond = the case subject (pinned focus)
  * dashed grey rectangle = external counterparty (PSEUDO_, no KYC held)
  * solid edge  = transaction (arrow along money flow, thickness = amount
    relative to this case's largest flow); dashed blue = identity link
  * accent glow = the top propagated-risk path (§5.1's highlighted subgraph)

Honesty rules baked into this screen:
  * a screening that did not run is shown as "not screened", never as 0
  * the risk score is labelled uncalibrated whenever the calibrator fell
    back to identity (too few labels)
  * filters disclose what they hide ("showing X of Y"), and alerted nodes
    plus highlighted-path nodes are exempt from the min-risk cut — evidence
    is never silently filtered

Nothing on this screen is hard-coded to the demo fixture: cases, labels,
KPIs, drivers, paths, and edge scales all derive from whatever the loaders
return, so dropping the real tables into data/ changes every element here.

Run:  .venv/bin/python -m src.app.app   (then open http://127.0.0.1:8050)
"""
import json
import math
import os
import re

import dash
import dash_cytoscape as cyto
import pandas as pd
from dash import ALL, Input, Output, State, dash_table, dcc, html
from dash.dash_table.Format import Format, Group, Scheme
from dash.exceptions import PreventUpdate

from .. import config
from ..conclusion.store import read_conclusion, write_conclusion
from ..data_access import DataAccess
from ..explain.paths import key_paths
from ..graph.ego import node_flow_summary
from ..ingest.crosswalk import NODE_EXTERNAL
from .auth import demo_active, verify_credentials

# ------------------------------------------------- cytoscape-side palette
# (the canvas cannot read CSS variables; everything else themes via assets/)
CY = {
    "light": {"text": "#1f2430", "muted": "#7a8194", "edge": "#c5cad4",
              "node": "#9aa3b2", "halo": "#ffffff"},
    "dark": {"text": "#e8eaf0", "muted": "#8b93a7", "edge": "#39404f",
             "node": "#5d6675", "halo": "#101319"},
}
RISK_RED = {"light": "#d64545", "dark": "#e05c5c"}
RISK_YELLOW = {"light": "#d99a1f", "dark": "#e6b23c"}
OK_GREEN = {"light": "#3d9a63", "dark": "#4fb87a"}
ACCENT = {"light": "#4c7fd6", "dark": "#6b9aea"}

DECISION_CHIP = {config.DECISION_SAR: "chip chip-sar",
                 config.DECISION_EDD: "chip chip-edd",
                 config.DECISION_NO_ACTION: "chip chip-ok"}

GLOSSARY = {
    "cycle": "Circular transaction flow — money returns near its origin (layering)",
    "dense_group": "Dense counterparty community moving funds together",
    "bridge": "Bridge / hub position between otherwise separate groups",
    "risk_score": "Aggregated network risk in [0,1]; decision bands are set on this score",
    "proximity": "Stage C: how close this entity sits to alerted/high-risk entities",
}


def _stylesheet(theme):
    t = CY[theme]
    return [
        {"selector": "node", "style": {
            # kept deliberately small relative to edges (analyst feedback);
            # deeper hops shrink further so the hierarchy reads at a glance
            "width": "mapData(risk, 0, 1, 12, 34)",
            "height": "mapData(risk, 0, 1, 12, 34)",
            "background-color": t["node"],
            "label": "data(label)", "font-size": "9px",
            # labels fade out when zoomed away — the declutter that matters
            "min-zoomed-font-size": 9,
            "text-wrap": "ellipsis", "text-max-width": "84px",
            "color": t["text"], "text-valign": "bottom", "text-margin-y": "3px",
            "text-outline-color": t["halo"], "text-outline-width": 2,
            "border-width": 0,
        }},
        {"selector": "node[hop = 2]", "style": {
            "width": "mapData(risk, 0, 1, 9, 24)",
            "height": "mapData(risk, 0, 1, 9, 24)",
            "font-size": "8px",
        }},
        {"selector": "node[hop = 3]", "style": {
            "width": "mapData(risk, 0, 1, 7, 18)",
            "height": "mapData(risk, 0, 1, 7, 18)",
            "font-size": "8px",
        }},
        {"selector": "node[?is_cluster]", "style": {
            "shape": "hexagon",
            "width": "mapData(cluster_size, 1, 60, 24, 64)",
            "height": "mapData(cluster_size, 1, 60, 24, 64)",
            "border-width": 2, "border-style": "double",
            "border-color": t["muted"], "font-size": "10px",
        }},
        {"selector": 'node[decision = "EDD"]',
         "style": {"background-color": RISK_YELLOW[theme]}},
        {"selector": 'node[decision = "SAR"]',
         "style": {"background-color": RISK_RED[theme]}},
        # externals: not bank customers, no KYC — visually distinct from
        # "scored and cleared" grey circles
        {"selector": 'node[node_type = "%s"]' % NODE_EXTERNAL,
         "style": {"shape": "round-rectangle", "border-width": 2,
                   "border-style": "dashed", "border-color": t["muted"]}},
        {"selector": "node[?alerted]",
         "style": {"border-width": 3, "border-style": "solid",
                   "border-color": RISK_RED[theme]}},
        {"selector": "node[?is_seed]",
         "style": {"shape": "diamond", "border-width": 3, "border-style": "solid",
                   "border-color": ACCENT[theme],
                   "width": "mapData(risk, 0, 1, 22, 40)",
                   "height": "mapData(risk, 0, 1, 22, 40)"}},
        {"selector": "edge", "style": {
            "width": "mapData(weight, 0, 1, 1.2, 4)", "line-color": t["edge"],
            "curve-style": "bezier", "opacity": 0.6,
        }},
        {"selector": 'edge[kind = "txn"]', "style": {
            "target-arrow-shape": "triangle", "target-arrow-color": t["edge"],
            "arrow-scale": 0.8,
        }},
        # the expansion tree: parent -> revealed child, so drill structure
        # stays legible after several expansions
        {"selector": "edge.treeedge", "style": {
            "line-color": ACCENT[theme], "target-arrow-color": ACCENT[theme],
            "opacity": 0.85, "z-index": 5,
        }},
        # the expansion lens: emphasis, never removal — HARD two-tier
        # contrast. The trail glows and keeps full colour; everything else
        # recedes into a small unlabeled ghost. Edges touching the trail
        # stay as whispers so back-links remain traceable.
        {"selector": "node.lenson", "style": {
            "overlay-color": ACCENT[theme], "overlay-opacity": 0.10,
            "overlay-padding": 5,
        }},
        {"selector": "node.lensbg", "style": {
            "opacity": 0.12, "text-opacity": 0,
            "width": "mapData(risk, 0, 1, 6, 13)",
            "height": "mapData(risk, 0, 1, 6, 13)",
        }},
        {"selector": "edge.lenstrail", "style": {"opacity": 0.95, "z-index": 6}},
        {"selector": "edge.lensmid", "style": {"opacity": 0.22}},
        {"selector": "edge.lensbg", "style": {"opacity": 0.04}},
        # focus mode: everything outside subject -> focus -> its children
        # fades back (tap empty canvas to clear)
        {"selector": ".dimmed", "style": {
            "opacity": 0.13, "text-opacity": 0, "events": "yes",
        }},
        {"selector": 'edge[kind != "txn"]',
         "style": {"line-style": "dashed", "line-color": ACCENT[theme]}},
        # §5.1: the key propagated-risk path, highlighted in the graph view
        {"selector": "node.onpath", "style": {
            "border-width": 4, "border-color": ACCENT[theme],
            "border-style": "double"}},
        {"selector": "edge.onpath", "style": {
            "line-color": ACCENT[theme], "target-arrow-color": ACCENT[theme],
            "width": 4, "opacity": 1.0, "z-index": 9}},
        {"selector": "node.focused", "style": {
            "overlay-color": ACCENT[theme], "overlay-opacity": 0.18,
            "overlay-padding": 6}},
        {"selector": "node:selected", "style": {"border-width": 4,
                                                "border-color": ACCENT[theme]}},
    ]


# Layout dicts are cached so an unchanged (mode, seed) returns the SAME
# object — otherwise every render hands cytoscape a "new" layout and the
# graph re-springs on theme flips and focus taps (camera-stability item).
_LAYOUT_CACHE = {}


def _layout_spec(mode, seed):
    key = (mode, seed)
    if key not in _LAYOUT_CACHE:
        if mode == "rings":  # §6: nodes ringed by hop distance around the seed
            spec = {"name": "breadthfirst", "circle": True, "animate": False,
                    "fit": False,
                    "roots": '[id = "%s"]' % seed, "spacingFactor": 1.1}
        elif mode == "force":  # static: compute once, no motion
            spec = {"name": "cose", "animate": False, "fit": False,
                    "randomize": False, "nodeRepulsion": 12000,
                    "idealEdgeLength": 80, "padding": 24}
        else:
            # "live" (default): PRESET — cytoscape never runs a layout.
            # Nodes are placed from the server's deterministic hints and a
            # continuous clientside force simulation (Obsidian-style) keeps
            # them settled; nothing ever reshuffles or randomizes.
            spec = {"name": "preset", "fit": False, "animate": False}
        _LAYOUT_CACHE[key] = spec
    return _LAYOUT_CACHE[key]


_STATIC_LAYOUT_LAST = {}


def _static_layout(mode, seed, els):
    """Layout dict for the graph, with the identity dash-cytoscape needs.

    dash-cytoscape re-runs a layout only when the `layout` prop is a NEW
    object. Static layouts (rings/force/clusters) must re-run when the
    element SET changes — otherwise nodes added later (expansions, filter
    changes, a mode round-trip) are never laid out and pile up unplaced.
    Same element set -> same cached dict -> no re-layout, so unrelated
    re-renders (theme, focus) never reshuffle. `fit` stays False everywhere:
    the view-sig clientside pass owns the camera.
    """
    base = _layout_spec(mode, seed)
    if base.get("name") == "preset":  # live mode: never lays out anyway
        return base
    key = hash(frozenset((e["data"].get("id"), e["data"].get("source"),
                          e["data"].get("target"), e["data"].get("kind"))
                         for e in els))
    last = _STATIC_LAYOUT_LAST.get((mode, seed))
    if last is not None and last[0] == key:
        return last[1]
    spec = dict(base)
    _STATIC_LAYOUT_LAST[(mode, seed)] = (key, spec)
    return spec


# --------------------------------------------------- data access (lazy)
# Prebuilt masked graph (full scale) when parquet is present, demo fixture
# otherwise. Cases score on first selection and cache — eager-scoring six
# hub-scale networks would stall startup for no benefit.
DA = DataAccess()
CASES = DA.case_ids()
print("Analyst app data source: %s" % DA.source.upper())


def _result(case_id):
    return DA.result(case_id)


def _short(n: str) -> str:
    """Compact display for unnamed ids in any scheme (PSEUDO_n, CUS_…, raw)."""
    u = str(n).upper()
    if u.startswith("PSEUDO_"):
        return "ext…" + n[-4:]
    if u.startswith("CUS_"):
        return "cus…" + n[-4:]
    return n


def _display_id(n, attrs) -> str:
    """Prefer the human-meaningful original id over the masked graph key."""
    orig = attrs.get("original_id")
    return _short(orig) if orig else _short(n)


def _node_label(n, attrs) -> str:
    return (attrs.get("name") or _display_id(n, attrs))[:22]


def _name_of(ego, n) -> str:
    if n in ego.nodes:
        return ego.nodes[n].get("name") or _display_id(n, ego.nodes[n])
    return _short(n)


def _fmt_money(x) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1_000_000_000:
        return "$%.2fB" % (x / 1_000_000_000)
    if abs(x) >= 1_000_000:
        return "$%.2fM" % (x / 1_000_000)
    if abs(x) >= 10_000:
        return "$%.0fK" % (x / 1_000)
    return "$%s" % format(int(round(x)), ",")


def _fmt_date(ts) -> str:
    return "—" if ts is None else str(ts)[:10]


def _top_path_members(case_id):
    """Nodes and consecutive pairs of the strongest key path."""
    ego = _result(case_id)["ego"]
    paths = key_paths(ego, top_k=1)
    if not paths:
        return set(), set()
    p = paths[0]["path"]
    return set(p), {frozenset(pair) for pair in zip(p, p[1:])}


def expand_top(ego, node, k=None):
    """The drill-down primitive: a node's top-K neighbours worth revealing
    (alerted first, then by risk). Used by double-click and the Expand
    button — deeper hops appear only through these expansions."""
    k = k or config.EXPAND_TOP_K
    nbrs = set(ego.successors(node)) | set(ego.predecessors(node))
    nbrs.discard(node)
    ranked = sorted(nbrs, key=lambda m: (not ego.nodes[m].get("alerted"),
                                         -ego.nodes[m].get("final_risk", 0.0)))
    return set(ranked[:k]) | {node}


def _cluster_groups(ego):
    """Community id -> member list (communities found by Stage D)."""
    groups = {}
    for n, a in ego.nodes(data=True):
        cid = a.get("community_id")
        if cid is not None:
            groups.setdefault(cid, []).append(n)
    return groups


def _elements(case_id, top_n, min_risk, edge_kinds, expanded=None,
              highlight=False, mode="entities", isolate=False):
    """Graph elements + disclosure stats for the drill-down view model.

    entities mode: subject + its TOP-N riskiest direct counterparties;
    deeper hops appear only via expansions (double-click / Expand button).
    The highlighted key path is always force-included, alerted nodes among
    the visible set are exempt from the min-risk cut, and everything hidden
    is counted in `stats` — never silently dropped.

    isolate=True (with expansions present) switches to the expansion lens:
    only subject + drilled parents + revealed children + the lower-depth
    nodes those children connect back to. Cross-links between different
    expansions render whenever both endpoints are visible.

    clusters mode: one node per Stage-D community (plus the subject and an
    "unclustered" bucket), edges = aggregated flows between groups — the
    broad view of the case.

    Every node carries deterministic placement hints (x0/y0; phint/kidx for
    expansion children) so the live-physics view never reshuffles: hop-1
    sits on a ring, children bloom out of their parent.

    Returns (elements, stats).
    """
    if mode == "clusters":
        return _cluster_elements(case_id)
    ego = _result(case_id)["ego"]
    seed = ego.graph["seed"]
    # expanded: {child: parent} (parent None when revealed without a drill,
    # e.g. table click); plain iterables accepted for backward compat
    if isinstance(expanded, dict):
        parents = {c: p for c, p in expanded.items() if p}
        expanded = set(expanded)
    else:
        parents = {}
        expanded = set(expanded or [])
    tree_pairs = {frozenset((c, p)) for c, p in parents.items()}
    path_nodes, path_pairs = _top_path_members(case_id) if highlight else (set(), set())

    hop1 = [n for n, a in ego.nodes(data=True) if a.get("hop") == 1]
    top_hop1 = sorted(hop1, key=lambda n: ego.nodes[n].get("final_risk", 0.0),
                      reverse=True)[:top_n]
    baseline = {seed} | set(top_hop1) | expanded | path_nodes

    visible, hidden_flagged = set(), 0
    path_revealed, alert_exempt = 0, 0
    isolated = bool(isolate) and bool(expanded)
    for n in baseline:
        if n not in ego.nodes:
            continue
        a = ego.nodes[n]
        forced = n == seed or n in expanded or n in path_nodes
        below_cut = a.get("final_risk", 0.0) < min_risk
        if below_cut and not (forced or a.get("alerted")):
            if a.get("decision") in (config.DECISION_EDD, config.DECISION_SAR):
                hidden_flagged += 1
            continue
        if below_cut and not forced and a.get("alerted"):
            alert_exempt += 1  # kept ONLY because alerted — disclose it
        if n in path_nodes and n not in expanded and n != seed \
                and n not in top_hop1:
            path_revealed += 1
        visible.add(n)

    alerted_offscreen = sum(1 for n, a in ego.nodes(data=True)
                            if a.get("alerted") and n not in visible)

    # hard canvas backstop (drill-down keeps views tiny; this only fires if
    # someone expands dozens of hubs). Must-draw set always survives.
    render_capped = 0
    if len(visible) > config.RENDER_MAX_NODES:
        must = {n for n in visible
                if n == seed or n in expanded or n in path_nodes}
        rest = sorted(visible - must,
                      key=lambda n: ego.nodes[n].get("final_risk", 0.0),
                      reverse=True)
        budget = max(config.RENDER_MAX_NODES - len(must), 0)
        render_capped = len(rest) - budget
        visible = must | set(rest[:budget])

    # -------- the expansion lens: EMPHASIS, never removal ---------------
    # Two tiers with HARD contrast (a half-lit middle tier swallowed the
    # whole graph on dense alert rings and the toggle read as a no-op):
    #   trail (subject + drilled parents + revealed children) -> full
    #     brightness plus an accent glow, trail edges bright
    #   everything else -> small unlabeled ghosts; edges touching the trail
    #     stay as whispers so back-links remain traceable
    lens_trail = None
    if isolated:
        lens_trail = ({seed} | expanded
                      | {p for p in parents.values() if p}) & visible

    # -------- deterministic placement hints (no reshuffle, ever) --------
    # seed at origin; hop-1 on a risk-ranked ring; expansion children fan
    # outward from their parent's hint; parentless reveals on outer rings.
    hints, kidx = {seed: (0.0, 0.0)}, {}
    ring_n = max(len(top_hop1), 1)
    for i, n in enumerate(top_hop1):
        ang = 2 * math.pi * i / ring_n - math.pi / 2
        hints[n] = (300 * math.cos(ang), 300 * math.sin(ang))
    fan_count = {}
    for child in sorted(expanded,
                        key=lambda c: ego.nodes[c].get("hop", 9) if c in ego.nodes else 9):
        if child in hints or child not in ego.nodes:
            continue
        parent = parents.get(child)
        hop = ego.nodes[child].get("hop", 2)
        if parent in hints:
            px, py = hints[parent]
            base_ang = math.atan2(py, px) if (px, py) != (0.0, 0.0) else 0.0
            k = fan_count.get(parent, 0)
            fan_count[parent] = k + 1
            kidx[child] = k
            ang = base_ang + ((k % 7) - 3) * 0.33
            r = 130 + 40 * (k // 7)
            hints[child] = (px + r * math.cos(ang), py + r * math.sin(ang))
        else:  # revealed without a drill (table click): stable outer ring
            ang = (sum(ord(c) for c in child) % 360) * math.pi / 180
            r = 300 + 170 * max(hop - 1, 1)
            hints[child] = (r * math.cos(ang), r * math.sin(ang))

    els = []
    for n in visible:
        a = ego.nodes[n]
        classes = []
        if n in path_nodes:
            classes.append("onpath")
        if lens_trail is not None:
            classes.append("lenson" if n in lens_trail else "lensbg")
        if n not in hints:  # e.g. back-link nodes in isolate mode
            ang = (sum(ord(c) for c in n) % 360) * math.pi / 180
            hints[n] = (430 * math.cos(ang), 430 * math.sin(ang))
        hx, hy = hints[n]
        data = {
            "id": n, "label": _node_label(n, a),
            "name": a.get("name") or _short(n),
            "risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision", config.DECISION_NO_ACTION),
            "alerted": bool(a.get("alerted")), "is_seed": n == seed,
            "hop": a.get("hop"), "node_type": a.get("node_type"),
            "x0": round(hx, 1), "y0": round(hy, 1),
        }
        if n in parents and parents[n] in visible:
            data["phint"] = parents[n]
            data["kidx"] = kidx.get(n, 0)
        els.append({"data": data, "classes": " ".join(classes)})

    def _lens_edge_class(u, v):
        """Trail edges bright, edges touching the trail (back-links) stay
        as whispers, the rest all but disappears — dim, never delete."""
        if lens_trail is None:
            return None
        if u in lens_trail and v in lens_trail:
            return "lenstrail"
        if u in lens_trail or v in lens_trail:
            return "lensmid"
        return "lensbg"

    # aggregate parallel edges: txn per (u, v); identity per (u, v, kind)
    txn_groups, ident_groups, total_groups = {}, {}, set()
    for u, v, d in ego.edges(data=True):
        kind = d.get("kind")
        gkey = (u, v) if kind == "txn" else (u, v, kind)
        total_groups.add((kind == "txn", gkey))
        if u not in visible or v not in visible:
            continue
        family = "txn" if kind == "txn" else "identity"
        if family not in edge_kinds:
            continue
        if kind == "txn":
            g = txn_groups.setdefault((u, v), {
                "amount": 0.0, "count": 0, "first": None, "last": None,
                "src_country": None, "dst_country": None})
            g["amount"] += float(d.get("total_amount_base", 0.0))
            g["count"] += int(d.get("txn_count", 0))
            f, l = d.get("first_run_date"), d.get("last_run_date")
            if f is not None and (g["first"] is None or f < g["first"]):
                g["first"] = f
            if l is not None and (g["last"] is None or l > g["last"]):
                g["last"] = l
            g["src_country"] = g["src_country"] or d.get("src_country")
            g["dst_country"] = g["dst_country"] or d.get("dst_country")
        else:
            g = ident_groups.setdefault((u, v, kind), {"weight": 0.0, "value": None})
            g["weight"] = max(g["weight"], float(d.get("weight", 0.5)))
            g["value"] = g["value"] or d.get("value")

    # thickness scale from this ego's own largest AGGREGATED flow
    max_amt = max((g["amount"] for g in txn_groups.values()), default=1.0) or 1.0
    for (u, v), g in txn_groups.items():
        classes = []
        if frozenset((u, v)) in path_pairs:
            classes.append("onpath")
        if frozenset((u, v)) in tree_pairs:
            classes.append("treeedge")
        lens_cls = _lens_edge_class(u, v)
        if lens_cls:
            classes.append(lens_cls)
        els.append({"data": {
            "source": u, "target": v, "kind": "txn",
            "weight": round(min(g["amount"] / max_amt, 1.0), 3),
            "amount": round(g["amount"], 2), "count": g["count"],
            "first": _fmt_date(g["first"]), "last": _fmt_date(g["last"]),
            "src_country": g["src_country"], "dst_country": g["dst_country"],
        }, "classes": " ".join(classes)})
    for (u, v, kind), g in ident_groups.items():
        icls = ["treeedge"] if frozenset((u, v)) in tree_pairs else []
        lens_cls = _lens_edge_class(u, v)
        if lens_cls:
            icls.append(lens_cls)
        els.append({"data": {"source": u, "target": v, "kind": kind,
                             "weight": round(min(g["weight"], 1.0), 3),
                             "value": g["value"]},
                    "classes": " ".join(icls)})

    stats = {
        "mode": "entities", "isolated": isolated,
        "nodes_shown": len(visible), "nodes_total": ego.number_of_nodes(),
        "edges_shown": len(txn_groups) + len(ident_groups),
        "edges_total": len(total_groups),
        "hop1_shown": sum(1 for n in visible if ego.nodes[n].get("hop") == 1),
        "hop1_total": len(hop1),
        "expanded_shown": sum(1 for n in visible
                              if n in expanded and ego.nodes[n].get("hop", 0) > 1),
        "alerted_offscreen": alerted_offscreen, "hidden_flagged": hidden_flagged,
        "alert_exempt": alert_exempt, "min_risk": float(min_risk or 0.0),
        "path_revealed": path_revealed, "render_capped": render_capped,
        "lens_trail": len(lens_trail) if isolated else 0,
        "lens_faded": len(visible) - len(lens_trail) if isolated else 0,
    }
    return els, stats


def _cluster_elements(case_id):
    """The broad view: one node per Stage-D community, plus the subject and
    an 'unclustered' bucket; edges = aggregated flows between groups."""
    ego = _result(case_id)["ego"]
    seed = ego.graph["seed"]
    groups = _cluster_groups(ego)

    UNCLUSTERED = "cl_rest"
    node_group = {}
    for cid, members in groups.items():
        for n in members:
            node_group[n] = "cl_%s" % cid
    for n in ego.nodes:
        if n not in node_group:
            node_group[n] = UNCLUSTERED
    node_group[seed] = seed  # the subject always stands alone

    order = [config.DECISION_NO_ACTION, config.DECISION_EDD, config.DECISION_SAR]
    els = []
    seed_a = ego.nodes[seed]
    els.append({"data": {
        "id": seed, "label": _node_label(seed, seed_a),
        "name": seed_a.get("name") or _short(seed),
        "risk": round(seed_a.get("final_risk", 0.0), 3),
        "decision": seed_a.get("decision", config.DECISION_NO_ACTION),
        "alerted": bool(seed_a.get("alerted")), "is_seed": True,
        "node_type": seed_a.get("node_type"),
        "x0": 0.0, "y0": 0.0,
    }, "classes": ""})

    def _cluster_data(gid, members, label):
        risks = [ego.nodes[m].get("final_risk", 0.0) for m in members]
        worst = max(members, key=lambda m: order.index(
            ego.nodes[m].get("decision", config.DECISION_NO_ACTION)))
        alerted_k = sum(1 for m in members if ego.nodes[m].get("alerted"))
        return {
            "id": gid, "label": label, "is_cluster": True,
            "cluster_size": len(members),
            "risk": round(max(risks, default=0.0), 3),
            "decision": ego.nodes[worst].get("decision", config.DECISION_NO_ACTION),
            "alerted": alerted_k > 0, "alerted_count": alerted_k,
        }

    specs = []
    for cid, members in sorted(groups.items()):
        members = [m for m in members if m != seed]
        if not members:
            continue
        top = max(members, key=lambda m: ego.nodes[m].get("final_risk", 0.0))
        label = "%s +%d" % ((ego.nodes[top].get("name")
                             or _display_id(top, ego.nodes[top]))[:16],
                            len(members) - 1)
        specs.append(_cluster_data("cl_%s" % cid, members, label))
    rest = [n for n in ego.nodes if node_group[n] == UNCLUSTERED and n != seed]
    if rest:
        specs.append(_cluster_data(UNCLUSTERED, rest, "unclustered (%d)" % len(rest)))

    # deterministic placement, same contract as the entity view: subject at
    # the origin, communities on a ring — heaviest (most alerted members,
    # then largest) start at 12 o'clock; the radius grows with the count so
    # hexagons never open overlapped. The live-physics sim relaxes from here.
    specs.sort(key=lambda d: (-d["alerted_count"], -d["cluster_size"], d["id"]))
    ring_r = max(300.0, len(specs) * 82 / (2 * math.pi))
    for i, d in enumerate(specs):
        ang = 2 * math.pi * i / max(len(specs), 1) - math.pi / 2
        d["x0"] = round(ring_r * math.cos(ang), 1)
        d["y0"] = round(ring_r * math.sin(ang), 1)
        els.append({"data": d, "classes": "cluster"})

    flows = {}
    for u, v, d in ego.edges(data=True):
        if d.get("kind") != "txn":
            continue
        gu, gv = node_group[u], node_group[v]
        if gu == gv:
            continue
        g = flows.setdefault((gu, gv), {"amount": 0.0, "count": 0})
        g["amount"] += float(d.get("total_amount_base", 0.0))
        g["count"] += int(d.get("txn_count", 0))
    max_amt = max((g["amount"] for g in flows.values()), default=1.0) or 1.0
    for (gu, gv), g in flows.items():
        els.append({"data": {
            "source": gu, "target": gv, "kind": "txn",
            "weight": round(min(g["amount"] / max_amt, 1.0), 3),
            "amount": round(g["amount"], 2), "count": g["count"],
        }, "classes": ""})

    stats = {
        "mode": "clusters",
        "nodes_shown": len(els) and sum(1 for e in els if "source" not in e["data"]),
        "nodes_total": ego.number_of_nodes(),
        "edges_shown": len(flows), "edges_total": len(flows),
        "clusters": len(groups), "unclustered": len(rest),
        "hop1_shown": 0, "hop1_total": 0, "expanded_shown": 0,
        "alerted_offscreen": 0, "hidden_flagged": 0,
        "path_revealed": 0, "render_capped": 0,
    }
    return els, stats


_FRAME_COLUMNS = ["entity", "id", "type", "hop", "final_risk", "decision",
                  "alerted", "total_amount", "txn_count", "first_seen",
                  "last_seen", "volume_share_%", "shared_attrs", "case_id"]


_FRAME_CACHE = {}


def _counterparty_frame(case_id):
    """Ranked counterparty rows, capped at TABLE_MAX_ROWS (top by risk).

    Returns (frame, total_counterparties) — the cap is disclosed next to
    the table, never applied silently. Cached per case (the ego is fixed)."""
    if case_id in _FRAME_CACHE:
        return _FRAME_CACHE[case_id]
    ego = _result(case_id)["ego"]
    seed = ego.graph["seed"]
    ranked = sorted((n for n in ego.nodes if n != seed),
                    key=lambda n: ego.nodes[n].get("final_risk", 0.0),
                    reverse=True)
    total = len(ranked)
    rows = []
    for n in ranked[:config.TABLE_MAX_ROWS]:
        a = ego.nodes[n]
        flow = node_flow_summary(ego, n)
        hop = a.get("hop")
        rows.append({
            "entity": a.get("name") or _display_id(n, a),
            "id": a.get("original_id") or n,
            "type": "external" if a.get("node_type") == NODE_EXTERNAL else "customer",
            "hop": hop,
            "final_risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision"),
            "alerted": "yes" if a.get("alerted") else "",
            "total_amount": round(flow["total_amount"], 0),
            "txn_count": flow["txn_count"],
            "first_seen": _fmt_date(flow["first_seen"]),
            "last_seen": _fmt_date(flow["last_seen"]),
            # volume share is defined only for DIRECT counterparties (hop 1)
            "volume_share_%": round(100 * a.get("rel_components", {})
                                    .get("volume_share", 0.0), 1) if hop == 1 else None,
            "shared_attrs": ", ".join(k.replace("same_", "")
                                      for k in a.get("rel_shared_kinds", [])),
            "case_id": case_id,
            "_node": n,  # graph key for click-to-inspect (hidden column)
        })
    if not rows:  # isolated seed: render an empty table, never a KeyError
        out = (pd.DataFrame(columns=_FRAME_COLUMNS + ["_node"]), 0)
    else:
        out = (pd.DataFrame(rows), total)
    _FRAME_CACHE[case_id] = out
    return out


# ------------------------------------------------------ table filtering
# Custom, server-side (native Dash filtering is case-sensitive, has no
# operator UI, and can't compare numbers/dates properly). Operators are
# exposed as dropdowns; dates as range pickers.
_NUM_OPS = {
    "gte": lambda s, v: s >= v,
    "gt": lambda s, v: s > v,
    "eq": None,  # handled specially (round to column precision)
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
}
_NUM_OP_OPTIONS = [
    {"label": "≥", "value": "gte"}, {"label": ">", "value": "gt"},
    {"label": "=", "value": "eq"}, {"label": "<", "value": "lt"},
    {"label": "≤", "value": "lte"},
]
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _num_mask(series, op, val, prec):
    if val is None or val == "" or not op:
        return pd.Series(True, index=series.index)
    try:
        v = float(val)
    except (TypeError, ValueError):
        return pd.Series(True, index=series.index)
    s = pd.to_numeric(series, errors="coerce")
    if op == "eq":
        return (s.round(prec) == round(v, prec)).fillna(False)
    fn = _NUM_OPS.get(op)
    if fn is None:
        return pd.Series(True, index=series.index)
    return fn(s, v).fillna(False)


def _date_mask(series, dfrom, dto):
    if not dfrom and not dto:
        return pd.Series(True, index=series.index)
    s = series.astype(str)  # ISO strings sort chronologically; "—" excluded
    m = s.str.match(_ISO_RE)
    if dfrom:
        m &= s >= str(dfrom)[:10]
    if dto:
        m &= s <= str(dto)[:10]
    return m


def _apply_filters(frame, *, text=None, decisions=None, type_=None,
                   alerted_only=False, risk_op=None, risk_val=None,
                   amt_op=None, amt_val=None, txn_op=None, txn_val=None,
                   first_from=None, first_to=None, last_from=None, last_to=None):
    if frame.empty:
        return frame
    mask = pd.Series(True, index=frame.index)
    if text and str(text).strip():
        t = str(text).strip().lower()
        hay = (frame["entity"].astype(str).str.lower() + " "
               + frame["id"].astype(str).str.lower())
        mask &= hay.str.contains(re.escape(t), na=False)  # case-insensitive
    if decisions:
        mask &= frame["decision"].isin(decisions)
    if type_:
        mask &= frame["type"] == type_
    if alerted_only:
        mask &= frame["alerted"] == "yes"
    mask &= _num_mask(frame["final_risk"], risk_op, risk_val, 3)
    mask &= _num_mask(frame["total_amount"], amt_op, amt_val, 0)
    mask &= _num_mask(frame["txn_count"], txn_op, txn_val, 0)
    mask &= _date_mask(frame["first_seen"], first_from, first_to)
    mask &= _date_mask(frame["last_seen"], last_from, last_to)
    return frame[mask]


_TABLE_COLUMN_LABELS = {
    "entity": "Entity", "id": "ID", "type": "Type", "hop": "Hop",
    "final_risk": "Risk", "decision": "Decision", "alerted": "Alerted",
    "total_amount": "Total amount", "txn_count": "# Txns",
    "first_seen": "First seen", "last_seen": "Last seen",
    "volume_share_%": "% of subject flow", "shared_attrs": "Shared attrs",
    "case_id": "Case",
}
_MONEY_FMT = Format(group=Group.yes, precision=0, scheme=Scheme.fixed)


def _table_columns(frame):
    cols = []
    for c in frame.columns:
        if c == "_node":  # internal graph key for click-to-inspect
            continue
        col = {"name": _TABLE_COLUMN_LABELS.get(c, c), "id": c}
        if c == "total_amount":
            col.update(type="numeric", format=_MONEY_FMT)
        cols.append(col)
    return cols


def _row_node(row: dict):
    """Graph key for a clicked table row (falls back to the display id)."""
    return row.get("_node") or row.get("id")


# Base conditional cell styling (colour by decision / risk band / alert),
# plus the whole-row and active-cell selection tint. The row highlight is
# appended dynamically by _highlight_row on active_cell.
_BASE_CONDITIONAL = [
    {"if": {"filter_query": '{decision} = "SAR"', "column_id": "decision"},
     "color": "var(--risk-red)", "fontWeight": "650"},
    {"if": {"filter_query": '{decision} = "EDD"', "column_id": "decision"},
     "color": "var(--risk-yellow-text)", "fontWeight": "650"},
    # risk cell colours follow the SAME thresholds as the decision bands
    {"if": {"filter_query": "{final_risk} >= %s" % config.DECISION_T2,
            "column_id": "final_risk"},
     "color": "var(--risk-red)", "fontWeight": "650"},
    {"if": {"filter_query": "{final_risk} >= %s && {final_risk} < %s"
            % (config.DECISION_T1, config.DECISION_T2),
            "column_id": "final_risk"},
     "color": "var(--risk-yellow-text)", "fontWeight": "650"},
    {"if": {"filter_query": '{alerted} = "yes"', "column_id": "alerted"},
     "color": "var(--risk-red)"},
    # the clicked cell inherits the row tint (no lone blue cell outline)
    {"if": {"state": "active"},
     "backgroundColor": "var(--row-sel)", "border": "none"},
    {"if": {"state": "selected"},
     "backgroundColor": "var(--row-sel)", "border": "none"},
]


def _row_conditional(active_row):
    """Base styling + a full-row highlight for the selected row."""
    styles = list(_BASE_CONDITIONAL)
    if active_row is not None:
        styles.append({"if": {"row_index": active_row},
                       "backgroundColor": "var(--row-sel)"})
    return styles


def _table_styles():
    return dict(
        style_table={"overflowX": "auto"},
        style_cell={"fontSize": "12px",
                    "fontFamily": "-apple-system, BlinkMacSystemFont, sans-serif",
                    "padding": "7px 10px", "textAlign": "left",
                    "backgroundColor": "transparent", "color": "var(--text)",
                    "border": "none", "borderBottom": "1px solid var(--border)"},
        style_header={"backgroundColor": "transparent", "color": "var(--muted)",
                      "fontWeight": "650", "textTransform": "uppercase",
                      "fontSize": "10.5px", "letterSpacing": "0.05em",
                      "border": "none", "borderBottom": "2px solid var(--border)"},
        style_data_conditional=list(_BASE_CONDITIONAL),
    )


# --------------------------------------------------------- UI fragments
def _decision_panel(case_id, ev):
    """Left band column: the call, the score, and why — the first read."""
    ego = _result(case_id)["ego"]
    subject_own = ego.nodes[ev["subject_id"]].get("decision", ev["decision"])
    order = [config.DECISION_NO_ACTION, config.DECISION_EDD, config.DECISION_SAR]
    escalated = order.index(ev["decision"]) > order.index(subject_own)

    p = ev["calibrated_score"]
    fill = ("var(--risk-red)" if p >= config.DECISION_T2
            else "var(--risk-yellow)" if p >= config.DECISION_T1
            else "var(--ok-green)")
    near_threshold = min(abs(p - config.DECISION_T1), abs(p - config.DECISION_T2)) < 0.02
    near_extreme = p > 0.985 or p < 0.015  # never round to a false 1.00 / 0.00
    # precision grows until the DISPLAYED value sits on the same side of
    # both thresholds as the true score — "0.750 · EDD" next to "≥ 0.75 SAR"
    # reads as a contradiction (the score was 0.7496)
    p_text = "%.4f" % p
    for fmt in (("%.3f",) if near_threshold or near_extreme
                else ("%.2f", "%.3f")):
        cand = fmt % p
        shown = float(cand)
        if ((shown >= config.DECISION_T1) == (p >= config.DECISION_T1)
                and (shown >= config.DECISION_T2) == (p >= config.DECISION_T2)):
            p_text = cand
            break
    calibration = DA.calibration()
    calibrated = bool(calibration.get("calibrated"))
    score_label = "Calibrated risk" if calibrated else "Risk score (uncalibrated)"
    bands_caption = "< %.2f no action · %.2f–%.2f EDD · ≥ %.2f SAR" % (
        config.DECISION_T1, config.DECISION_T1, config.DECISION_T2, config.DECISION_T2)

    return [
        html.Div("Case decision", className="kpi-label"),
        html.Div([html.Span(ev["decision"],
                            className=DECISION_CHIP[ev["decision"]] + " chip-lg")],
                 style={"margin": "4px 0"}),
        html.Div("escalated by network evidence", className="kpi-caption")
        if escalated else html.Span(""),
        html.Div([html.Span(score_label),
                  html.Span(" ⚠", className="warn-badge",
                            title="Calibration fallback: only %d weak positive labels "
                                  "(need %d). Thresholds ride on the raw score."
                                  % (calibration.get("n_pos", 0),
                                     config.MIN_CALIBRATION_POSITIVES))
                  if not calibrated else html.Span("")],
                 className="kpi-label", title=GLOSSARY["risk_score"],
                 style={"marginTop": "10px"}),
        html.Div(p_text, className="kpi-value", style={"fontSize": "22px"}),
        html.Div([
            html.Div(className="score-fill",
                     style={"width": "%d%%" % round(100 * p), "background": fill}),
            html.Div(className="score-tick",
                     style={"left": "%d%%" % round(100 * config.DECISION_T1)},
                     title="t1 = %.2f → EDD" % config.DECISION_T1),
            html.Div(className="score-tick",
                     style={"left": "%d%%" % round(100 * config.DECISION_T2)},
                     title="t2 = %.2f → SAR" % config.DECISION_T2),
        ], className="score-track", style={"width": "160px"}),
        html.Div(bands_caption, className="kpi-caption"),
        html.Div([html.Div("· " + r, className="reason-line")
                  for r in ev["decision_reasons"][:3]],
                 style={"marginTop": "10px"}),
    ]


def _quickstats_panel(ev):
    """Right band column: the secondary facts, one compact grid."""
    window = ev.get("activity_window", {})
    if config.WATCHLIST_CONNECTED:
        watchlist_val = str(len(ev["sanctioned_neighbors"]))
        watchlist_title = None
    else:  # a screening that did not run must never read as a clean 0
        watchlist_val = "not screened"
        watchlist_title = "no watchlist source connected (open Q5)"
    alerted = ev["alerted_neighbors"]
    flags = ", ".join(ev["structural_flags"]) or "—"
    rows = [
        ("Alerted ≤ 2 hops", str(len(alerted)),
         ", ".join(alerted[:15]) + (" …" if len(alerted) > 15 else "")),
        ("Watchlist", watchlist_val, watchlist_title),
        ("Activity window", "%s → %s" % (window.get("first") or "—",
                                         window.get("last") or "—"), None),
        ("Subject flow", _fmt_money(ev.get("subject_total_flow")), None),
        ("Scored network", "%s nodes · %s edges"
         % (format(ev["network_size"]["nodes"], ","),
            format(ev["network_size"]["edges"], ",")),
         "full depth-%s scoring; the canvas shows the drill-down view"
         % ev["network_size"]["depth"]),
        ("Typologies", flags, " · ".join(GLOSSARY.get(f, f)
                                         for f in ev["structural_flags"]) or None),
        ("LOB", ev["lob"] or "—", None),
    ]
    out = [html.Div("Case facts", className="kpi-label",
                    style={"marginBottom": "6px"})]
    for label, value, tip in rows:
        out.append(html.Div([
            html.Div(label, className="stat-k"),
            html.Div(value, className="stat-v", title=tip),
        ], className="stat-row"))
    return out


def _conclusion_content(case_id):
    """The AI conclusion card body: the Copilot-written file, or the recipe."""
    text = read_conclusion(case_id)
    if text:
        return dcc.Markdown(text, className="conclusion-md")
    return html.Div([
        html.Div("No conclusion on file yet.", style={"fontWeight": 600,
                                                      "marginBottom": "4px"}),
        html.Div(["Metrics for this case are exported to ",
                  html.Code("output/case_metrics/case_%s.json" % case_id),
                  ". In VS Code, ask Copilot to follow ",
                  html.Code("skills/case-conclusion/SKILL.md"),
                  " — it writes ",
                  html.Code("output/conclusions/case_%s.md" % case_id),
                  ", then press ↻ Refresh (or paste it below)."]),
    ], className="conclusion-empty")


_PART_LABELS = {
    "base": "Own attributes (KYC, alerts)",
    "rel": "Relationship to subject",
    "prop": "Network proximity (propagated)",
    "struct": "Structural patterns",
}


def _risk_parts_bars(attrs):
    parts = attrs.get("risk_parts", {})
    max_w = max(config.STAGE_E_WEIGHTS.values())
    rows = []
    raw_sum = 0.0
    for part in ("base", "rel", "prop", "struct"):
        w = config.STAGE_E_WEIGHTS[part]
        contrib = w * parts.get(part, 0.0)
        raw_sum += contrib
        rows.append(html.Div([
            html.Div("%s (w=%.2f)" % (_PART_LABELS[part], w), className="bar-label",
                     title=GLOSSARY.get("proximity") if part == "prop" else None),
            html.Div(html.Div(className="bar-fill",
                              style={"width": "%d%%" % round(100 * min(contrib / max_w, 1.0)),
                                     "background": "var(--accent)"}),
                     className="bar-track"),
            html.Div("%.3f" % contrib, className="bar-val"),
        ], className="bar-row"))
    rows.append(html.Div("raw %.3f → calibration → final %.3f"
                         % (raw_sum, attrs.get("final_risk", 0.0)),
                         className="kpi-caption", style={"marginTop": "4px"}))
    return rows


def _node_panel(case_id, node_id):
    ego = _result(case_id)["ego"]
    if node_id not in ego.nodes:
        return html.Div("Click a node or edge (or search above) to inspect it.",
                        style={"color": "var(--muted)", "fontSize": "12px"})
    a = ego.nodes[node_id]
    is_external = a.get("node_type") == NODE_EXTERNAL
    decision = a.get("decision", config.DECISION_NO_ACTION)
    name = a.get("name") or _short(node_id)
    flow = node_flow_summary(ego, node_id)

    status = (html.Span("External — no KYC held", className="chip chip-ext",
                        title="Not a bank customer; scored on network behaviour "
                              "only, and KYC opacity adds a small risk component")
              if is_external else html.Span(decision, className=DECISION_CHIP[decision]))

    props = [
        ("Type", "external counterparty" if is_external else "customer"),
        ("Hop from subject", a.get("hop")),
        ("Country", a.get("country") or "—"),
        ("Country risk", a.get("country_risk") or "—"),
        ("PEP", a.get("pep_flag") or "—"), ("CRR", a.get("crr") or "—"),
        ("TM alerted", "yes" if a.get("alerted") else "no"),
        ("Accounts", len(a.get("accounts", []))),
        ("Flow in this network", "%s · %d txns" % (_fmt_money(flow["total_amount"]),
                                                   flow["txn_count"])),
        ("Active", "%s → %s" % (_fmt_date(flow["first_seen"]),
                                _fmt_date(flow["last_seen"]))),
        ("Risk score", "%.3f" % a.get("final_risk", 0.0)),
    ]
    if a.get("hop") == 1:
        share = a.get("rel_components", {}).get("volume_share")
        if share is not None:
            props.insert(9, ("Share of subject flow", "%.1f%%" % (100 * share)))

    nbrs = set(ego.successors(node_id)) | set(ego.predecessors(node_id))
    nbrs.discard(node_id)  # self-loop rows exist in the extract
    top = sorted(nbrs, key=lambda m: ego.nodes[m].get("final_risk", 0.0),
                 reverse=True)[:config.TOP_COUNTERPARTIES]
    return html.Div([
        html.Div([
            html.Span(name, style={"fontWeight": 650, "fontSize": "14px",
                                   "flex": "1"}),
            status,
        ], style={"display": "flex", "alignItems": "center", "gap": "8px",
                  "marginBottom": "8px"}),
        html.Div(node_id, style={"color": "var(--muted)", "fontSize": "11px",
                                 "marginBottom": "8px"}),
        html.Div([e for k, v in props
                  for e in (html.Div(k, className="k"), html.Div(str(v)))],
                 className="prop-grid"),
        html.H5("Risk decomposition", className="section"),
        html.Div(_risk_parts_bars(a)),
        html.H5("Top counterparties of %s" % name, className="section"),
        html.Div([html.Button([
            html.Span("%d." % (i + 1), className="rank"),
            html.Span(ego.nodes[m].get("name") or _short(m), style={"flex": "1"}),
            html.Span("%.3f" % ego.nodes[m].get("final_risk", 0.0),
                      style={"color": "var(--muted)"}),
            html.Span(ego.nodes[m].get("decision", ""),
                      className=DECISION_CHIP.get(ego.nodes[m].get("decision"), ""),
                      style={"fontSize": "10px", "padding": "1px 8px"}),
        ], id={"type": "cpty-jump", "node": m}, n_clicks=0,
            className="cpty-item cpty-btn") for i, m in enumerate(top)]),
    ])


def _edge_panel(case_id, d):
    """Inspector for a tapped edge — the transaction relationship itself."""
    ego = _result(case_id)["ego"]
    src, dst = _name_of(ego, d.get("source")), _name_of(ego, d.get("target"))
    if d.get("kind") == "txn":
        props = [
            ("Total amount", _fmt_money(d.get("amount"))),
            ("Transactions", d.get("count")),
            ("Active", "%s → %s" % (d.get("first", "—"), d.get("last", "—"))),
            ("Corridor", "%s → %s" % (d.get("src_country") or "?",
                                      d.get("dst_country") or "?")),
        ]
        title = html.Div([html.Span(src), html.Span(" → ", className="arrow"),
                          html.Span(dst)],
                         style={"fontWeight": 650, "fontSize": "13px"})
        note = "Direction follows the money flow. Thickness on the canvas is " \
               "this amount relative to the case's largest flow."
    else:
        props = [("Link type", d.get("kind", "").replace("same_", "shared ")),
                 ("Shared value", d.get("value") or "—")]
        title = html.Div([html.Span(src), html.Span(" ⇔ ", className="arrow"),
                          html.Span(dst)],
                         style={"fontWeight": 650, "fontSize": "13px"})
        note = "Identity link mined from KYC fields — a classic nominee/shell " \
               "signature when combined with high flow."
    return html.Div([
        title,
        html.Div("relationship", style={"color": "var(--muted)", "fontSize": "11px",
                                        "margin": "2px 0 8px"}),
        html.Div([e for k, v in props
                  for e in (html.Div(k, className="k"), html.Div(str(v)))],
                 className="prop-grid"),
        html.Div(note, className="kpi-caption", style={"marginTop": "10px"}),
    ])


def _cluster_panel(case_id, cluster_id):
    """Inspector for a tapped cluster: what the group is, top members."""
    ego = _result(case_id)["ego"]
    seed = ego.graph["seed"]
    if cluster_id == "cl_rest":
        members = [n for n, a in ego.nodes(data=True)
                   if a.get("community_id") is None and n != seed]
        title = "Unclustered entities"
    else:
        cid = cluster_id.replace("cl_", "")
        members = [n for n, a in ego.nodes(data=True)
                   if str(a.get("community_id")) == cid and n != seed]
        title = "Cluster %s" % cid
    total_flow = sum(node_flow_summary(ego, m)["total_amount"]
                     for m in members[:400])  # bounded cost on huge buckets
    alerted_k = sum(1 for m in members if ego.nodes[m].get("alerted"))
    top = sorted(members, key=lambda m: ego.nodes[m].get("final_risk", 0.0),
                 reverse=True)[:config.TOP_COUNTERPARTIES]
    return html.Div([
        html.Div([html.Span(title, style={"fontWeight": 650, "fontSize": "14px",
                                          "flex": "1"}),
                  html.Span("%d members" % len(members), className="user-chip")],
                 style={"display": "flex", "gap": "8px", "alignItems": "center",
                        "marginBottom": "8px"}),
        html.Div([e for k, v in (
            ("TM-alerted members", alerted_k),
            ("Flow (top members)", _fmt_money(total_flow)),
        ) for e in (html.Div(k, className="k"), html.Div(str(v)))],
            className="prop-grid"),
        html.H5("Top members — click to open in entity view", className="section"),
        html.Div([html.Button([
            html.Span("%d." % (i + 1), className="rank"),
            html.Span(ego.nodes[m].get("name") or _display_id(m, ego.nodes[m]),
                      style={"flex": "1"}),
            html.Span("%.3f" % ego.nodes[m].get("final_risk", 0.0),
                      style={"color": "var(--muted)"}),
            html.Span(ego.nodes[m].get("decision", ""),
                      className=DECISION_CHIP.get(ego.nodes[m].get("decision"), ""),
                      style={"fontSize": "10px", "padding": "1px 8px"}),
        ], id={"type": "cluster-jump", "node": m}, n_clicks=0,
            className="cpty-item cpty-btn") for i, m in enumerate(top)]),
    ])


def _drivers_card(ev):
    drivers = ev["top_drivers"]
    max_mag = max((d["magnitude"] for d in drivers), default=1.0) or 1.0
    # contributions in the additive scorer are non-negative by construction
    rows = [html.Div([
        html.Div(d["feature"], className="bar-label", title=d["feature"]),
        html.Div(html.Div(className="bar-fill",
                          style={"width": "%d%%" % round(100 * d["magnitude"] / max_mag),
                                 "background": "var(--risk-red)"}),
                 className="bar-track"),
        html.Div("%.3f" % d["magnitude"], className="bar-val"),
    ], className="bar-row") for d in drivers]
    return [html.H4("Why — top risk drivers", className="section"),
            html.Div(rows)]


def _paths_card(ev):
    items = []
    for i, p in enumerate(ev["key_paths"]):
        segs = []
        for j, label in enumerate(p["path"]):
            if j:
                segs.append(html.Span("→", className="arrow"))
            segs.append(html.Span(label))
        items.append(html.Div(
            [html.Span("%d. " % (i + 1), style={"color": "var(--muted)"})] + segs +
            [html.Span("  (prop %.2f)" % p["prop_risk"],
                       style={"color": "var(--muted)"},
                       title=GLOSSARY["proximity"])], className="path-item"))
    if not items:
        items = [html.Div("No propagated-risk paths in this neighbourhood.",
                          style={"color": "var(--muted)", "fontSize": "12px"})]
    shared = [html.Div("%s — %s (%s)"
                       % (l["counterparty"], l["kind"].replace("same_", "shared "),
                          l["value"]), className="path-item")
              for l in ev["shared_attribute_links"]]
    return [html.H4("Key risk paths", className="section"), html.Div(items),
            html.H5("Shared-attribute links to subject", className="section"),
            html.Div(shared or [html.Div("None found.",
                                         style={"color": "var(--muted)",
                                                "fontSize": "12px"})])]


def _graph_caption(stats, ego):
    if stats.get("mode") == "clusters":
        parts = [html.Span("broad view: %d clusters + subject · %d aggregated flows"
                           % (stats["clusters"], stats["edges_shown"])),
                 html.Span("click a cluster to list its members",
                           className="caption-note")]
        if stats.get("unclustered"):
            parts.append(html.Span("%s entities without a community sit in "
                                   "'unclustered'" % format(stats["unclustered"], ",")))
        return parts

    if stats.get("isolated"):
        # the lens dims, it never deletes — the caption says exactly that
        return [html.Span(
            "expansion lens ON: %d trail entities glowing · %d faded to "
            "ghosts — nothing is removed, untick to restore"
            % (stats["lens_trail"], stats["lens_faded"]),
            className="caption-note",
            title="Trail = subject, drilled parents and revealed children — "
                  "full colour with an accent glow. Edges that connect the "
                  "trail back to the faded context stay as faint whispers.")]
    parts = [html.Span(
        "top %d of %s direct counterparties by risk"
        % (stats["hop1_shown"], format(stats["hop1_total"], ","))
        + (" · %d expanded" % stats["expanded_shown"]
           if stats["expanded_shown"] else ""),
        title="Double-click any node (or use the Expand button) to reveal "
              "its own top counterparties — down to 3 hops.")]
    if stats["alerted_offscreen"]:
        parts.append(html.Span(
            "%s alerted entities not in view" % format(stats["alerted_offscreen"], ","),
            className="caption-warn",
            title="They are still scored and in the table — raise Show top, "
                  "expand nodes, or work the table to reach them."))
    if stats["hidden_flagged"]:
        parts.append(html.Span("%d flagged entities hidden by min-risk"
                               % stats["hidden_flagged"], className="caption-warn"))
    if stats.get("alert_exempt"):
        parts.append(html.Span(
            "%d alerted kept despite min-risk %.2f"
            % (stats["alert_exempt"], stats.get("min_risk", 0.0)),
            className="caption-note",
            title="TM-alerted entities are never filtered out — raising "
                  "min-risk cannot hide an alert. On an alert-heavy case "
                  "the picture may therefore not change at all."))
    if stats["path_revealed"]:
        parts.append(html.Span("key path revealed %d extra node%s"
                               % (stats["path_revealed"],
                                  "" if stats["path_revealed"] == 1 else "s"),
                               className="caption-note"))
    if stats.get("render_capped"):
        parts.append(html.Span("canvas capped: %s not drawn"
                               % format(stats["render_capped"], ","),
                               className="caption-warn"))
    # bounded-expansion disclosure on hub-scale graphs (never silent)
    trunc = ego.graph.get("truncation")
    if trunc and trunc.get("neighbours_skipped"):
        parts.append(html.Span(
            "scored network bounded: %s nodes kept, %s further counterparties "
            "not expanded" % (format(trunc["nodes_scored"], ","),
                              format(trunc["neighbours_skipped"], ",")),
            className="caption-note",
            title="%s. All TM-alerted, PEP and high-CRR neighbours are always "
                  "retained; the skipped remainder are low-flow counterparties. "
                  "Recorded in the evidence pack (governance.scoring_scope)."
                  % trunc["strategy"]))
    return parts


def _isolate_options(enabled, in_clusters=False):
    """The expansion lens is meaningless without an expansion (and in the
    clusters view) — say so on the control instead of silently no-op'ing."""
    if in_clusters:
        return [{"label": " highlight expansions", "value": "on",
                 "disabled": True,
                 "title": "lenses apply to the entities view — switch back "
                          "to entities to use this"}]
    return [{"label": " highlight expansions", "value": "on",
             "disabled": not enabled,
             "title": ("keep the whole graph but light the expansion trail: "
                       "subject → drilled parents → children glow at full "
                       "colour, everything else fades to small ghosts"
                       if enabled else
                       "expand a node first (double-click one, or use the "
                       "Expand button) — this lens highlights expansions")}]


LEGEND = html.Div([
    html.Span([html.Span(className="dot",
                         style={"background": "var(--risk-red)"}), "SAR"]),
    html.Span([html.Span(className="dot",
                         style={"background": "var(--risk-yellow)"}), "EDD"]),
    html.Span([html.Span(className="dot",
                         style={"background": "var(--muted)"}), "No action"]),
    html.Span([html.Span(className="dot",
                         style={"background": "transparent",
                                "border": "2px solid var(--risk-red)"}),
               "TM-alerted"]),
    html.Span("◆ case subject"),
    html.Span([html.Span(className="dot dot-ext"), "external (no KYC)"]),
    html.Span([html.Span(className="line"), "transaction (→ money flow)"]),
    html.Span([html.Span(className="line dashed"), "identity link"]),
    html.Span([html.Span(className="line line-accent"), "expansion trail"],
              title="Drawn from a drilled node to the counterparties it revealed"),
    html.Span("size = risk · deeper hops draw smaller",
              title="Node size is the entity's risk score within its hop; "
                    "hop-2/3 nodes are drawn smaller so the drill hierarchy "
                    "stays readable. Edge thickness is the flow amount."),
], className="legend")


# ------------------------------------------------------------------- app
# suppress_callback_exceptions: the login view and the main screen are
# rendered alternately into #page, so at any moment half the callback
# targets are legitimately absent from the layout.
app = dash.Dash(__name__, title="Network Intelligence — Counterparty Risk",
                suppress_callback_exceptions=True)

# labels come from lightweight case metadata — building them from evidence
# would force-score all six networks at import
_case_options = [
    {"label": "Case %d — %s (%s)" % (c, DA.case_meta(c)["name"],
                                     DA.case_meta(c)["lob"] or "?"),
     "value": c} for c in CASES]


def _op_dd(id_):
    return dcc.Dropdown(id=id_, options=_NUM_OP_OPTIONS, value="gte",
                        clearable=False, className="flt-op")


def _filter_bar():
    """Custom filter bar (replaces Dash's native per-column filter row).

    Always-visible: case-insensitive text search + decision/type/alerted +
    Clear. Behind 'More filters': numeric operator+value for risk/amount/
    txns, and range pickers for the first/last-seen dates (leave one side
    blank for a one-sided before/after)."""
    return html.Div([
        html.Div([
            dcc.Input(id="flt-text", type="text", debounce=False,
                      placeholder="Filter by entity or ID…", className="flt-text"),
            dcc.Dropdown(id="flt-decision", multi=True, placeholder="Decision",
                         options=[{"label": d, "value": d} for d in
                                  (config.DECISION_NO_ACTION, config.DECISION_EDD,
                                   config.DECISION_SAR)], className="flt-dd"),
            dcc.Dropdown(id="flt-type", placeholder="Type",
                         options=[{"label": "customer", "value": "customer"},
                                  {"label": "external", "value": "external"}],
                         className="flt-dd"),
            dcc.Checklist(id="flt-alerted",
                          options=[{"label": " alerted only", "value": "on"}],
                          value=[], className="flt-chk"),
            html.Button("Clear filters", id="flt-clear", className="btn btn-xs",
                        n_clicks=0),
        ], className="flt-row"),
        html.Details([
            html.Summary("More filters — risk, amounts, dates"),
            html.Div([
                html.Span("Risk", className="flt-lbl"), _op_dd("flt-risk-op"),
                dcc.Input(id="flt-risk-val", type="number", step=0.05,
                          min=0, max=1, placeholder="0–1", className="flt-num"),
                html.Span("Amount", className="flt-lbl"), _op_dd("flt-amt-op"),
                dcc.Input(id="flt-amt-val", type="number",
                          placeholder="base ccy", className="flt-num"),
                html.Span("# Txns", className="flt-lbl"), _op_dd("flt-txn-op"),
                dcc.Input(id="flt-txn-val", type="number", placeholder="count",
                          className="flt-num"),
            ], className="flt-row", style={"paddingTop": "8px"}),
            html.Div([
                html.Span("First seen", className="flt-lbl"),
                dcc.DatePickerRange(id="flt-first", display_format="YYYY-MM-DD",
                                    clearable=True, className="flt-date",
                                    minimum_nights=0,
                                    start_date_placeholder_text="after",
                                    end_date_placeholder_text="before"),
                html.Span("Last seen", className="flt-lbl"),
                dcc.DatePickerRange(id="flt-last", display_format="YYYY-MM-DD",
                                    clearable=True, className="flt-date",
                                    minimum_nights=0,
                                    start_date_placeholder_text="after",
                                    end_date_placeholder_text="before"),
            ], className="flt-row", style={"paddingTop": "4px"}),
        ], className="adv"),
    ], className="filter-bar")


def _login_view():
    return html.Div(html.Div([
        html.Div("◆", className="login-mark"),
        html.H2("Counterparty Network Risk", style={"margin": "0 0 2px",
                                                    "fontSize": "18px"}),
        html.Div("Sign in to open the analyst workspace",
                 style={"color": "var(--muted)", "fontSize": "12.5px",
                        "marginBottom": "18px"}),
        dcc.Input(id="login-user", placeholder="Username", type="text",
                  className="login-input", autoFocus=True,
                  autoComplete="username"),
        dcc.Input(id="login-pass", placeholder="Password", type="password",
                  className="login-input", n_submit=0,
                  autoComplete="current-password"),
        html.Button("Sign in", id="login-btn", n_clicks=0,
                    className="btn btn-primary login-btn"),
        html.Div(id="login-error", className="login-error"),
        # only rendered while the demo fallback is the sole credential
        # source — a teammate must never face a password box with no password
        html.Div("demo access — analyst / riskdemo", className="login-hint")
        if demo_active() else None,
    ], className="login-card card"), className="login-wrap")


def _main_layout(user):
    return html.Div([
        html.Div([
            html.H2(["Counterparty Network Risk",
                     html.Span("staged scorecard · %s propagation"
                               % config.PROP_METHOD.upper(), className="sub")]),
            dcc.Dropdown(id="case", options=_case_options, value=CASES[0],
                         clearable=False, style={"width": "340px"}),
            html.Span("◈ %s" % user, className="user-chip"),
            html.Button("Sign out", id="logout-btn", className="btn", n_clicks=0),
            html.Button("☾ / ☀", id="theme-btn", className="btn", n_clicks=0),
        ], className="header"),
        # ---- case summary band: decision + score | AI conclusion | stats --
        html.Div([
            html.Div(id="decision-panel", className="band-col band-decision"),
            html.Div([
                html.Div([html.H4("AI conclusion", className="section",
                                  style={"flex": "1", "margin": 0}),
                          html.Button("↻ Refresh", id="refresh-conclusion-btn",
                                      className="btn btn-xs", n_clicks=0)],
                         style={"display": "flex", "alignItems": "center",
                                "marginBottom": "6px"}),
                html.Div(id="conclusion-content", className="conclusion-body"),
                html.Details([
                    html.Summary("Edit / paste conclusion"),
                    dcc.Textarea(id="conclusion-edit", className="conclusion-edit"),
                    html.Button("Save to case file", id="save-conclusion-btn",
                                className="btn btn-xs", n_clicks=0,
                                style={"marginTop": "6px"}),
                ], className="adv"),
            ], className="band-col band-conclusion"),
            html.Div(id="quickstats-panel", className="band-col band-stats"),
        ], className="card summary-band"),
        html.Div([
            html.Div([
                html.Div([
                    html.Div([
                        html.Div("view", className="ctl-group-label"),
                        dcc.RadioItems(id="view-mode",
                                       options=[{"label": " entities",
                                                 "value": "entities"},
                                                {"label": " clusters",
                                                 "value": "clusters"}],
                                       value="entities", inline=True),
                        html.Div([html.Div("Show top", className="ctl-label"),
                                  dcc.Dropdown(id="top-n",
                                               options=[{"label": str(n), "value": n}
                                                        for n in config.TOP_N_OPTIONS],
                                               value=config.TOP_N_DEFAULT,
                                               clearable=False,
                                               style={"width": "80px"})]),
                        html.Div([html.Div("Min risk", className="ctl-label"),
                                  dcc.Slider(id="min-risk", min=0, max=1,
                                             step=0.05, value=0.0,
                                             updatemode="drag",
                                             tooltip={"placement": "bottom"},
                                             marks={0: "0", 0.5: "0.5", 1: "1"})],
                                 style={"width": "150px"}),
                    ], className="ctl-group"),
                    html.Div([
                        html.Div("lenses", className="ctl-group-label"),
                        dcc.Checklist(id="highlight-path",
                                      options=[{"label": " key risk path",
                                                "value": "on"}],
                                      value=[], inline=True),
                        dcc.Checklist(id="isolate-exp",
                                      options=_isolate_options(False), value=[],
                                      inline=True, className="ctl-tip"),
                    ], className="ctl-group"),
                    html.Div([
                        html.Div("actions", className="ctl-group-label"),
                        html.Button("◎ Center subject", id="center-btn",
                                    className="btn", n_clicks=0),
                        html.Button("Reset view", id="reset-view-btn",
                                    className="btn", n_clicks=0,
                                    title="Back to the default view AND the "
                                          "canonical layout — the rescue "
                                          "hatch if the graph gets messy"),
                        html.Button("Expand next hop", id="expand-btn",
                                    className="btn btn-expand", n_clicks=0,
                                    title="Reveal the focused node's top "
                                          "counterparties (same as "
                                          "double-click)"),
                        html.Button("Reset expansion", id="reset-expand-btn",
                                    className="btn", n_clicks=0),
                    ], className="ctl-group ctl-actions"),
                    html.Details([
                        html.Summary("Advanced"),
                        html.Div([
                            dcc.Checklist(id="edge-kinds",
                                          options=[{"label": " transactions",
                                                    "value": "txn"},
                                                   {"label": " identity links",
                                                    "value": "identity"}],
                                          value=["txn", "identity"], inline=True),
                            dcc.RadioItems(id="layout-mode",
                                           options=[{"label": " live physics",
                                                     "value": "live"},
                                                    {"label": " force",
                                                     "value": "force"},
                                                    {"label": " rings by hop",
                                                     "value": "rings"}],
                                           value="live", inline=True),
                        ], className="controls", style={"paddingTop": "8px"}),
                    ], className="adv"),
                ], className="controls"),
                dcc.Loading(
                    cyto.Cytoscape(id="graph", layout=_layout_spec("force", ""),
                                   # taller screens get a taller canvas — the
                                   # graph is the workspace's centrepiece
                                   style={"width": "100%",
                                          "height": "clamp(460px, 56vh, 760px)"},
                                   elements=[], stylesheet=_stylesheet("light"),
                                   responsive=True,
                                   # a degenerate fit (all nodes stacked) must
                                   # never be able to wedge the canvas at an
                                   # unusable zoom
                                   minZoom=0.03, maxZoom=4),
                    # first open of a case scores its whole network — up to
                    # ~1 min at full scale. Never leave that wait silent.
                    # delay_show keeps quick re-renders (sliders) flicker-free
                    delay_show=600, delay_hide=150,
                    overlay_style={"visibility": "visible", "opacity": 0.45},
                    custom_spinner=html.Div([
                        html.Div(className="spin-ring"),
                        html.Div("Scoring the network…", className="spin-title"),
                        html.Div("first open of a case builds and scores its "
                                 "full counterparty network — up to a minute "
                                 "at full scale; later opens are instant",
                                 className="spin-sub"),
                    ], className="spinner-card"),
                ),
                html.Div(id="graph-caption", className="graph-caption"),
                LEGEND,
            ], className="card card-graph"),
            html.Div([
                html.Div([
                    dcc.Dropdown(id="node-search", placeholder="Search entity…",
                                 style={"flex": "1"}),
                ], style={"display": "flex", "gap": "8px", "marginBottom": "10px",
                          "alignItems": "center"}),
                html.Div(id="side-panel", style={"overflowY": "auto",
                                                 "maxHeight": "560px"}),
            ], className="card card-side"),
        ], className="main-row"),
        html.Div([
            html.Div(id="drivers-card", className="card"),
            html.Div([
                html.Div(id="paths-card-content"),
                # NOTE: these buttons must stay OUTSIDE any callback-rendered
                # children — re-created Inputs re-fire the download callback,
                # which used to save a JSON on every slider move.
                html.Div([
                    html.Button("Download evidence pack (.json)",
                                id="dl-evidence-btn", className="btn", n_clicks=0),
                    html.Button("Download conclusion prompt (.md)",
                                id="dl-prompt-btn", className="btn", n_clicks=0),
                ], style={"display": "flex", "gap": "8px", "marginTop": "12px"}),
            ], className="card"),
        ], className="cards-row", style={"marginTop": "12px"}),
        html.Div([
            html.H4("Counterparties", className="section",
                    style={"marginBottom": "2px"}),
            html.Div("ranked by risk — click a row to inspect",
                     className="sub-note"),
            _filter_bar(),
            html.Div(id="table-note", className="kpi-caption"),
            dash_table.DataTable(id="cpty-table", sort_action="native",
                                 page_size=12, cell_selectable=True,
                                 export_format="csv", **_table_styles()),
        ], className="card", style={"marginTop": "12px"}),
        dcc.Store(id="theme-store", data="light"),
        dcc.Store(id="inspect-store"),
        dcc.Store(id="expanded-store", data=[]),
        dcc.Store(id="view-sig"),
        dcc.Store(id="center-op"),
        dcc.Store(id="fit-op"),
        dcc.Store(id="focus-op"),
        dcc.Store(id="dbltap-store"),
        dcc.Store(id="place-nonce", data=0),
        dcc.Store(id="conclusion-refresh", data=0),
        dcc.Download(id="download"),
    ])


# #root (theme scope) always exists; #page swaps between login and workspace.
# auth-store uses session storage: the login survives a browser refresh but
# not closing the tab — good enough for the placeholder gate.
app.layout = html.Div(id="root", className="theme-light", children=[
    dcc.Store(id="auth-store", storage_type="session"),
    html.Div(id="page"),
])


@app.callback(Output("page", "children"), Input("auth-store", "data"))
def _route(auth):
    if auth and auth.get("user"):
        return _main_layout(auth["user"])
    return _login_view()


@app.callback(
    Output("auth-store", "data"), Output("login-error", "children"),
    Input("login-btn", "n_clicks"), Input("login-pass", "n_submit"),
    State("login-user", "value"), State("login-pass", "value"),
    prevent_initial_call=True,
)
def _login(n_clicks, n_submit, user, password):
    ctx = dash.callback_context
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate  # component (re)creation, not a real attempt
    if verify_credentials(user, password):
        return {"user": str(user).strip()}, ""
    return dash.no_update, "Invalid username or password."


@app.callback(
    Output("auth-store", "data", allow_duplicate=True),
    Input("logout-btn", "n_clicks"), prevent_initial_call=True,
)
def _logout(n):
    ctx = dash.callback_context
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    return None


# Find the live cytoscape instance — cytoscape.js registers itself (_cyreg)
# on the container div, which is the #graph element itself; fall back to a
# descendant scan in case dash-cytoscape ever changes its DOM structure.
_JS_FIND_CY = """
    let cy = null;
    const g = document.getElementById('graph');
    if (g && g._cyreg && g._cyreg.cy) { cy = g._cyreg.cy; }
    if (!cy && g) {
        g.querySelectorAll('div').forEach(function (d) {
            if (d._cyreg && d._cyreg.cy) { cy = d._cyreg.cy; }
        });
    }
"""

# "Center subject": pan/zoom onto the seed diamond and pulse-select it.
app.clientside_callback(
    """
    function(n) {
        if (!n) { return window.dash_clientside.no_update; }
        %s
        if (cy) {
            const seed = cy.nodes('[?is_seed]');
            if (seed.length) {
                cy.elements().unselect();
                seed.select();
                cy.animate({center: {eles: seed}, zoom: 1.8},
                           {duration: 450, easing: 'ease-in-out'});
            }
        }
        return window.dash_clientside.no_update;
    }
    """ % _JS_FIND_CY,
    Output("center-op", "data"), Input("center-btn", "n_clicks"),
)

# Fit ONLY when the view meaningfully changes (case/filters/layout/expansion),
# never on focus taps or theme flips — the analyst's camera position is
# investigative state and must not be stolen. Also: deterministic placement
# of newly-arrived nodes (near their drill parent, else the server hint)
# and the continuous Obsidian-style force simulation for "live" mode —
# springs + repulsion + overlap push acting on CURRENT positions, so the
# graph relaxes smoothly and NEVER reshuffles or randomizes.
app.clientside_callback(
    """
    function(sig, nonce) {
        setTimeout(function () {
            %s
            if (!cy) { return; }

            const liveMode = function () {
                // both views share the motion model — cluster hexagons carry
                // ring hints and ride the same simulation as entity nodes
                const m = document.querySelector('#layout-mode input:checked');
                return m && m.value === 'live';
            };

            // A case switch is a NEW investigation: shared counterparties
            // must not drag positions from the previous subject's frame in.
            let caseId = null;
            try { caseId = JSON.parse(sig)[0]; } catch (err) {}
            if (cy.scratch('_lastCase') !== caseId) {
                cy.scratch('_lastCase', caseId);
                cy.scratch('_placed', {});
                if (cy.__sim) { cy.__sim.vel = {}; }
            }

            // Reset view bumps the nonce: forget every remembered position
            // and velocity so the canonical bloom rebuilds from the hints.
            if (cy.scratch('_lastNonce') !== nonce) {
                cy.scratch('_lastNonce', nonce);
                if (nonce) {
                    cy.scratch('_placed', {});
                    if (cy.__sim) { cy.__sim.vel = {}; }
                }
            }

            // ---- place nodes the client hasn't seen yet -----------------
            // LIVE mode only: in force/rings/cluster modes the cytoscape
            // layout owns positions — placing hints there fights it.
            let newNodes = 0;
            if (liveMode()) {
                const placed = cy.scratch('_placed') || {};
                cy.nodes().forEach(function (n) {
                    if (placed[n.id()]) { return; }
                    let p = null;
                    const ph = n.data('phint');
                    if (ph) {  // bloom out of the parent's LIVE position
                        const par = cy.getElementById(ph);
                        if (par.length && placed[par.id()]) {
                            const pp = par.position();
                            const seed = cy.nodes('[?is_seed]');
                            const sp = seed.length ? seed.position() : {x: 0, y: 0};
                            const base = Math.atan2(pp.y - sp.y, pp.x - sp.x) || 0;
                            const k = n.data('kidx') || 0;
                            const ang = base + ((k %% 7) - 3) * 0.33;
                            const r = 130 + 40 * Math.floor(k / 7);
                            p = {x: pp.x + r * Math.cos(ang),
                                 y: pp.y + r * Math.sin(ang)};
                        }
                    }
                    if (!p && typeof n.data('x0') === 'number') {
                        p = {x: n.data('x0'), y: n.data('y0')};
                    }
                    if (p) { n.position(p); }
                    placed[n.id()] = true;
                    newNodes++;
                });
                cy.scratch('_placed', placed);
            }

            // a runaway position must never survive into a fit or a tick
            cy.nodes().forEach(function (n) {
                const p = n.position();
                if (!isFinite(p.x) || !isFinite(p.y)) {
                    n.position({
                        x: typeof n.data('x0') === 'number' ? n.data('x0') : 0,
                        y: typeof n.data('y0') === 'number' ? n.data('y0') : 0});
                    if (cy.__sim) { delete cy.__sim.vel[n.id()]; }
                }
            });

            if (cy.nodes().length) { cy.resize(); cy.fit(undefined, 40); }

            // ---- continuous force simulation (live mode only) -----------
            if (!cy.__sim) {
                const sim = {alpha: 0, running: false, vel: {}};
                cy.__sim = sim;
                const liveOn = function () {
                    return liveMode()
                        && cy.nodes().length > 1 && cy.nodes().length <= 500;
                };
                const tick = function () {
                    if (!sim.running) { return; }
                    if (!liveOn() || sim.alpha < 0.004) {
                        sim.running = false; return;
                    }
                    const nodes = cy.nodes().toArray();
                    const pos = {}, force = {};
                    nodes.forEach(function (n) {
                        pos[n.id()] = n.position();
                        force[n.id()] = {x: 0, y: 0};
                    });
                    // pairwise repulsion + hard overlap push
                    for (let i = 0; i < nodes.length; i++) {
                        for (let j = i + 1; j < nodes.length; j++) {
                            const a = nodes[i], b = nodes[j];
                            const pa = pos[a.id()], pb = pos[b.id()];
                            let dx = pa.x - pb.x, dy = pa.y - pb.y;
                            let d2 = dx * dx + dy * dy;
                            if (d2 < 0.01) { dx = 0.1 * (i - j); dy = 0.1; d2 = dx * dx + dy * dy; }
                            const d = Math.sqrt(d2);
                            // stacked pairs (two expansions can fan children
                            // onto the same spot) get a GENTLE constant push:
                            // 1/d^2 at d -> 0 was a catapult that flung nodes
                            // thousands of px off-canvas
                            let rep = d < 14 ? 90 : 5200 / d2;
                            const minD = a.width() / 2 + b.width() / 2 + 16;
                            if (d < minD) { rep += (minD - d) * 2.2 / d; }
                            const fx = dx / d * rep, fy = dy / d * rep;
                            force[a.id()].x += fx; force[a.id()].y += fy;
                            force[b.id()].x -= fx; force[b.id()].y -= fy;
                        }
                    }
                    // springs along edges — hexagons are big, so edges that
                    // touch a cluster rest longer than entity edges
                    cy.edges().forEach(function (e) {
                        const sn = e.source(), tn = e.target();
                        const s = sn.id(), t = tn.id();
                        if (!pos[s] || !pos[t] || s === t) { return; }
                        let dx = pos[t].x - pos[s].x, dy = pos[t].y - pos[s].y;
                        const d = Math.sqrt(dx * dx + dy * dy) || 1;
                        const rest = (sn.data('is_cluster') || tn.data('is_cluster'))
                            ? 185 : 95;
                        const F = 0.05 * (d - rest);
                        const fx = dx / d * F, fy = dy / d * F;
                        force[s].x += fx; force[s].y += fy;
                        force[t].x -= fx; force[t].y -= fy;
                    });
                    // weak gravity toward the centroid (keeps it together,
                    // lets the whole graph follow a dragged subject)
                    let cx = 0, cy_ = 0;
                    nodes.forEach(function (n) { cx += pos[n.id()].x; cy_ += pos[n.id()].y; });
                    cx /= nodes.length; cy_ /= nodes.length;
                    cy.startBatch();
                    nodes.forEach(function (n) {
                        if (n.grabbed()) { sim.vel[n.id()] = {x: 0, y: 0}; return; }
                        const f = force[n.id()];
                        f.x += (cx - pos[n.id()].x) * 0.002;
                        f.y += (cy_ - pos[n.id()].y) * 0.002;
                        const v = sim.vel[n.id()] || {x: 0, y: 0};
                        v.x = (v.x + f.x * sim.alpha * 0.02) * 0.86;
                        v.y = (v.y + f.y * sim.alpha * 0.02) * 0.86;
                        if (!isFinite(v.x) || !isFinite(v.y)) { v.x = 0; v.y = 0; }
                        // bounded step: whatever the forces say, nothing is
                        // ever catapulted across the canvas in one tick
                        const sp = Math.hypot(v.x, v.y);
                        if (sp > 36) { v.x *= 36 / sp; v.y *= 36 / sp; }
                        sim.vel[n.id()] = v;
                        const nx = pos[n.id()].x + v.x, ny = pos[n.id()].y + v.y;
                        if (isFinite(nx) && isFinite(ny)) { n.position({x: nx, y: ny}); }
                    });
                    cy.endBatch();
                    sim.alpha *= 0.99;
                    requestAnimationFrame(tick);
                };
                sim.kick = function (a) {
                    if (!liveOn()) { return; }
                    sim.alpha = Math.max(sim.alpha, a);
                    if (!sim.running) { sim.running = true; requestAnimationFrame(tick); }
                };
                // dragging keeps the sim warm so neighbours follow live
                cy.on('grab', 'node', function () { sim.kick(0.5); });
                cy.on('drag', 'node', function () { sim.kick(0.35); });
                cy.on('free', 'node', function () { sim.kick(0.3); });
            }
            if (newNodes) { cy.__sim.kick(0.9); } else { cy.__sim.kick(0.5); }

            // ---- one-time interaction handlers ---------------------------
            if (!cy.__handlersAttached) {
                cy.__handlersAttached = true;
                // a node that leaves the graph must lose its remembered
                // placement + velocity: if it ever comes back (mode
                // round-trip, filter change) it gets a FRESH deterministic
                // position instead of exploding out of a stale (0,0) pile
                cy.on('remove', 'node', function (e) {
                    const pl = cy.scratch('_placed');
                    if (pl) { delete pl[e.target.id()]; }
                    if (cy.__sim) { delete cy.__sim.vel[e.target.id()]; }
                });
                // drill-down: double-click a node to reveal ITS top
                // counterparties (next hop); clusters don't expand
                cy.on('dbltap', 'node', function (e) {
                    if (e.target.data('is_cluster')) { return; }
                    window.dash_clientside.set_props('dbltap-store',
                        {data: {id: e.target.id(), ts: Date.now()}});
                });
                // tap empty canvas: back to the subject (clears focus dim)
                cy.on('tap', function (e) {
                    if (e.target === cy) {
                        const seed = cy.nodes('[?is_seed]');
                        if (seed.length) {
                            window.dash_clientside.set_props('inspect-store',
                                {data: {t: 'node', id: seed.id()}});
                        }
                    }
                });
            }
        }, 400);
        return window.dash_clientside.no_update;
    }
    """ % _JS_FIND_CY,
    Output("fit-op", "data"), Input("view-sig", "data"),
    State("place-nonce", "data"),
)

# Focus ring + focus dimming, all clientside (camera stays put, no server
# round-trip). Focusing a non-subject node fades everything outside
# subject -> chain -> focus -> its neighbours; tap empty canvas to clear.
app.clientside_callback(
    """
    function(inspect, sig) {
        setTimeout(function () {
            %s
            if (!cy) { return; }
            cy.nodes('.focused').removeClass('focused');
            cy.elements().removeClass('dimmed');
            if (!inspect || inspect.t !== 'node' || !inspect.id) { return; }
            const f = cy.getElementById(inspect.id);
            if (f.empty()) { return; }
            f.addClass('focused');
            if (f.data('is_seed')) { return; }
            const seed = cy.nodes('[?is_seed]');
            let lit = f.closedNeighborhood().union(seed);
            if (seed.length) {
                const path = cy.elements().aStar(
                    {root: seed, goal: f, directed: false});
                if (path.found) { lit = lit.union(path.path); }
            }
            cy.elements().difference(lit).addClass('dimmed');
        }, 250);
        return window.dash_clientside.no_update;
    }
    """ % _JS_FIND_CY,
    Output("focus-op", "data"),
    Input("inspect-store", "data"), Input("view-sig", "data"),
)


@app.callback(Output("theme-store", "data"), Input("theme-btn", "n_clicks"))
def _toggle_theme(n):
    return "dark" if (n or 0) % 2 else "light"


@app.callback(
    Output("inspect-store", "data"),
    Input("graph", "tapNodeData"), Input("graph", "tapEdgeData"),
    Input("node-search", "value"), Input("case", "value"),
    Input("center-btn", "n_clicks"),
    Input("cpty-table", "active_cell"),
    Input({"type": "cpty-jump", "node": ALL}, "n_clicks"),
    Input({"type": "cluster-jump", "node": ALL}, "n_clicks"),
    State("cpty-table", "derived_viewport_data"),
)
def _inspect(tap_node, tap_edge, search, case_id, center_n, active_cell,
             jump_clicks, cluster_jump_clicks, viewport_rows):
    """Single writer for what the side panel inspects. Every input except
    the case switch is guarded against fire-on-recreation (the download-bug
    lesson: re-created Input components re-fire their callbacks)."""
    ctx = dash.callback_context
    trigger = ctx.triggered_id
    value = ctx.triggered[0]["value"] if ctx.triggered else None
    subject = {"t": "node", "id": DA.case_meta(case_id)["masked"]}

    if isinstance(trigger, dict) and trigger.get("type") in ("cpty-jump",
                                                             "cluster-jump"):
        if not value:
            raise PreventUpdate
        return {"t": "node", "id": trigger["node"]}
    if trigger == "cpty-table":
        if not value or not viewport_rows:
            raise PreventUpdate
        row = value.get("row")
        if row is None or row >= len(viewport_rows):
            raise PreventUpdate
        return {"t": "node", "id": _row_node(viewport_rows[row])}
    if trigger == "center-btn":
        if not value:
            raise PreventUpdate
        return subject
    if trigger == "graph" and ctx.triggered[0]["prop_id"].endswith("tapEdgeData"):
        if not value:
            raise PreventUpdate
        return {"t": "edge", "d": value}
    if trigger == "graph":
        if not value:
            raise PreventUpdate
        if value.get("is_cluster"):
            return {"t": "cluster", "id": value["id"]}
        return {"t": "node", "id": value["id"]}
    if trigger == "node-search":
        if not value:
            raise PreventUpdate
        return {"t": "node", "id": value}
    return subject  # case switch / initial load -> the case subject


@app.callback(
    Output("side-panel", "children"),
    Output("expand-btn", "children"), Output("expand-btn", "disabled"),
    Input("inspect-store", "data"), Input("case", "value"),
)
def _side_panel(inspect, case_id):
    ego = _result(case_id)["ego"]
    if inspect and inspect.get("t") == "edge":
        return _edge_panel(case_id, inspect["d"]), "Expand next hop", True
    if inspect and inspect.get("t") == "cluster":
        return (_cluster_panel(case_id, inspect["id"]),
                "Expand next hop", True)
    node_id = (inspect or {}).get("id")
    if node_id not in ego.nodes:
        node_id = DA.case_meta(case_id)["masked"]
    label = "Expand next hop of %s" % (_name_of(ego, node_id))
    return _node_panel(case_id, node_id), label, False


@app.callback(
    Output("expanded-store", "data"),
    Input("expand-btn", "n_clicks"), Input("reset-expand-btn", "n_clicks"),
    Input("case", "value"), Input("cpty-table", "active_cell"),
    Input("dbltap-store", "data"),
    Input({"type": "cluster-jump", "node": ALL}, "n_clicks"),
    State("inspect-store", "data"), State("expanded-store", "data"),
    State("cpty-table", "derived_viewport_data"),
)
def _expand(n_expand, n_reset, case_id, active_cell, dbltap, cluster_jumps,
            inspect, expanded, viewport_rows):
    """All the ways deeper hops get revealed. Every expansion is that
    node's TOP-K riskiest neighbours (alerted first) — never a full hub.

    Store shape: {child: parent} — the parent link draws the expansion
    tree edges and keeps the drill structure legible."""
    ctx = dash.callback_context
    trigger = ctx.triggered_id
    value = ctx.triggered[0]["value"] if ctx.triggered else None
    ego = _result(case_id)["ego"]
    current = (dict(expanded) if isinstance(expanded, dict)
               else {n: None for n in (expanded or [])})

    def _drill(node):
        out = dict(current)
        out.setdefault(node, None)  # the drilled node itself stays revealed
        for child in expand_top(ego, node) - {node}:
            out.setdefault(child, node)
        return out

    if trigger is None:
        # Dash re-fires this callback with an EMPTY trigger whenever the
        # cluster-jump pattern inputs are re-created (the member list mounts
        # or unmounts on a side-panel re-render). That is not a user action:
        # treating it as initial-load used to wipe the store and silently
        # UNDO the jump the user had just made. Only explicit triggers write.
        raise PreventUpdate
    if trigger == "case" or (trigger == "reset-expand-btn" and value):
        return {}
    if trigger == "dbltap-store" and value and value.get("id") in ego.nodes:
        return _drill(value["id"])
    if trigger == "expand-btn" and value:
        focus = (inspect or {}).get("id")
        if (inspect or {}).get("t") != "node" or focus not in ego.nodes:
            raise PreventUpdate
        return _drill(focus)
    if isinstance(trigger, dict) and trigger.get("type") == "cluster-jump":
        if not value:
            raise PreventUpdate
        node = trigger["node"]  # jump out of the cluster into entity view
        if node in ego.nodes:
            current.setdefault(node, None)
            return current
        raise PreventUpdate
    if trigger == "cpty-table" and value and viewport_rows:
        row = value.get("row")
        if row is None or row >= len(viewport_rows):
            raise PreventUpdate
        node = _row_node(viewport_rows[row])
        # reveal a clicked row that isn't part of the current baseline view
        if node in ego.nodes:
            current.setdefault(node, None)
            return current
    raise PreventUpdate


@app.callback(
    Output("view-mode", "value", allow_duplicate=True),
    Input({"type": "cluster-jump", "node": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _cluster_jump_switches_view(clicks):
    """Clicking a cluster member opens it in the entity view."""
    ctx = dash.callback_context
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    return "entities"


@app.callback(
    Output("view-mode", "value"), Output("top-n", "value"),
    Output("min-risk", "value"), Output("edge-kinds", "value"),
    Output("highlight-path", "value"), Output("isolate-exp", "value"),
    Output("expanded-store", "data", allow_duplicate=True),
    Output("place-nonce", "data"),
    Input("reset-view-btn", "n_clicks"), State("place-nonce", "data"),
    prevent_initial_call=True,
)
def _reset_view(n, nonce):
    ctx = dash.callback_context
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    # bumping the nonce makes the clientside pass forget every remembered
    # position: Reset view is the rescue hatch back to the canonical layout
    return ("entities", config.TOP_N_DEFAULT, 0.0, ["txn", "identity"],
            [], [], {}, (nonce or 0) + 1)


@app.callback(
    Output("isolate-exp", "value", allow_duplicate=True),
    Input("expanded-store", "data"), State("isolate-exp", "value"),
    prevent_initial_call=True,
)
def _isolate_follows_expansions(expanded, value):
    """The lens cannot outlive the expansions it isolates."""
    if expanded or not value:
        raise PreventUpdate
    return []


@app.callback(
    Output("conclusion-content", "children"), Output("conclusion-edit", "value"),
    Input("case", "value"), Input("conclusion-refresh", "data"),
)
def _conclusion(case_id, _bump):
    return _conclusion_content(case_id), read_conclusion(case_id) or ""


@app.callback(
    Output("conclusion-refresh", "data"),
    Input("refresh-conclusion-btn", "n_clicks"),
    Input("save-conclusion-btn", "n_clicks"),
    State("conclusion-edit", "value"), State("case", "value"),
    State("conclusion-refresh", "data"), prevent_initial_call=True,
)
def _conclusion_actions(n_refresh, n_save, text, case_id, bump):
    ctx = dash.callback_context
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    if ctx.triggered_id == "save-conclusion-btn":
        write_conclusion(case_id, text or "")
    return (bump or 0) + 1


@app.callback(
    Output("graph", "elements"), Output("graph", "stylesheet"),
    Output("graph", "layout"),
    Output("decision-panel", "children"), Output("quickstats-panel", "children"),
    Output("graph-caption", "children"),
    Output("drivers-card", "children"), Output("paths-card-content", "children"),
    Output("node-search", "options"), Output("root", "className"),
    Output("view-sig", "data"),
    Output("cpty-table", "sort_by"), Output("node-search", "value"),
    Output("isolate-exp", "options"),
    Input("case", "value"), Input("top-n", "value"), Input("min-risk", "value"),
    Input("edge-kinds", "value"), Input("layout-mode", "value"),
    Input("highlight-path", "value"), Input("theme-store", "data"),
    Input("expanded-store", "data"), Input("view-mode", "value"),
    Input("isolate-exp", "value"), Input("place-nonce", "data"),
)
def _render(case_id, top_n, min_risk, edge_kinds, layout_mode, highlight,
            theme, expanded, view_mode, isolate, place_nonce):
    r = _result(case_id)
    ev = r["evidence"]
    els, stats = _elements(case_id, top_n or config.TOP_N_DEFAULT, min_risk,
                           edge_kinds or [], expanded,
                           highlight=bool(highlight),
                           mode=view_mode or "entities",
                           isolate=bool(isolate))
    ranked_nodes = sorted(r["ego"].nodes(data=True),
                          key=lambda t: t[1].get("final_risk", 0), reverse=True)
    search_opts = [{"label": "%s (%s)" % (a.get("name") or _display_id(n, a),
                                          _display_id(n, a)),
                    "value": n}
                   for n, a in ranked_nodes[:config.SEARCH_MAX_OPTIONS]]
    # table sort + graph search reset on case switch only
    try:
        trigger = dash.callback_context.triggered_id
    except Exception:  # outside a live callback (unit tests) -> initial load
        trigger = None
    if trigger in (None, "case"):
        sort_by, search_val = [], None
    else:
        sort_by = search_val = dash.no_update

    view_sig = json.dumps([case_id, top_n, min_risk, sorted(edge_kinds or []),
                           layout_mode, sorted(expanded or []),
                           bool(highlight), view_mode, bool(isolate),
                           place_nonce or 0])
    # both views share the motion model: "live" = preset + hints + the
    # clientside sim (cluster hexagons carry ring hints just like entities);
    # force/rings = static cose — rings-by-hop has no meaning for
    # communities, so it falls back to force in the cluster view. Static
    # layouts get a fresh dict identity when the element set changed so
    # dash-cytoscape re-runs them (new nodes must be laid out).
    eff_layout = layout_mode
    if (view_mode or "entities") == "clusters" and layout_mode != "live":
        eff_layout = "force"
    layout = _static_layout(eff_layout, ev["subject_id"], els)
    return (els, _stylesheet(theme), layout,
            _decision_panel(case_id, ev), _quickstats_panel(ev),
            _graph_caption(stats, r["ego"]),
            _drivers_card(ev), _paths_card(ev), search_opts,
            "theme-%s" % theme, view_sig, sort_by, search_val,
            _isolate_options(bool(expanded),
                             in_clusters=(view_mode or "entities") == "clusters"))


# ---- counterparty table: server-side custom filtering + row selection ----
@app.callback(
    Output("cpty-table", "data"), Output("cpty-table", "columns"),
    Output("table-note", "children"), Output("cpty-table", "active_cell"),
    Input("case", "value"),
    Input("flt-text", "value"), Input("flt-decision", "value"),
    Input("flt-type", "value"), Input("flt-alerted", "value"),
    Input("flt-risk-op", "value"), Input("flt-risk-val", "value"),
    Input("flt-amt-op", "value"), Input("flt-amt-val", "value"),
    Input("flt-txn-op", "value"), Input("flt-txn-val", "value"),
    Input("flt-first", "start_date"), Input("flt-first", "end_date"),
    Input("flt-last", "start_date"), Input("flt-last", "end_date"),
)
def _filter_table(case_id, text, decisions, type_, alerted, risk_op, risk_val,
                  amt_op, amt_val, txn_op, txn_val, f_from, f_to, l_from, l_to):
    frame, total = _counterparty_frame(case_id)
    filtered = _apply_filters(
        frame, text=text, decisions=decisions, type_=type_,
        alerted_only=bool(alerted), risk_op=risk_op, risk_val=risk_val,
        amt_op=amt_op, amt_val=amt_val, txn_op=txn_op, txn_val=txn_val,
        first_from=f_from, first_to=f_to, last_from=l_from, last_to=l_to)
    active = any([text and str(text).strip(), decisions, type_, alerted,
                  risk_val not in (None, ""), amt_val not in (None, ""),
                  txn_val not in (None, ""), f_from, f_to, l_from, l_to])
    if active:
        note = "showing %s of %s counterparties (filtered)" % (
            format(len(filtered), ","), format(total, ","))
    elif total > len(frame):
        note = ("top %s of %s counterparties by risk — filter to reach the rest"
                % (format(len(frame), ","), format(total, ",")))
    else:
        note = ""
    # clearing active_cell drops any stale row highlight after a filter change
    return filtered.to_dict("records"), _table_columns(filtered), note, None


@app.callback(
    Output("flt-text", "value"), Output("flt-decision", "value"),
    Output("flt-type", "value"), Output("flt-alerted", "value"),
    Output("flt-risk-op", "value"), Output("flt-risk-val", "value"),
    Output("flt-amt-op", "value"), Output("flt-amt-val", "value"),
    Output("flt-txn-op", "value"), Output("flt-txn-val", "value"),
    Output("flt-first", "start_date"), Output("flt-first", "end_date"),
    Output("flt-last", "start_date"), Output("flt-last", "end_date"),
    Input("case", "value"), Input("flt-clear", "n_clicks"),
)
def _reset_table_filters(case_id, n_clear):
    """Reset all filter controls — on Clear, and on case switch (analytic
    state must not leak across investigations)."""
    return ("", [], None, [], "gte", None, "gte", None, "gte", None,
            None, None, None, None)


@app.callback(
    Output("cpty-table", "style_data_conditional"),
    Input("cpty-table", "active_cell"),
)
def _highlight_row(active_cell):
    row = active_cell.get("row") if active_cell else None
    return _row_conditional(row)


@app.callback(
    Output("download", "data"),
    Input("dl-evidence-btn", "n_clicks"), Input("dl-prompt-btn", "n_clicks"),
    State("case", "value"), prevent_initial_call=True,
)
def _download(n_ev, n_pr, case_id):
    ctx = dash.callback_context
    # Guard: only a real button press (n_clicks > 0 on the triggering button)
    # may download — component re-creation or initial wiring must not.
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    r = _result(case_id)
    if ctx.triggered_id == "dl-evidence-btn":
        return dict(content=json.dumps(r["evidence"], indent=2, default=str),
                    filename="evidence_case_%d.json" % case_id)
    return dict(content=r["prompt_path"].read_text(),
                filename=r["prompt_path"].name)


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "8050")))
