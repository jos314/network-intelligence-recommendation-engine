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

import dash
import dash_cytoscape as cyto
import pandas as pd
from dash import ALL, Input, Output, State, dash_table, dcc, html
from dash.dash_table.Format import Format, Group, Scheme
from dash.exceptions import PreventUpdate

from .. import config
from ..explain.paths import key_paths
from ..graph.ego import node_flow_summary
from ..ingest.crosswalk import NODE_EXTERNAL
from ..pipeline import run_all_cases
from .auth import verify_credentials

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
            "width": "mapData(risk, 0, 1, 16, 60)",
            "height": "mapData(risk, 0, 1, 16, 60)",
            "background-color": t["node"],
            "label": "data(label)", "font-size": "9px",
            "color": t["text"], "text-valign": "bottom", "text-margin-y": "4px",
            "text-outline-color": t["halo"], "text-outline-width": 1.5,
            "border-width": 0,
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
                   "border-color": ACCENT[theme]}},
        {"selector": "edge", "style": {
            "width": "mapData(weight, 0, 1, 1, 5)", "line-color": t["edge"],
            "curve-style": "bezier", "opacity": 0.9,
        }},
        {"selector": 'edge[kind = "txn"]', "style": {
            "target-arrow-shape": "triangle", "target-arrow-color": t["edge"],
            "arrow-scale": 0.8,
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


# One physics recipe shared by the Python layout prop and the drag-release
# handler injected below (kept in sync by construction).
_LIVE_PHYSICS = {"name": "cose", "animate": True, "randomize": False,
                 "nodeRepulsion": 12000, "idealEdgeLength": 80,
                 "numIter": 400, "padding": 24}

# Layout dicts are cached so an unchanged (mode, seed) returns the SAME
# object — otherwise every render hands cytoscape a "new" layout and the
# graph re-springs on theme flips and focus taps (camera-stability item).
_LAYOUT_CACHE = {}


def _layout_spec(mode, seed):
    key = (mode, seed)
    if key not in _LAYOUT_CACHE:
        if mode == "rings":  # §6: nodes ringed by hop distance around the seed
            spec = {"name": "breadthfirst", "circle": True, "animate": False,
                    "roots": '[id = "%s"]' % seed, "spacingFactor": 1.1}
        elif mode == "force":  # static: compute once, no motion
            spec = {"name": "cose", "animate": False, "nodeRepulsion": 12000,
                    "idealEdgeLength": 80, "padding": 24}
        else:  # "live" (default): animated physics + spring-on-release
            spec = dict(_LIVE_PHYSICS, fit=True)
        _LAYOUT_CACHE[key] = spec
    return _LAYOUT_CACHE[key]


# ------------------------------------------------------------ data (once)
RUN = run_all_cases(verbose=False)
CASES = sorted(RUN["results"].keys())
CALIBRATION = RUN["calibrator"].describe()


def _short(n: str) -> str:
    return ("ext…" + n[-4:]) if n.upper().startswith("PSEUDO_") else n


def _node_label(n, attrs) -> str:
    return (attrs.get("name") or _short(n))[:22]


def _name_of(ego, n) -> str:
    return ego.nodes[n].get("name") or _short(n) if n in ego.nodes else _short(n)


def _fmt_money(x) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1_000_000:
        return "$%.2fM" % (x / 1_000_000)
    if abs(x) >= 10_000:
        return "$%.0fK" % (x / 1_000)
    return "$%s" % format(int(round(x)), ",")


def _fmt_date(ts) -> str:
    return "—" if ts is None else str(ts)[:10]


def _top_path_members(case_id):
    """Nodes and consecutive pairs of the strongest key path."""
    ego = RUN["results"][case_id]["ego"]
    paths = key_paths(ego, top_k=1)
    if not paths:
        return set(), set()
    p = paths[0]["path"]
    return set(p), {frozenset(pair) for pair in zip(p, p[1:])}


def _elements(case_id, depth, min_risk, edge_kinds, expanded=None,
              highlight=False):
    """Graph elements + disclosure stats.

    Evidence-safety rules: alerted nodes are exempt from the min-risk cut;
    when the key path is highlighted, its nodes are force-included even
    beyond the depth slider. What filters DO hide is counted in `stats`
    and disclosed in the graph caption — never silently dropped.

    Parallel txn edges between the same pair are aggregated (summed amount
    and count, spanned dates) into ONE drawn edge; its data feeds the edge
    inspector. Returns (elements, stats).
    """
    ego = RUN["results"][case_id]["ego"]
    seed = ego.graph["seed"]
    expanded = set(expanded or [])
    path_nodes, path_pairs = _top_path_members(case_id) if highlight else (set(), set())

    visible, hidden_alerted, hidden_flagged = set(), 0, 0
    path_revealed = 0
    for n, a in ego.nodes(data=True):
        in_depth = a.get("hop", 99) <= depth
        forced = n in expanded or n in path_nodes or n == seed
        if not (in_depth or forced):
            if a.get("alerted"):
                hidden_alerted += 1
            continue
        risky_exempt = a.get("alerted") or forced
        if a.get("final_risk", 0.0) < min_risk and not risky_exempt:
            if a.get("decision") in (config.DECISION_EDD, config.DECISION_SAR):
                hidden_flagged += 1
            continue
        if not in_depth and n in path_nodes and n not in expanded:
            path_revealed += 1
        visible.add(n)

    els = []
    for n in visible:
        a = ego.nodes[n]
        classes = []
        if n in path_nodes:
            classes.append("onpath")
        els.append({"data": {
            "id": n, "label": _node_label(n, a),
            "name": a.get("name") or _short(n),
            "risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision", config.DECISION_NO_ACTION),
            "alerted": bool(a.get("alerted")), "is_seed": n == seed,
            "hop": a.get("hop"), "node_type": a.get("node_type"),
        }, "classes": " ".join(classes)})

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
        els.append({"data": {
            "source": u, "target": v, "kind": "txn",
            "weight": round(min(g["amount"] / max_amt, 1.0), 3),
            "amount": round(g["amount"], 2), "count": g["count"],
            "first": _fmt_date(g["first"]), "last": _fmt_date(g["last"]),
            "src_country": g["src_country"], "dst_country": g["dst_country"],
        }, "classes": "onpath" if frozenset((u, v)) in path_pairs else ""})
    for (u, v, kind), g in ident_groups.items():
        els.append({"data": {"source": u, "target": v, "kind": kind,
                             "weight": round(min(g["weight"], 1.0), 3),
                             "value": g["value"]},
                    "classes": ""})

    stats = {
        "nodes_shown": len(visible), "nodes_total": ego.number_of_nodes(),
        "edges_shown": len(txn_groups) + len(ident_groups),
        "edges_total": len(total_groups),
        "hidden_alerted": hidden_alerted, "hidden_flagged": hidden_flagged,
        "path_revealed": path_revealed,
    }
    return els, stats


_FRAME_COLUMNS = ["entity", "id", "type", "hop", "final_risk", "decision",
                  "alerted", "total_amount", "txn_count", "first_seen",
                  "last_seen", "volume_share_%", "shared_attrs", "case_id"]


def _counterparty_frame(case_id) -> pd.DataFrame:
    ego = RUN["results"][case_id]["ego"]
    seed = ego.graph["seed"]
    rows = []
    for n, a in ego.nodes(data=True):
        if n == seed:
            continue
        flow = node_flow_summary(ego, n)
        hop = a.get("hop")
        rows.append({
            "entity": a.get("name") or _short(n), "id": n,
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
        })
    if not rows:  # isolated seed: render an empty table, never a KeyError
        return pd.DataFrame(columns=_FRAME_COLUMNS)
    return pd.DataFrame(rows).sort_values("final_risk", ascending=False)


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
        col = {"name": _TABLE_COLUMN_LABELS.get(c, c), "id": c}
        if c == "total_amount":
            col.update(type="numeric", format=_MONEY_FMT)
        cols.append(col)
    return cols


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
        style_data_conditional=[
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
        ],
    )


# --------------------------------------------------------- UI fragments
def _kpis(case_id, ev):
    ego = RUN["results"][case_id]["ego"]
    subject_own = ego.nodes[ev["subject_id"]].get("decision", ev["decision"])
    order = [config.DECISION_NO_ACTION, config.DECISION_EDD, config.DECISION_SAR]
    escalated = order.index(ev["decision"]) > order.index(subject_own)

    p = ev["calibrated_score"]
    fill = ("var(--risk-red)" if p >= config.DECISION_T2
            else "var(--risk-yellow)" if p >= config.DECISION_T1
            else "var(--ok-green)")
    near_threshold = min(abs(p - config.DECISION_T1), abs(p - config.DECISION_T2)) < 0.02
    p_text = ("%.3f" if near_threshold else "%.2f") % p
    calibrated = bool(CALIBRATION.get("calibrated"))
    score_label = "Calibrated risk" if calibrated else "Risk score (uncalibrated)"
    bands_caption = "< %.2f no action · %.2f–%.2f EDD · ≥ %.2f SAR" % (
        config.DECISION_T1, config.DECISION_T1, config.DECISION_T2, config.DECISION_T2)

    score_children = [
        html.Div([html.Span(score_label),
                  html.Span(" ⚠", className="warn-badge",
                            title="Calibration fallback: only %d weak positive labels "
                                  "(need %d). Thresholds ride on the raw score."
                                  % (CALIBRATION.get("n_pos", 0),
                                     config.MIN_CALIBRATION_POSITIVES))
                  if not calibrated else html.Span("")],
                 className="kpi-label", title=GLOSSARY["risk_score"]),
        html.Div(p_text, className="kpi-value"),
        html.Div([
            html.Div(className="score-fill",
                     style={"width": "%d%%" % round(100 * p), "background": fill}),
            html.Div(className="score-tick",
                     style={"left": "%d%%" % round(100 * config.DECISION_T1)},
                     title="t1 = %.2f → EDD" % config.DECISION_T1),
            html.Div(className="score-tick",
                     style={"left": "%d%%" % round(100 * config.DECISION_T2)},
                     title="t2 = %.2f → SAR" % config.DECISION_T2),
        ], className="score-track"),
        html.Div(bands_caption, className="kpi-caption"),
    ]

    if config.WATCHLIST_CONNECTED:
        watchlist_kpi = [html.Div("Sanctioned / watchlist", className="kpi-label"),
                         html.Div(str(len(ev["sanctioned_neighbors"])),
                                  className="kpi-value")]
    else:  # a screening that did not run must never read as a clean 0
        watchlist_kpi = [html.Div("Sanctioned / watchlist", className="kpi-label"),
                         html.Div("—", className="kpi-value kpi-disabled"),
                         html.Div("not screened — no watchlist source",
                                  className="kpi-caption")]

    flags = ev["structural_flags"]
    flag_spans = [html.Span(f, title=GLOSSARY.get(f, f),
                            style={"marginRight": "6px"}) for f in flags] or "—"
    window = ev.get("activity_window", {})

    return [
        html.Div([html.Div("Case decision", className="kpi-label"),
                  html.Div([html.Span(ev["decision"],
                                      className=DECISION_CHIP[ev["decision"]]),
                            html.Span(" escalated by network evidence",
                                      className="kpi-caption")
                            if escalated else html.Span("")],
                           style={"marginTop": "4px"})], className="kpi"),
        html.Div(score_children, className="kpi"),
        html.Div([html.Div("Alerted within 2 hops", className="kpi-label",
                           title="Counterparties with a TM alert at hop 1–2"),
                  html.Div(str(len(ev["alerted_neighbors"])), className="kpi-value",
                           title=", ".join(ev["alerted_neighbors"]) or "none")],
                 className="kpi"),
        html.Div(watchlist_kpi, className="kpi"),
        html.Div([html.Div("Activity window", className="kpi-label",
                           title="First to last transaction date in this network"),
                  html.Div("%s → %s" % (window.get("first") or "—",
                                        window.get("last") or "—"),
                           className="kpi-value", style={"fontSize": "13px"})],
                 className="kpi"),
        html.Div([html.Div("Subject total flow", className="kpi-label",
                           title="Sum of all transaction amounts touching the subject"),
                  html.Div(_fmt_money(ev.get("subject_total_flow")),
                           className="kpi-value")], className="kpi"),
        html.Div([html.Div("Scored network (depth %s)" % ev["network_size"]["depth"],
                           className="kpi-label",
                           title="Scoring always covers the full depth-%s network; "
                                 "the graph view starts at depth 1"
                                 % ev["network_size"]["depth"]),
                  html.Div("%d nodes · %d edges" % (ev["network_size"]["nodes"],
                                                    ev["network_size"]["edges"]),
                           className="kpi-value", style={"fontSize": "14px"})],
                 className="kpi"),
        html.Div([html.Div("Typology flags", className="kpi-label"),
                  html.Div(flag_spans, className="kpi-value",
                           style={"fontSize": "14px"})], className="kpi"),
        html.Div([html.Div("LOB", className="kpi-label"),
                  html.Div(ev["lob"] or "—", className="kpi-value")], className="kpi"),
    ]


def _reasons_row(ev):
    return [html.Div("· " + r, className="reason-line")
            for r in ev["decision_reasons"]]


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
    ego = RUN["results"][case_id]["ego"]
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
    ego = RUN["results"][case_id]["ego"]
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


def _graph_caption(stats):
    parts = [html.Span("showing %d of %d entities · %d of %d relationships"
                       % (stats["nodes_shown"], stats["nodes_total"],
                          stats["edges_shown"], stats["edges_total"]))]
    hidden_risky = stats["hidden_alerted"] + stats["hidden_flagged"]
    if hidden_risky:
        parts.append(html.Span(
            "⚠ %d risk-relevant %s outside current filters"
            % (hidden_risky, "entity is" if hidden_risky == 1 else "entities are"),
            className="caption-warn",
            title="Alerted or EDD/SAR entities beyond the depth slider. "
                  "Raise depth or use Reset filters to see them."))
    if stats["path_revealed"]:
        parts.append(html.Span("key path extends beyond depth — %d node%s revealed"
                               % (stats["path_revealed"],
                                  "" if stats["path_revealed"] == 1 else "s"),
                               className="caption-note"))
    return parts


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
    html.Span("size = risk · thickness = amount", title="Node size is the "
              "entity's risk score; edge thickness is the flow amount relative "
              "to this case's largest flow"),
], className="legend")


# ------------------------------------------------------------------- app
# suppress_callback_exceptions: the login view and the main screen are
# rendered alternately into #page, so at any moment half the callback
# targets are legitimately absent from the layout.
app = dash.Dash(__name__, title="Network Intelligence — Counterparty Risk",
                suppress_callback_exceptions=True)

_case_options = [
    {"label": "Case %d — %s (%s)" % (c, RUN["results"][c]["evidence"]["subject_name"],
                                     RUN["results"][c]["evidence"]["lob"] or "?"),
     "value": c} for c in CASES]


def _login_view():
    return html.Div(html.Div([
        html.Div("◆", className="login-mark"),
        html.H2("Counterparty Network Risk", style={"margin": "0 0 2px",
                                                    "fontSize": "18px"}),
        html.Div("Sign in to open the analyst workspace",
                 style={"color": "var(--muted)", "fontSize": "12.5px",
                        "marginBottom": "18px"}),
        dcc.Input(id="login-user", placeholder="Username", type="text",
                  className="login-input", autoFocus=True),
        dcc.Input(id="login-pass", placeholder="Password", type="password",
                  className="login-input", n_submit=0),
        html.Button("Sign in", id="login-btn", n_clicks=0,
                    className="btn btn-primary login-btn"),
        html.Div(id="login-error", className="login-error"),
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
        html.Div(id="kpi-row", className="kpi-row"),
        html.Div(id="reasons-row", className="reasons-row card"),
        html.Div([
            html.Div([
                html.Div([
                    html.Div([html.Div("Depth", className="ctl-label"),
                              dcc.Slider(id="depth", min=1, max=config.EGO_DEPTH_VIEW,
                                         step=1, value=1,
                                         marks={i: str(i) for i in
                                                range(1, config.EGO_DEPTH_VIEW + 1)})],
                             style={"width": "130px"}),
                    html.Div([html.Div("Min risk", className="ctl-label"),
                              dcc.Slider(id="min-risk", min=0, max=1, step=0.05,
                                         value=0.0,
                                         marks={0: "0", 0.5: "0.5", 1: "1"})],
                             style={"width": "130px"}),
                    dcc.Checklist(id="edge-kinds",
                                  options=[{"label": " transactions", "value": "txn"},
                                           {"label": " identity links",
                                            "value": "identity"}],
                                  value=["txn", "identity"], inline=True),
                    dcc.RadioItems(id="layout-mode",
                                   options=[{"label": " live physics", "value": "live"},
                                            {"label": " force", "value": "force"},
                                            {"label": " rings by hop",
                                             "value": "rings"}],
                                   value="live", inline=True),
                    dcc.Checklist(id="highlight-path",
                                  options=[{"label": " highlight key risk path",
                                            "value": "on"}], value=[], inline=True),
                    html.Button("◎ Center subject", id="center-btn", className="btn",
                                n_clicks=0),
                    html.Button("Reset filters", id="reset-filters-btn",
                                className="btn", n_clicks=0),
                ], className="controls"),
                cyto.Cytoscape(id="graph", layout=_layout_spec("force", ""),
                               style={"width": "100%", "height": "500px"},
                               elements=[], stylesheet=_stylesheet("light"),
                               responsive=True),
                html.Div(id="graph-caption", className="graph-caption"),
                LEGEND,
            ], className="card card-graph"),
            html.Div([
                html.Div([
                    dcc.Dropdown(id="node-search", placeholder="Search entity…",
                                 style={"flex": "1"}),
                ], style={"display": "flex", "gap": "8px", "marginBottom": "8px",
                          "alignItems": "center"}),
                html.Div([
                    html.Button("Expand next hop", id="expand-btn", className="btn",
                                n_clicks=0),
                    html.Button("Reset expansion", id="reset-expand-btn",
                                className="btn", n_clicks=0),
                ], style={"display": "flex", "gap": "8px", "marginBottom": "10px"}),
                html.Div(id="side-panel", style={"overflowY": "auto",
                                                 "maxHeight": "520px"}),
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
            html.H4("Counterparties (ranked by risk) — click a row to inspect",
                    className="section"),
            dash_table.DataTable(id="cpty-table", sort_action="native",
                                 filter_action="native", page_size=12,
                                 export_format="csv", **_table_styles()),
        ], className="card", style={"marginTop": "12px"}),
        dcc.Store(id="theme-store", data="light"),
        dcc.Store(id="inspect-store"),
        dcc.Store(id="expanded-store", data=[]),
        dcc.Store(id="view-sig"),
        dcc.Store(id="center-op"),
        dcc.Store(id="fit-op"),
        dcc.Store(id="focus-op"),
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
# investigative state and must not be stolen. Also attaches (once per
# instance) the Obsidian-style spring-on-release drag handler.
_JS_SPRING = json.dumps(dict(_LIVE_PHYSICS, fit=False))
app.clientside_callback(
    """
    function(sig) {
        setTimeout(function () {
            %s
            if (!cy) { return; }
            if (cy.nodes().length) { cy.resize(); cy.fit(undefined, 40); }
            if (!cy.__springAttached) {
                cy.__springAttached = true;
                cy.on('grab', 'node', function (e) {
                    e.target.__grabPos = Object.assign({}, e.target.position());
                });
                cy.on('free', 'node', function (e) {
                    const mode = document.querySelector(
                        '#layout-mode input:checked');
                    if (!mode || mode.value !== 'live') { return; }
                    const p = e.target.position(), g = e.target.__grabPos;
                    if (g && Math.hypot(p.x - g.x, p.y - g.y) > 8) {
                        cy.layout(%s).run();
                    }
                });
            }
        }, 650);
        return window.dash_clientside.no_update;
    }
    """ % (_JS_FIND_CY, _JS_SPRING),
    Output("fit-op", "data"), Input("view-sig", "data"),
)

# Focus ring follows the inspected node without a server round-trip of the
# whole element list (camera stays put).
app.clientside_callback(
    """
    function(inspect) {
        %s
        if (cy) {
            cy.nodes('.focused').removeClass('focused');
            if (inspect && inspect.t === 'node' && inspect.id) {
                cy.getElementById(inspect.id).addClass('focused');
            }
        }
        return window.dash_clientside.no_update;
    }
    """ % _JS_FIND_CY,
    Output("focus-op", "data"), Input("inspect-store", "data"),
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
    State("cpty-table", "derived_viewport_data"),
)
def _inspect(tap_node, tap_edge, search, case_id, center_n, active_cell,
             jump_clicks, viewport_rows):
    """Single writer for what the side panel inspects. Every input except
    the case switch is guarded against fire-on-recreation (the download-bug
    lesson: re-created Input components re-fire their callbacks)."""
    ctx = dash.callback_context
    trigger = ctx.triggered_id
    value = ctx.triggered[0]["value"] if ctx.triggered else None
    subject = {"t": "node", "id": RUN["results"][case_id]["evidence"]["subject_id"]}

    if isinstance(trigger, dict) and trigger.get("type") == "cpty-jump":
        if not value:
            raise PreventUpdate
        return {"t": "node", "id": trigger["node"]}
    if trigger == "cpty-table":
        if not value or not viewport_rows:
            raise PreventUpdate
        row = value.get("row")
        if row is None or row >= len(viewport_rows):
            raise PreventUpdate
        return {"t": "node", "id": viewport_rows[row]["id"]}
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
    ego = RUN["results"][case_id]["ego"]
    if inspect and inspect.get("t") == "edge":
        return _edge_panel(case_id, inspect["d"]), "Expand next hop", True
    node_id = (inspect or {}).get("id")
    if node_id not in ego.nodes:
        node_id = RUN["results"][case_id]["evidence"]["subject_id"]
    label = "Expand next hop of %s" % (_name_of(ego, node_id))
    return _node_panel(case_id, node_id), label, False


@app.callback(
    Output("expanded-store", "data"),
    Input("expand-btn", "n_clicks"), Input("reset-expand-btn", "n_clicks"),
    Input("case", "value"), Input("cpty-table", "active_cell"),
    State("inspect-store", "data"), State("expanded-store", "data"),
    State("depth", "value"), State("cpty-table", "derived_viewport_data"),
)
def _expand(n_expand, n_reset, case_id, active_cell, inspect, expanded,
            depth, viewport_rows):
    ctx = dash.callback_context
    trigger = ctx.triggered_id
    value = ctx.triggered[0]["value"] if ctx.triggered else None
    ego = RUN["results"][case_id]["ego"]

    if trigger in (None, "case") or (trigger == "reset-expand-btn" and value):
        return []
    if trigger == "expand-btn" and value:
        focus = (inspect or {}).get("id")
        if (inspect or {}).get("t") != "node" or focus not in ego.nodes:
            raise PreventUpdate
        nbrs = set(ego.successors(focus)) | set(ego.predecessors(focus))
        return sorted(set(expanded or []) | nbrs | {focus})
    if trigger == "cpty-table" and value and viewport_rows:
        row = value.get("row")
        if row is None or row >= len(viewport_rows):
            raise PreventUpdate
        node = viewport_rows[row]["id"]
        # reveal a clicked row that sits beyond the current depth slider
        if node in ego.nodes and ego.nodes[node].get("hop", 0) > (depth or 1):
            return sorted(set(expanded or []) | {node})
    raise PreventUpdate


@app.callback(
    Output("depth", "value"), Output("min-risk", "value"),
    Output("edge-kinds", "value"),
    Input("reset-filters-btn", "n_clicks"), prevent_initial_call=True,
)
def _reset_filters(n):
    ctx = dash.callback_context
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    return 1, 0.0, ["txn", "identity"]


@app.callback(
    Output("graph", "elements"), Output("graph", "stylesheet"),
    Output("graph", "layout"),
    Output("cpty-table", "data"), Output("cpty-table", "columns"),
    Output("kpi-row", "children"), Output("reasons-row", "children"),
    Output("graph-caption", "children"),
    Output("drivers-card", "children"), Output("paths-card-content", "children"),
    Output("node-search", "options"), Output("root", "className"),
    Output("view-sig", "data"),
    Output("cpty-table", "filter_query"), Output("cpty-table", "sort_by"),
    Output("node-search", "value"),
    Input("case", "value"), Input("depth", "value"), Input("min-risk", "value"),
    Input("edge-kinds", "value"), Input("layout-mode", "value"),
    Input("highlight-path", "value"), Input("theme-store", "data"),
    Input("expanded-store", "data"),
)
def _render(case_id, depth, min_risk, edge_kinds, layout_mode, highlight,
            theme, expanded):
    r = RUN["results"][case_id]
    ev = r["evidence"]
    els, stats = _elements(case_id, depth, min_risk, edge_kinds or [], expanded,
                           highlight=bool(highlight))
    frame = _counterparty_frame(case_id)
    search_opts = [{"label": "%s (%s)" % (a.get("name") or _short(n), _short(n)),
                    "value": n}
                   for n, a in sorted(r["ego"].nodes(data=True),
                                      key=lambda t: t[1].get("final_risk", 0),
                                      reverse=True)]
    # analytic state (table filter/sort, search) resets on case switch only
    try:
        trigger = dash.callback_context.triggered_id
    except Exception:  # outside a live callback (unit tests) -> initial load
        trigger = None
    if trigger in (None, "case"):
        filter_q, sort_by, search_val = "", [], None
    else:
        filter_q = sort_by = search_val = dash.no_update

    view_sig = json.dumps([case_id, depth, min_risk, sorted(edge_kinds or []),
                           layout_mode, sorted(expanded or []),
                           bool(highlight)])
    return (els, _stylesheet(theme), _layout_spec(layout_mode, ev["subject_id"]),
            frame.to_dict("records"), _table_columns(frame),
            _kpis(case_id, ev), _reasons_row(ev), _graph_caption(stats),
            _drivers_card(ev), _paths_card(ev), search_opts,
            "theme-%s" % theme, view_sig,
            filter_q, sort_by, search_val)


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
    r = RUN["results"][case_id]
    if ctx.triggered_id == "dl-evidence-btn":
        return dict(content=json.dumps(r["evidence"], indent=2, default=str),
                    filename="evidence_case_%d.json" % case_id)
    return dict(content=r["prompt_path"].read_text(),
                filename=r["prompt_path"].name)


if __name__ == "__main__":
    app.run(debug=False, port=8050)
