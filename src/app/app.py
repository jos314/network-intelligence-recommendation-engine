"""P4 — the analyst screen (§6): Obsidian-style force-directed ego-graph,
one subject at a time, plus the ranked counterparty table.

Encodings (per the build plan):
  * node SIZE   = final_risk (bigger = riskier)
  * node COLOUR = decision (red = SAR, yellow = EDD, neutral = No action)
  * ring/border = TM-alerted; diamond = the case subject (pinned focus)
  * solid edge  = transaction (arrow along money flow); dashed = identity link

Overwhelm control: default render = subject + level 1; depth slider (1-3),
min-risk filter, edge-family toggles; click a node -> side panel with its
properties and its top-20 riskiest counterparties.

Run:  .venv/bin/python -m src.app.app   (then open http://127.0.0.1:8050)
"""
import dash
import dash_cytoscape as cyto
import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from .. import config
from ..pipeline import run_all_cases

# ---------------------------------------------------------------- palette
THEMES = {
    "light": {"bg": "#fafafa", "panel": "#ffffff", "text": "#1f2430",
              "muted": "#7a8194", "edge": "#c9cdd6", "node": "#9aa3b2"},
    "dark": {"bg": "#14161c", "panel": "#1e222b", "text": "#e8eaf0",
             "muted": "#8b93a7", "edge": "#3a4150", "node": "#5d6675"},
}
RISK_RED = "#d64545"     # SAR
RISK_YELLOW = "#e6b23c"  # EDD
ACCENT = "#4c7fd6"


def _stylesheet(theme):
    t = THEMES[theme]
    return [
        {"selector": "node", "style": {
            "width": "mapData(risk, 0, 1, 18, 62)",
            "height": "mapData(risk, 0, 1, 18, 62)",
            "background-color": t["node"],
            "label": "data(label)", "font-size": "9px",
            "color": t["text"], "text-valign": "bottom", "text-margin-y": "4px",
            "border-width": 0,
        }},
        {"selector": 'node[decision = "EDD"]', "style": {"background-color": RISK_YELLOW}},
        {"selector": 'node[decision = "SAR"]', "style": {"background-color": RISK_RED}},
        {"selector": "node[?alerted]", "style": {"border-width": 3, "border-color": RISK_RED}},
        {"selector": "node[?is_seed]", "style": {"shape": "diamond", "border-width": 3,
                                                 "border-color": ACCENT}},
        {"selector": "edge", "style": {
            "width": "mapData(weight, 0, 1, 1, 5)", "line-color": t["edge"],
            "curve-style": "bezier",
        }},
        {"selector": 'edge[kind = "txn"]', "style": {
            "target-arrow-shape": "triangle", "target-arrow-color": t["edge"],
        }},
        {"selector": 'edge[kind != "txn"]', "style": {"line-style": "dashed",
                                                      "line-color": ACCENT}},
        {"selector": "node:selected", "style": {"border-width": 4, "border-color": ACCENT}},
    ]


# ------------------------------------------------------------ data (once)
RUN = run_all_cases(verbose=False)
CASES = sorted(RUN["results"].keys())


def _elements(case_id, depth, min_risk, edge_kinds):
    ego = RUN["results"][case_id]["ego"]
    seed = ego.graph["seed"]
    visible = {n for n, a in ego.nodes(data=True)
               if a.get("hop", 99) <= depth
               and (a.get("final_risk", 0.0) >= min_risk or n == seed)}
    els = []
    for n in visible:
        a = ego.nodes[n]
        els.append({"data": {
            "id": n, "label": (a.get("name") or n)[:22],
            "risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision", config.DECISION_NO_ACTION),
            "alerted": bool(a.get("alerted")), "is_seed": n == seed,
            "hop": a.get("hop"),
        }})
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
        weight = min((d.get("total_amount_base", 0) / 500_000.0) if kind == "txn"
                     else d.get("weight", 0.5), 1.0)
        els.append({"data": {"source": u, "target": v, "kind": kind,
                             "weight": round(weight, 3)}})
    return els


def _counterparty_frame(case_id) -> pd.DataFrame:
    ego = RUN["results"][case_id]["ego"]
    seed = ego.graph["seed"]
    rows = []
    for n, a in ego.nodes(data=True):
        if n == seed:
            continue
        rows.append({
            "entity": a.get("name") or n, "id": n, "hop": a.get("hop"),
            "final_risk": round(a.get("final_risk", 0.0), 3),
            "decision": a.get("decision"),
            "alerted": "yes" if a.get("alerted") else "",
            "volume_share_%": round(100 * a.get("rel_components", {}).get("volume_share", 0.0), 1),
            "shared_attrs": ", ".join(a.get("rel_shared_kinds", [])),
        })
    return pd.DataFrame(rows).sort_values("final_risk", ascending=False)


def _node_panel(case_id, node_id, theme):
    t = THEMES[theme]
    ego = RUN["results"][case_id]["ego"]
    if node_id not in ego.nodes:
        return html.Div("Click a node to inspect it.", style={"color": t["muted"]})
    a = ego.nodes[node_id]
    props = [
        ("Name", a.get("name") or "—"), ("Type", a.get("node_type")),
        ("Hop", a.get("hop")), ("Country", a.get("country") or "—"),
        ("Country risk", a.get("country_risk") or "—"),
        ("PEP", a.get("pep_flag") or "—"), ("CRR", a.get("crr") or "—"),
        ("Alerted", "yes" if a.get("alerted") else "no"),
        ("Accounts", len(a.get("accounts", []))),
        ("Final risk", "%.3f" % a.get("final_risk", 0.0)),
        ("Decision", a.get("decision")),
    ]
    parts = a.get("risk_parts", {})
    # top-N riskiest counterparties of the CLICKED node
    nbrs = set(ego.successors(node_id)) | set(ego.predecessors(node_id))
    top = sorted(nbrs, key=lambda m: ego.nodes[m].get("final_risk", 0.0),
                 reverse=True)[:config.TOP_COUNTERPARTIES]
    return html.Div([
        html.H4(node_id, style={"margin": "0 0 8px", "color": t["text"]}),
        html.Table([html.Tr([html.Td(k, style={"color": t["muted"], "paddingRight": "10px"}),
                             html.Td(str(v), style={"color": t["text"]})]) for k, v in props],
                   style={"fontSize": "12px"}),
        html.Div("Risk parts — base %.2f | rel %.2f | prop %.2f | struct %.2f"
                 % (parts.get("base", 0), parts.get("rel", 0), parts.get("prop", 0),
                    parts.get("struct", 0)),
                 style={"fontSize": "11px", "color": t["muted"], "margin": "8px 0"}),
        html.H5("Top %d riskiest counterparties" % config.TOP_COUNTERPARTIES,
                style={"margin": "10px 0 4px", "color": t["text"]}),
        html.Ol([html.Li("%s — %.3f (%s)" % (ego.nodes[m].get("name") or m,
                                             ego.nodes[m].get("final_risk", 0.0),
                                             ego.nodes[m].get("decision")),
                         style={"fontSize": "12px", "color": t["text"]}) for m in top]),
    ])


# ------------------------------------------------------------------- app
app = dash.Dash(__name__, title="Network Intelligence — Counterparty Risk")


def _layout():
    t = THEMES["light"]
    return html.Div(id="root", children=[
        html.Div([
            html.H2("Counterparty Network Risk", id="title",
                    style={"margin": "0", "flex": "1"}),
            html.Label("Case"),
            dcc.Dropdown(id="case", options=[
                {"label": "Case %d — %s (%s)" % (c, RUN["results"][c]["evidence"]["subject_name"],
                                                 RUN["results"][c]["evidence"]["lob"] or "?"),
                 "value": c} for c in CASES], value=CASES[0], clearable=False,
                style={"width": "320px"}),
            html.Button("Dark / light", id="theme-btn", n_clicks=0,
                        style={"marginLeft": "12px"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "10px",
                  "padding": "12px 16px"}),
        html.Div(id="case-banner", style={"padding": "0 16px 8px", "fontSize": "13px"}),
        html.Div([
            html.Div([
                html.Div([
                    html.Label("Depth"),
                    dcc.Slider(id="depth", min=1, max=config.EGO_DEPTH_VIEW, step=1,
                               value=1, marks={i: str(i) for i in range(1, config.EGO_DEPTH_VIEW + 1)}),
                    html.Label("Min risk"),
                    dcc.Slider(id="min-risk", min=0, max=1, step=0.05, value=0.0,
                               marks={0: "0", 0.5: "0.5", 1: "1"}),
                    dcc.Checklist(id="edge-kinds",
                                  options=[{"label": " transactions", "value": "txn"},
                                           {"label": " identity links", "value": "identity"}],
                                  value=["txn", "identity"], inline=True),
                ], style={"padding": "0 16px", "fontSize": "12px"}),
                cyto.Cytoscape(id="graph", layout={"name": "cose", "animate": False},
                               style={"width": "100%", "height": "520px"},
                               elements=[], stylesheet=_stylesheet("light")),
            ], style={"flex": "2", "minWidth": "0"}),
            html.Div(id="side-panel",
                     style={"flex": "1", "padding": "12px 16px", "overflowY": "auto",
                            "maxHeight": "600px"}),
        ], style={"display": "flex", "gap": "8px"}),
        html.H4("Counterparties (ranked by risk)", style={"padding": "0 16px"}),
        html.Div([dash_table.DataTable(
            id="cpty-table", sort_action="native", page_size=12,
            style_table={"overflowX": "auto"},
            style_cell={"fontSize": "12px", "fontFamily": "system-ui",
                        "padding": "6px", "textAlign": "left"},
        )], style={"padding": "0 16px 24px"}),
        dcc.Store(id="theme-store", data="light"),
    ], style={"backgroundColor": t["bg"], "color": t["text"],
              "fontFamily": "system-ui", "minHeight": "100vh"})


app.layout = _layout()


@app.callback(Output("theme-store", "data"), Input("theme-btn", "n_clicks"))
def _toggle_theme(n):
    return "dark" if (n or 0) % 2 else "light"


@app.callback(
    Output("graph", "elements"), Output("graph", "stylesheet"),
    Output("cpty-table", "data"), Output("cpty-table", "columns"),
    Output("case-banner", "children"), Output("root", "style"),
    Output("side-panel", "children"),
    Input("case", "value"), Input("depth", "value"), Input("min-risk", "value"),
    Input("edge-kinds", "value"), Input("theme-store", "data"),
    Input("graph", "tapNodeData"),
)
def _render(case_id, depth, min_risk, edge_kinds, theme, tap):
    t = THEMES[theme]
    els = _elements(case_id, depth, min_risk, edge_kinds or [])
    frame = _counterparty_frame(case_id)
    ev = RUN["results"][case_id]["evidence"]
    banner = "Decision: %s | calibrated p=%.2f | %s | prompt file: %s" % (
        ev["decision"], ev["calibrated_score"],
        "; ".join(ev["decision_reasons"][:2]),
        RUN["results"][case_id]["prompt_path"].name)
    root_style = {"backgroundColor": t["bg"], "color": t["text"],
                  "fontFamily": "system-ui", "minHeight": "100vh"}
    node_id = tap["id"] if tap else ev["subject_id"]
    panel = _node_panel(case_id, node_id, theme)
    columns = [{"name": c, "id": c} for c in frame.columns]
    return (els, _stylesheet(theme), frame.to_dict("records"), columns,
            banner, root_style, panel)


if __name__ == "__main__":
    app.run(debug=False, port=8050)
