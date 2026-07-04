"""P4 acceptance — the analyst screen renders every case with the plan's
encodings, and the UX-critique guarantees hold: evidence is never silently
filtered, screenings that did not run never read as 0, edges aggregate and
carry amounts/dates, and the table exposes flow columns."""
import pytest

from src import config
from src.app.app import (CALIBRATION, CASES, RUN, _counterparty_frame,
                         _elements, _kpis, _layout_spec, _render)


def _render_case(case_id, **over):
    args = dict(depth=config.EGO_DEPTH_VIEW, min_risk=0.0,
                edge_kinds=["txn", "identity"], layout_mode="force",
                highlight=[], theme="dark", expanded=[])
    args.update(over)
    return _render(case_id, args["depth"], args["min_risk"], args["edge_kinds"],
                   args["layout_mode"], args["highlight"], args["theme"],
                   args["expanded"])


@pytest.mark.parametrize("case_id", CASES)
def test_render_all_cases(case_id):
    (els, style, layout, data, cols, kpis, reasons, caption, drivers, paths,
     opts, theme, view_sig, filter_q, sort_by, search_val) = _render_case(case_id)
    nodes = [e for e in els if "source" not in e["data"]]
    edges = [e for e in els if "source" in e["data"]]
    assert nodes and edges and data and cols and kpis and reasons and caption
    seeds = [n for n in nodes if n["data"]["is_seed"]]
    assert len(seeds) == 1
    assert theme == "theme-dark"
    assert all("risk" in n["data"] and "decision" in n["data"] for n in nodes)
    assert view_sig  # camera-stability signature present


def test_depth_filter_reduces_elements():
    full, _ = _elements(1, config.EGO_DEPTH_VIEW, 0.0, ["txn", "identity"])
    lvl1, _ = _elements(1, 1, 0.0, ["txn", "identity"])
    assert len(lvl1) <= len(full)


def test_edge_family_toggle():
    only_txn, _ = _elements(3, 3, 0.0, ["txn"])
    kinds = {e["data"]["kind"] for e in only_txn if "source" in e["data"]}
    assert kinds <= {"txn"}


def test_key_path_highlight_marks_elements():
    els, _ = _elements(3, 3, 0.0, ["txn", "identity"], highlight=True)
    assert any("onpath" in e.get("classes", "") for e in els)


def test_highlighted_path_is_never_truncated_by_filters():
    # depth 1 + aggressive min-risk: the key path must still render whole
    els, stats = _elements(3, 1, 0.9, ["txn", "identity"], highlight=True)
    ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
    from src.app.app import _top_path_members
    path_nodes, _ = _top_path_members(3)
    assert path_nodes <= ids


def test_alerted_nodes_exempt_from_min_risk_cut():
    for cid in CASES:
        ego = RUN["results"][cid]["ego"]
        alerted_l1 = [n for n, a in ego.nodes(data=True)
                      if a.get("alerted") and a.get("hop") == 1]
        if not alerted_l1:
            continue
        els, _ = _elements(cid, 1, 0.99, ["txn", "identity"])
        ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
        assert set(alerted_l1) <= ids
        return
    pytest.skip("no alerted hop-1 node in fixture")


def test_stats_disclose_filtering():
    _, stats = _elements(1, 1, 0.0, ["txn", "identity"])
    assert stats["nodes_shown"] <= stats["nodes_total"]
    assert {"edges_shown", "edges_total", "hidden_alerted",
            "hidden_flagged", "path_revealed"} <= set(stats)


def test_parallel_txn_edges_aggregate():
    # any (u, v) pair appears at most once per kind in the drawn elements,
    # and txn edges carry amount/count/date fields for the edge inspector
    for cid in CASES:
        els, _ = _elements(cid, config.EGO_DEPTH_SCORE, 0.0, ["txn", "identity"])
        seen = set()
        for e in els:
            if "source" not in e["data"]:
                continue
            key = (e["data"]["source"], e["data"]["target"], e["data"]["kind"])
            assert key not in seen, "duplicate drawn edge %r" % (key,)
            seen.add(key)
            if e["data"]["kind"] == "txn":
                assert {"amount", "count", "first", "last"} <= set(e["data"])


def test_expansion_reveals_nodes_beyond_depth():
    ego = RUN["results"][1]["ego"]
    beyond = [n for n, a in ego.nodes(data=True) if a.get("hop", 0) >= 2]
    if not beyond:
        pytest.skip("ego has no depth-2 nodes")
    els, _ = _elements(1, 1, 0.0, ["txn", "identity"], expanded=set(beyond))
    ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
    assert set(beyond) <= ids


def test_layout_modes_and_identity_cache():
    live = _layout_spec("live", "X")
    assert live["name"] == "cose" and live["animate"] and not live["randomize"]
    rings = _layout_spec("rings", "X")
    assert rings["name"] == "breadthfirst" and 'id = "X"' in rings["roots"]
    assert _layout_spec("force", "X")["name"] == "cose"
    # unchanged (mode, seed) must return the SAME object, or cytoscape
    # re-springs the graph on every render (camera-stability regression)
    assert _layout_spec("live", "X") is live


def test_edge_weights_data_driven():
    for cid in CASES:
        els, _ = _elements(cid, config.EGO_DEPTH_SCORE, 0.0, ["txn", "identity"])
        txn_w = [e["data"]["weight"] for e in els
                 if "source" in e["data"] and e["data"]["kind"] == "txn"]
        if txn_w:
            assert max(txn_w) == 1.0
            assert all(0.0 <= w <= 1.0 for w in txn_w)


def test_counterparty_frame_ranked_with_flow_columns():
    frame = _counterparty_frame(4)
    risks = frame["final_risk"].tolist()
    assert risks == sorted(risks, reverse=True)
    assert {"entity", "id", "decision", "alerted"} <= set(frame.columns)
    assert {"type", "total_amount", "txn_count", "first_seen", "last_seen",
            "case_id"} <= set(frame.columns)
    # volume share is only defined for direct counterparties
    beyond_l1 = frame[frame["hop"] > 1]
    if len(beyond_l1):
        assert beyond_l1["volume_share_%"].isna().all()


def test_kpis_watchlist_honesty_and_calibration_disclosure():
    ev = RUN["results"][1]["evidence"]
    rendered = str(_kpis(1, ev))
    if not config.WATCHLIST_CONNECTED:
        assert "not screened" in rendered
        assert "Sanctioned / watchlist" in rendered
    if not CALIBRATION.get("calibrated"):
        assert "uncalibrated" in rendered
    # threshold ticks are config-driven
    assert "t1 = %.2f" % config.DECISION_T1 in rendered
    assert "t2 = %.2f" % config.DECISION_T2 in rendered
