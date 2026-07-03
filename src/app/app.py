"""P4 — the analyst screen (§6): Obsidian-style force-directed ego-graph,
one subject at a time, plus the ranked counterparty table.

Encodings (per the build plan):
  * node SIZE   = final_risk (bigger = riskier)
  * node COLOUR = decision (red = SAR, yellow = EDD, neutral = No action)
  * ring/border = TM-alerted; diamond = the case subject (pinned focus)
  * solid edge  = transaction (arrow along money flow); dashed = identity link
  * accent glow = the top propagated-risk path (§5.1's highlighted subgraph)

Overwhelm control: default render = subject + level 1; depth slider (1-3),
min-risk filter, edge-family toggles, per-node "expand next hop"
(progressive disclosure). Layouts: live physics (Obsidian-style, drag a node
and the graph springs), static force, or rings-by-hop-distance.

Nothing on this screen is hard-coded to the demo fixture: cases, labels,
KPIs, drivers, paths, and edge scales all derive from whatever the loaders
return, so dropping the real tables into data/ changes every element here.

Run:  .venv/bin/python -m src.app.app   (then open http://127.0.0.1:8050)
"""
import json

import dash
import dash_cytoscape as cyto
import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from .. import config
from ..explain.paths import key_paths
from ..pipeline import run_all_cases


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
        {"selector": "node[?alerted]",
         "style": {"border-width": 3, "border-color": RISK_RED[theme]}},
        {"selector": "node[?is_seed]",
         "style": {"shape": "diamond", "border-width": 3,
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


def _layout_spec(mode, seed):
    if mode == "rings":  # §6: nodes ringed by hop distance around the seed
        return {"name": "breadthfirst", "circle": True, "animate": False,
                "roots": '[id = "%s"]' % seed, "spacingFactor": 1.1}
    if mode == "force":  # static: compute once, no motion
        return {"name": "cose", "animate": False, "nodeRepulsion": 12000,
                "idealEdgeLength": 80, "padding": 24}
    # "live" (default): animated physics — nodes visibly spring into place,
    # and a clientside handler re-runs the simulation whenever the user
    # drops a dragged node, so the graph re-settles like Obsidian's view
    return dict(_LIVE_PHYSICS, fit=True)


# One physics recipe shared by the Python layout prop and the drag-release
# handler injected below (kept in sync by construction).
_LIVE_PHYSICS = {"name": "cose", "animate": True, "randomize": False,
                 "nodeRepulsion": 12000, "idealEdgeLength": 80,
                 "numIter": 400, "padding": 24}


# ------------------------------------------------------------ data (once)
RUN = run_all_cases(verbose=False)
CASES = sorted(RUN["results"].keys())


def _short(n: str) -> str:
    return ("ext…" + n[-4:]) if n.upper().startswith("PSEUDO_") else n


def _node_label(n, attrs) -> str:
    return (attrs.get("name") or _short(n))[:22]


def _top_path_members(case_id):
    """Nodes and consecutive pairs of the strongest key path."""
    ego = RUN["results"][case_id]["ego"]
    paths = key_paths(ego, top_k=1)
    if not paths:
        return set(), set()
    p = paths[0]["path"]
    return set(p), {frozenset(pair) for pair in zip(p, p[1:])}


def _elements(case_id, depth, min_risk, edge_kinds, expanded=None,
              highlight=False, focus=None):
    ego = RUN["results"][case_id]["ego"]
    seed = ego.graph["seed"]
    expanded = set(expanded or [])
    path_nodes, path_pairs = _top_path_members(case_id) if highlight else (set(), set())

    visible = set()
    for n, a in ego.nodes(data=True):
        in_depth = a.get("hop", 99) <= depth
        if not (in_depth or n in expanded):
            continue
        if a.get("final_risk", 0.0) < min_risk and n != seed and n not in expanded:
            continue
        visible.add(n)

    # edge-thickness scale derived from this ego's own flow volumes (never a
    # fixed constant — must hold for whatever the real tables contain)
    max_amt = max((d.get("total_amount_base", 0.0)
                   for _, _, d in ego.edges(data=True) if d.get("kind") == "txn"),
                  default=1.0) or 1.0

    els = []
    for n in visible:
        a = ego.nodes[n]
        classes = []
        if n in path_nodes:
            classes.append("onpath")
        if focus and n == focus:
            classes.append("focused")
        els.append({"data": {
            "id": n, "label": _node_label(n, a),
            "risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision", config.DECISION_NO_ACTION),
            "alerted": bool(a.get("alerted")), "is_seed": n == seed,
            "hop": a.get("hop"),
        }, "classes": " ".join(classes)})

    seen_pairs = set()
    for u, v, d in ego.edges(data=True):
        if u not in visible or v not in visible:
            continue
        kind = d.get("kind")
        family = "txn" if kind == "txn" else "identity"
        if family not in edge_kinds:
            continue
        pair = (u, v, kind)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        weight = min((d.get("total_amount_base", 0.0) / max_amt) if kind == "txn"
                     else d.get("weight", 0.5), 1.0)
        onpath = kind == "txn" and frozenset((u, v)) in path_pairs
        els.append({"data": {"source": u, "target": v, "kind": kind,
                             "weight": round(weight, 3)},
                    "classes": "onpath" if onpath else ""})
    return els


def _counterparty_frame(case_id) -> pd.DataFrame:
    ego = RUN["results"][case_id]["ego"]
    seed = ego.graph["seed"]
    rows = []
    for n, a in ego.nodes(data=True):
        if n == seed:
            continue
        rows.append({
            "entity": a.get("name") or _short(n), "id": n, "hop": a.get("hop"),
            "final_risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision"),
            "alerted": "yes" if a.get("alerted") else "",
            "volume_share_%": round(100 * a.get("rel_components", {}).get("volume_share", 0.0), 1),
            "shared_attrs": ", ".join(a.get("rel_shared_kinds", [])),
        })
    return pd.DataFrame(rows).sort_values("final_risk", ascending=False)


# --------------------------------------------------------- UI fragments
def _kpis(ev):
    p = ev["calibrated_score"]
    fill = ("var(--risk-red)" if p >= config.DECISION_T2
            else "var(--risk-yellow)" if p >= config.DECISION_T1
            else "var(--ok-green)")
    flags = ", ".join(ev["structural_flags"]) or "—"
    return [
        html.Div([html.Div("Decision", className="kpi-label"),
                  html.Div(html.Span(ev["decision"],
                                     className=DECISION_CHIP[ev["decision"]]),
                           style={"marginTop": "4px"})], className="kpi"),
        html.Div([html.Div("Calibrated risk", className="kpi-label"),
                  html.Div("%.2f" % p, className="kpi-value"),
                  html.Div(html.Div(className="score-fill",
                                    style={"width": "%d%%" % round(100 * p),
                                           "background": fill}),
                           className="score-track")], className="kpi"),
        html.Div([html.Div("Alerted neighbours", className="kpi-label"),
                  html.Div(str(len(ev["alerted_neighbors"])), className="kpi-value")],
                 className="kpi"),
        html.Div([html.Div("Sanctioned / watchlist", className="kpi-label"),
                  html.Div(str(len(ev["sanctioned_neighbors"])), className="kpi-value")],
                 className="kpi"),
        html.Div([html.Div("Ego network", className="kpi-label"),
                  html.Div("%d nodes · %d edges" % (ev["network_size"]["nodes"],
                                                    ev["network_size"]["edges"]),
                           className="kpi-value", style={"fontSize": "14px"})],
                 className="kpi"),
        html.Div([html.Div("Typology flags", className="kpi-label"),
                  html.Div(flags, className="kpi-value", style={"fontSize": "14px"})],
                 className="kpi"),
        html.Div([html.Div("LOB", className="kpi-label"),
                  html.Div(ev["lob"] or "—", className="kpi-value")], className="kpi"),
    ]


def _risk_parts_bars(attrs):
    parts = attrs.get("risk_parts", {})
    rows = []
    for part in ("base", "rel", "prop", "struct"):
        w = config.STAGE_E_WEIGHTS[part]
        contrib = w * parts.get(part, 0.0)
        rows.append(html.Div([
            html.Div("%s (w=%.2f)" % (part, w), className="bar-label"),
            html.Div(html.Div(className="bar-fill",
                              style={"width": "%d%%" % round(100 * min(contrib / 0.35, 1.0)),
                                     "background": "var(--accent)"}),
                     className="bar-track"),
            html.Div("%.3f" % contrib, className="bar-val"),
        ], className="bar-row"))
    return rows


def _node_panel(case_id, node_id):
    ego = RUN["results"][case_id]["ego"]
    if node_id not in ego.nodes:
        return html.Div("Click a node (or search above) to inspect it.",
                        style={"color": "var(--muted)", "fontSize": "12px"})
    a = ego.nodes[node_id]
    decision = a.get("decision", config.DECISION_NO_ACTION)
    props = [
        ("Type", a.get("node_type")), ("Hop", a.get("hop")),
        ("Country", a.get("country") or "—"),
        ("Country risk", a.get("country_risk") or "—"),
        ("PEP", a.get("pep_flag") or "—"), ("CRR", a.get("crr") or "—"),
        ("Alerted", "yes" if a.get("alerted") else "no"),
        ("Accounts", len(a.get("accounts", []))),
        ("Final risk", "%.3f" % a.get("final_risk", 0.0)),
    ]
    nbrs = set(ego.successors(node_id)) | set(ego.predecessors(node_id))
    top = sorted(nbrs, key=lambda m: ego.nodes[m].get("final_risk", 0.0),
                 reverse=True)[:config.TOP_COUNTERPARTIES]
    return html.Div([
        html.Div([
            html.Span(a.get("name") or _short(node_id),
                      style={"fontWeight": 650, "fontSize": "14px", "flex": "1"}),
            html.Span(decision, className=DECISION_CHIP[decision]),
        ], style={"display": "flex", "alignItems": "center", "gap": "8px",
                  "marginBottom": "8px"}),
        html.Div(node_id, style={"color": "var(--muted)", "fontSize": "11px",
                                 "marginBottom": "8px"}),
        html.Div([e for k, v in props
                  for e in (html.Div(k, className="k"), html.Div(str(v)))],
                 className="prop-grid"),
        html.H5("Risk decomposition (Stage E)", className="section"),
        html.Div(_risk_parts_bars(a)),
        html.H5("Top %d riskiest counterparties" % config.TOP_COUNTERPARTIES,
                className="section"),
        html.Div([html.Div([
            html.Span("%d." % (i + 1), className="rank"),
            html.Span(ego.nodes[m].get("name") or _short(m), style={"flex": "1"}),
            html.Span("%.3f" % ego.nodes[m].get("final_risk", 0.0),
                      style={"color": "var(--muted)"}),
            html.Span(ego.nodes[m].get("decision", ""),
                      className=DECISION_CHIP.get(ego.nodes[m].get("decision"), ""),
                      style={"fontSize": "10px", "padding": "1px 8px"}),
        ], className="cpty-item") for i, m in enumerate(top)]),
    ])


def _drivers_card(ev):
    drivers = ev["top_drivers"]
    max_mag = max((d["magnitude"] for d in drivers), default=1.0) or 1.0
    rows = [html.Div([
        html.Div(d["feature"], className="bar-label", title=d["feature"]),
        html.Div(html.Div(className="bar-fill",
                          style={"width": "%d%%" % round(100 * d["magnitude"] / max_mag),
                                 "background": "var(--risk-red)" if d["direction"] == "+"
                                 else "var(--ok-green)"}),
                 className="bar-track"),
        html.Div("%+.3f" % (d["magnitude"] if d["direction"] == "+" else -d["magnitude"]),
                 className="bar-val"),
    ], className="bar-row") for d in drivers]
    reasons = html.Ul([html.Li(r, style={"fontSize": "11.5px", "color": "var(--muted)"})
                       for r in ev["decision_reasons"]],
                      style={"margin": "6px 0 0", "paddingLeft": "18px"})
    return [html.H4("Why — top risk drivers", className="section"),
            html.Div(rows), html.H5("Decision rationale", className="section"), reasons]


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
                       style={"color": "var(--muted)"})], className="path-item"))
    if not items:
        items = [html.Div("No propagated-risk paths in this neighbourhood.",
                          style={"color": "var(--muted)", "fontSize": "12px"})]
    shared = [html.Div("%s — %s (%s)" % (l["counterparty"], l["kind"], l["value"]),
                       className="path-item") for l in ev["shared_attribute_links"]]
    return [html.H4("Key risk paths", className="section"), html.Div(items),
            html.H5("Shared-attribute links to subject", className="section"),
            html.Div(shared or [html.Div("None found.",
                                         style={"color": "var(--muted)",
                                                "fontSize": "12px"})])]


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
             "color": "var(--risk-yellow)", "fontWeight": "650"},
            {"if": {"filter_query": "{final_risk} >= 0.5", "column_id": "final_risk"},
             "color": "var(--risk-red)", "fontWeight": "650"},
            {"if": {"filter_query": '{alerted} = "yes"', "column_id": "alerted"},
             "color": "var(--risk-red)"},
        ],
    )


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
    html.Span([html.Span(className="line"), "transaction (→ money flow)"]),
    html.Span([html.Span(className="line dashed"), "identity link"]),
    html.Span("size = final risk"),
], className="legend")


# ------------------------------------------------------------------- app
app = dash.Dash(__name__, title="Network Intelligence — Counterparty Risk")

_case_options = [
    {"label": "Case %d — %s (%s)" % (c, RUN["results"][c]["evidence"]["subject_name"],
                                     RUN["results"][c]["evidence"]["lob"] or "?"),
     "value": c} for c in CASES]

app.layout = html.Div(id="root", className="theme-light", children=[
    html.Div([
        html.H2(["Counterparty Network Risk",
                 html.Span("staged scorecard · %s propagation"
                           % config.PROP_METHOD.upper(), className="sub")]),
        dcc.Dropdown(id="case", options=_case_options, value=CASES[0],
                     clearable=False, style={"width": "340px"}),
        html.Button("☾ / ☀", id="theme-btn", className="btn", n_clicks=0),
    ], className="header"),
    html.Div(id="kpi-row", className="kpi-row"),
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
                          dcc.Slider(id="min-risk", min=0, max=1, step=0.05, value=0.0,
                                     marks={0: "0", 0.5: "0.5", 1: "1"})],
                         style={"width": "130px"}),
                dcc.Checklist(id="edge-kinds",
                              options=[{"label": " transactions", "value": "txn"},
                                       {"label": " identity links", "value": "identity"}],
                              value=["txn", "identity"], inline=True),
                dcc.RadioItems(id="layout-mode",
                               options=[{"label": " live physics", "value": "live"},
                                        {"label": " force", "value": "force"},
                                        {"label": " rings by hop", "value": "rings"}],
                               value="live", inline=True),
                dcc.Checklist(id="highlight-path",
                              options=[{"label": " highlight key risk path",
                                        "value": "on"}], value=[], inline=True),
                html.Button("◎ Center subject", id="center-btn", className="btn",
                            n_clicks=0),
            ], className="controls"),
            cyto.Cytoscape(id="graph", layout=_layout_spec("force", ""),
                           style={"width": "100%", "height": "500px"},
                           elements=[], stylesheet=_stylesheet("light"),
                           responsive=True),
            LEGEND,
        ], className="card card-graph"),
        html.Div([
            html.Div([
                dcc.Dropdown(id="node-search", placeholder="Search entity…",
                             style={"flex": "1"}),
                html.Button("Expand next hop", id="expand-btn", className="btn",
                            n_clicks=0),
            ], style={"display": "flex", "gap": "8px", "marginBottom": "10px",
                      "alignItems": "center"}),
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
                html.Button("Download evidence pack (.json)", id="dl-evidence-btn",
                            className="btn", n_clicks=0),
                html.Button("Download conclusion prompt (.md)", id="dl-prompt-btn",
                            className="btn", n_clicks=0),
            ], style={"display": "flex", "gap": "8px", "marginTop": "12px"}),
        ], className="card"),
    ], className="cards-row", style={"marginTop": "12px"}),
    html.Div([
        html.H4("Counterparties (ranked by risk)", className="section"),
        dash_table.DataTable(id="cpty-table", sort_action="native",
                             filter_action="native", page_size=12,
                             export_format="csv", **_table_styles()),
    ], className="card", style={"marginTop": "12px"}),
    dcc.Store(id="theme-store", data="light"),
    dcc.Store(id="focus-store"),
    dcc.Store(id="expanded-store", data=[]),
    dcc.Store(id="center-op"),
    dcc.Store(id="fit-op"),
    dcc.Download(id="download"),
])

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

# On every element change: (a) fit once so new nodes are never off-screen,
# (b) attach — once per cytoscape instance — the Obsidian-style "spring on
# release" handler: dropping a dragged node re-runs the animated physics so
# the neighbourhood re-settles around it. Only active in "live" layout mode.
_JS_SPRING = json.dumps(dict(_LIVE_PHYSICS, fit=False))
app.clientside_callback(
    """
    function(elements) {
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
    Output("fit-op", "data"), Input("graph", "elements"),
)


@app.callback(Output("theme-store", "data"), Input("theme-btn", "n_clicks"))
def _toggle_theme(n):
    return "dark" if (n or 0) % 2 else "light"


@app.callback(
    Output("focus-store", "data"),
    Input("graph", "tapNodeData"), Input("node-search", "value"),
    Input("case", "value"),
)
def _focus(tap, search, case_id):
    trigger = dash.callback_context.triggered_id
    if trigger == "graph" and tap:
        return tap["id"]
    if trigger == "node-search" and search:
        return search
    return RUN["results"][case_id]["evidence"]["subject_id"]


@app.callback(
    Output("expanded-store", "data"),
    Input("expand-btn", "n_clicks"), Input("case", "value"),
    State("focus-store", "data"), State("expanded-store", "data"),
)
def _expand(n, case_id, focus, expanded):
    if dash.callback_context.triggered_id != "expand-btn" or not focus:
        return []  # reset on case change / initial load
    ego = RUN["results"][case_id]["ego"]
    if focus not in ego.nodes:
        return expanded or []
    nbrs = set(ego.successors(focus)) | set(ego.predecessors(focus))
    return sorted(set(expanded or []) | nbrs | {focus})


@app.callback(
    Output("graph", "elements"), Output("graph", "stylesheet"),
    Output("graph", "layout"),
    Output("cpty-table", "data"), Output("cpty-table", "columns"),
    Output("kpi-row", "children"), Output("side-panel", "children"),
    Output("drivers-card", "children"), Output("paths-card-content", "children"),
    Output("node-search", "options"), Output("root", "className"),
    Input("case", "value"), Input("depth", "value"), Input("min-risk", "value"),
    Input("edge-kinds", "value"), Input("layout-mode", "value"),
    Input("highlight-path", "value"), Input("theme-store", "data"),
    Input("focus-store", "data"), Input("expanded-store", "data"),
)
def _render(case_id, depth, min_risk, edge_kinds, layout_mode, highlight,
            theme, focus, expanded):
    r = RUN["results"][case_id]
    ev = r["evidence"]
    focus = focus if focus in r["ego"].nodes else ev["subject_id"]
    els = _elements(case_id, depth, min_risk, edge_kinds or [], expanded,
                    highlight=bool(highlight), focus=focus)
    frame = _counterparty_frame(case_id)
    search_opts = [{"label": "%s (%s)" % (a.get("name") or _short(n), _short(n)),
                    "value": n}
                   for n, a in sorted(r["ego"].nodes(data=True),
                                      key=lambda t: t[1].get("final_risk", 0),
                                      reverse=True)]
    return (els, _stylesheet(theme), _layout_spec(layout_mode, ev["subject_id"]),
            frame.to_dict("records"),
            [{"name": c, "id": c} for c in frame.columns],
            _kpis(ev), _node_panel(case_id, focus),
            _drivers_card(ev), _paths_card(ev), search_opts,
            "theme-%s" % theme)


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
