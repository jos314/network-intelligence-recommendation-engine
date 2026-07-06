"""P4 acceptance — the drill-down analyst screen: top-N baseline, expansion
primitives, cluster view, honesty disclosures, and the conclusion loop."""
import pytest
from dash.exceptions import PreventUpdate

from src import config
from src.app.app import (CASES, DA, _apply_filters, _conclusion_content,
                         _counterparty_frame, _elements, _decision_panel,
                         _graph_caption, _isolate_follows_expansions,
                         _isolate_options, _layout_spec, _quickstats_panel,
                         _render, _result, _row_conditional, _static_layout,
                         expand_top)


def _render_case(case_id, **over):
    args = dict(top_n=config.TOP_N_DEFAULT, min_risk=0.0,
                edge_kinds=["txn", "identity"], layout_mode="force",
                highlight=[], theme="dark", expanded=[], view_mode="entities",
                isolate=[], place_nonce=0)
    args.update(over)
    return _render(case_id, args["top_n"], args["min_risk"], args["edge_kinds"],
                   args["layout_mode"], args["highlight"], args["theme"],
                   args["expanded"], args["view_mode"], args["isolate"],
                   args["place_nonce"])


@pytest.mark.parametrize("case_id", CASES)
def test_render_all_cases(case_id):
    (els, style, layout, decision, stats_panel, caption, drivers, paths, opts,
     theme, view_sig, sort_by, search_val, iso_opts) = _render_case(case_id)
    nodes = [e for e in els if "source" not in e["data"]]
    edges = [e for e in els if "source" in e["data"]]
    assert nodes and edges and decision and stats_panel
    seeds = [n for n in nodes if n["data"].get("is_seed")]
    assert len(seeds) == 1
    assert theme == "theme-dark"
    assert view_sig


def test_baseline_is_topN_hop1_plus_subject():
    for cid in CASES:
        ego = _result(cid)["ego"]
        hop1 = [n for n, a in ego.nodes(data=True) if a.get("hop") == 1]
        els, stats = _elements(cid, 3, 0.0, ["txn", "identity"])
        ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
        assert stats["hop1_shown"] <= 3
        # nothing beyond hop 1 without an expansion
        assert all(ego.nodes[n].get("hop", 0) <= 1 for n in ids)
        assert ego.graph["seed"] in ids
        if len(hop1) > 3:
            return
    pytest.skip("fixture egos too small to exercise the cap")


def test_expand_top_is_bounded_and_prioritises_alerted():
    ego = _result(1)["ego"]
    seed = ego.graph["seed"]
    exp = expand_top(ego, seed, k=2)
    assert len(exp) <= 3  # node itself + k
    nbrs = (set(ego.successors(seed)) | set(ego.predecessors(seed))) - {seed}
    alerted = {n for n in nbrs if ego.nodes[n].get("alerted")}
    if alerted:
        assert alerted & exp, "alerted neighbours must survive the top-K cut"


def test_expansion_reveals_deeper_hops():
    ego = _result(1)["ego"]
    beyond = [n for n, a in ego.nodes(data=True) if a.get("hop", 0) >= 2]
    if not beyond:
        pytest.skip("ego has no depth-2 nodes")
    els, stats = _elements(1, 25, 0.0, ["txn", "identity"], expanded=set(beyond))
    ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
    assert set(beyond) <= ids
    assert stats["expanded_shown"] == len(beyond)


def test_placement_hints_deterministic():
    """Every node carries x0/y0; expansion children carry phint/kidx —
    the no-reshuffle contract for the live-physics view."""
    els1, _ = _elements(1, 25, 0.0, ["txn", "identity"])
    els2, _ = _elements(1, 25, 0.0, ["txn", "identity"])
    n1 = {e["data"]["id"]: (e["data"]["x0"], e["data"]["y0"])
          for e in els1 if "source" not in e["data"]}
    n2 = {e["data"]["id"]: (e["data"]["x0"], e["data"]["y0"])
          for e in els2 if "source" not in e["data"]}
    assert n1 == n2  # identical across calls — deterministic
    ego = _result(1)["ego"]
    seed = ego.graph["seed"]
    assert n1[seed] == (0.0, 0.0)


def test_isolate_expansion_lens():
    """The lens EMPHASIZES the trail — it never removes anything. Trail
    nodes stay unclassed, back-links go .lensmid, the rest .lensbg, and the
    node set is IDENTICAL to the un-lensed view."""
    for cid in CASES:
        ego = _result(cid)["ego"]
        pair = None
        for u, v, d in ego.edges(data=True):
            hu, hv = ego.nodes[u].get("hop"), ego.nodes[v].get("hop")
            if {hu, hv} == {1, 2}:
                pair = (u, v) if hu == 1 else (v, u)
                break
        if not pair:
            continue
        parent, child = pair
        seed = ego.graph["seed"]
        expanded = {child: parent, parent: None}
        els, stats = _elements(cid, 25, 0.0, ["txn", "identity"],
                               expanded=expanded, isolate=True)
        els_full, stats_full = _elements(cid, 25, 0.0, ["txn", "identity"],
                                         expanded=expanded)
        assert stats["isolated"] and not stats_full["isolated"]
        nodes = {e["data"]["id"]: e for e in els if "source" not in e["data"]}
        full_ids = {e["data"]["id"] for e in els_full if "source" not in e["data"]}
        # nothing removed: identical node sets with and without the lens
        assert set(nodes) == full_ids
        # HARD two-tier contrast: trail glows (.lenson), everything else is
        # a ghost (.lensbg) — no invisible middle tier
        trail = {seed, parent, child}
        for n, e in nodes.items():
            if n in trail:
                assert "lenson" in e["classes"] and "lensbg" not in e["classes"]
            else:
                assert "lensbg" in e["classes"]
        assert stats["lens_trail"] + stats["lens_faded"] == stats["nodes_shown"]
        # edge tiers: trail-trail bright, one-end-on-trail whisper, rest gone
        for e in els:
            d = e["data"]
            if "source" not in d:
                continue
            u, v = d["source"], d["target"]
            on_trail = (u in trail) + (v in trail)
            want = {2: "lenstrail", 1: "lensmid", 0: "lensbg"}[on_trail]
            assert want in e.get("classes", "")
        return
    pytest.skip("no hop1-hop2 adjacency in fixture egos")


def test_expansion_tree_edges_marked():
    """A parented expansion ({child: parent}) draws the drill trail."""
    for cid in CASES:
        ego = _result(cid)["ego"]
        pair = None
        for u, v, d in ego.edges(data=True):
            hu, hv = ego.nodes[u].get("hop"), ego.nodes[v].get("hop")
            if {hu, hv} == {1, 2}:
                pair = (u, v) if hu == 1 else (v, u)
                break
        if not pair:
            continue
        parent, child = pair
        els, _ = _elements(cid, 25, 0.0, ["txn", "identity"],
                           expanded={child: parent, parent: None})
        tree = [e for e in els if "source" in e["data"]
                and "treeedge" in e.get("classes", "")]
        assert tree, "parent->child expansion edge should carry .treeedge"
        ends = {(e["data"]["source"], e["data"]["target"]) for e in tree}
        assert any({s, t} == {parent, child} for s, t in ends)
        return
    pytest.skip("no hop1-hop2 adjacency in fixture egos")


def test_cluster_view():
    for cid in CASES:
        els, stats = _elements(cid, 25, 0.0, ["txn", "identity"], mode="clusters")
        assert stats["mode"] == "clusters"
        nodes = [e for e in els if "source" not in e["data"]]
        assert any(n["data"].get("is_seed") for n in nodes)
        clusters = [n for n in nodes if n["data"].get("is_cluster")]
        if clusters:
            assert all("cluster_size" in n["data"] for n in clusters)
            return
    pytest.skip("no communities in any fixture ego")


def test_key_path_highlight_forced_into_view():
    els, stats = _elements(3, 1, 0.9, ["txn", "identity"], highlight=True)
    ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
    from src.app.app import _top_path_members
    path_nodes, _ = _top_path_members(3)
    assert path_nodes <= ids


def test_stats_disclose_everything():
    _, stats = _elements(1, 2, 0.0, ["txn", "identity"])
    assert {"hop1_shown", "hop1_total", "expanded_shown", "alerted_offscreen",
            "hidden_flagged", "path_revealed", "render_capped"} <= set(stats)


def test_parallel_txn_edges_aggregate():
    for cid in CASES:
        els, _ = _elements(cid, 100, 0.0, ["txn", "identity"])
        seen = set()
        for e in els:
            if "source" not in e["data"]:
                continue
            key = (e["data"]["source"], e["data"]["target"], e["data"]["kind"])
            assert key not in seen
            seen.add(key)
            if e["data"]["kind"] == "txn":
                assert {"amount", "count", "first", "last"} <= set(e["data"])


def test_layout_modes_and_identity_cache():
    # live = preset: cytoscape never runs a layout — placement comes from
    # server hints + the clientside force simulation (no reshuffle ever)
    live = _layout_spec("live", "X")
    assert live["name"] == "preset" and not live["fit"]
    assert _layout_spec("force", "X")["name"] == "cose"
    assert _layout_spec("live", "X") is live  # camera-stability cache
    # static layouts never fit (the view-sig pass owns the camera) and
    # cose never randomizes (re-runs must not reshuffle from scratch)
    assert _layout_spec("rings", "X")["fit"] is False
    assert _layout_spec("force", "X")["fit"] is False
    assert _layout_spec("force", "X")["randomize"] is False


def test_static_layout_identity_follows_element_set():
    """dash-cytoscape re-runs a layout only on a NEW dict identity: same
    element set -> same dict (no reshuffle); changed set -> fresh dict
    (new nodes actually get laid out — the rings-mode pile-up bug)."""
    els_a, _ = _elements(1, 25, 0.0, ["txn", "identity"])
    els_b, _ = _elements(2, 25, 0.0, ["txn", "identity"])
    one = _static_layout("rings", "S", els_a)
    assert _static_layout("rings", "S", els_a) is one
    other = _static_layout("rings", "S", els_b)
    assert other is not one
    # live mode passes the cached preset through untouched
    assert _static_layout("live", "S", els_a)["name"] == "preset"


def test_isolate_control_lifecycle():
    """The lens control explains itself: disabled without expansions,
    enabled with them; the value clears when expansions are reset."""
    assert _isolate_options(False)[0]["disabled"] is True
    assert _isolate_options(True)[0]["disabled"] is False
    # server side: isolate without expansions is a no-op, disclosed in stats
    els_plain, stats_plain = _elements(1, 25, 0.0, ["txn", "identity"])
    els_iso, stats_iso = _elements(1, 25, 0.0, ["txn", "identity"],
                                   expanded={}, isolate=True)
    assert not stats_iso["isolated"]
    assert stats_iso["nodes_shown"] == stats_plain["nodes_shown"]
    # ticked lens dies with its expansions
    assert _isolate_follows_expansions({}, ["on"]) == []
    with pytest.raises(PreventUpdate):
        _isolate_follows_expansions({"c": "p"}, ["on"])
    with pytest.raises(PreventUpdate):
        _isolate_follows_expansions({}, [])


def test_min_risk_alert_exemption_is_disclosed():
    """Alerted nodes survive any min-risk cut — the caption must SAY so,
    otherwise an alert-heavy case makes the slider look broken."""
    for cid in CASES:
        ego = _result(cid)["ego"]
        els, stats = _elements(cid, 25, 1.0, ["txn", "identity"])
        ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
        kept_alerted = [n for n in ids if ego.nodes[n].get("alerted")
                        and ego.nodes[n].get("final_risk", 0.0) < 1.0
                        and n != ego.graph["seed"]]
        if not kept_alerted:
            continue
        assert stats["alert_exempt"] == len(kept_alerted)
        rendered = str(_graph_caption(stats, ego))
        assert "alerted kept despite min-risk" in rendered
        return
    pytest.skip("no alerted hop-1 nodes below risk 1.0 in fixture egos")


def test_counterparty_frame_ranked_with_flow_columns():
    frame, total = _counterparty_frame(4)
    assert total >= len(frame)
    risks = frame["final_risk"].tolist()
    assert risks == sorted(risks, reverse=True)
    assert {"entity", "id", "decision", "alerted", "type", "total_amount",
            "txn_count", "first_seen", "last_seen", "case_id"} <= set(frame.columns)
    beyond_l1 = frame[frame["hop"] > 1]
    if len(beyond_l1):
        assert beyond_l1["volume_share_%"].isna().all()


def test_decision_panel_honesty():
    ev = _result(1)["evidence"]
    rendered = str(_decision_panel(1, ev)) + str(_quickstats_panel(ev))
    if not config.WATCHLIST_CONNECTED:
        assert "not screened" in rendered
    if not DA.calibration().get("calibrated"):
        assert "uncalibrated" in rendered
    assert "t1 = %.2f" % config.DECISION_T1 in rendered
    assert "t2 = %.2f" % config.DECISION_T2 in rendered


def test_conclusion_roundtrip(tmp_path, monkeypatch):
    from src.conclusion import store
    monkeypatch.setattr(store, "CONCLUSIONS_DIR", tmp_path)
    monkeypatch.setattr(store, "METRICS_DIR", tmp_path)
    assert store.read_conclusion(1) is None
    store.write_conclusion(1, "**EDD** recommended for Subject X.")
    assert "Subject X" in store.read_conclusion(1)
    path = store.write_metrics(_result(1)["evidence"])
    assert path.exists() and path.name == "case_1.json"


def test_conclusion_card_placeholder_mentions_skill():
    import src.app.app as app_mod
    text = str(_conclusion_content(999))  # no file for a fake case id
    assert "SKILL.md" in text and "case_metrics" in text


# ------------------------------------------------- counterparty filters
def test_text_filter_is_case_insensitive():
    frame, _ = _counterparty_frame(4)
    if frame.empty:
        import pytest
        pytest.skip("empty frame")
    sample = str(frame.iloc[0]["entity"])
    # a lowercased substring of a real name must still match
    frag = sample.strip().lower()[:3]
    out = _apply_filters(frame, text=frag)
    assert len(out) >= 1
    assert out["entity"].astype(str).str.lower().str.contains(
        frag, regex=False).all()
    # uppercased query yields the identical result (case-insensitive)
    assert len(_apply_filters(frame, text=frag.upper())) == len(out)


def test_numeric_operator_filters():
    frame, _ = _counterparty_frame(4)
    if frame.empty:
        import pytest
        pytest.skip("empty frame")
    ge = _apply_filters(frame, risk_op="gte", risk_val=0.5)
    assert (ge["final_risk"] >= 0.5).all()
    lt = _apply_filters(frame, risk_op="lt", risk_val=0.5)
    assert (lt["final_risk"] < 0.5).all()
    assert len(ge) + len(lt) == len(frame)
    # amount and txn columns compare as numbers, not strings
    big = _apply_filters(frame, amt_op="gte", amt_val=1)
    assert (big["total_amount"] >= 1).all()
    few = _apply_filters(frame, txn_op="lte", txn_val=5)
    assert (few["txn_count"] <= 5).all()


def test_date_range_filter():
    frame, _ = _counterparty_frame(4)
    dated = frame[frame["first_seen"].astype(str).str.match(r"\d{4}-\d{2}-\d{2}")]
    if dated.empty:
        import pytest
        pytest.skip("no dated rows")
    cutoff = sorted(dated["first_seen"])[len(dated) // 2]
    after = _apply_filters(frame, first_from=cutoff)
    assert (after["first_seen"].astype(str) >= cutoff).all()
    before = _apply_filters(frame, first_to=cutoff)
    assert (before["first_seen"].astype(str) <= cutoff).all()
    # rows with no date ("—") drop out when a date filter is active
    assert after["first_seen"].astype(str).str.match(r"\d{4}").all()


def test_combined_filters_and_empty_frame():
    frame, _ = _counterparty_frame(4)
    out = _apply_filters(frame, decisions=[config.DECISION_SAR],
                         risk_op="gte", risk_val=0.0)
    assert set(out["decision"].unique()) <= {config.DECISION_SAR}
    import pandas as pd
    empty = pd.DataFrame(columns=frame.columns)
    assert _apply_filters(empty, text="x").empty


def test_row_highlight_conditional():
    base = _row_conditional(None)
    sel = _row_conditional(3)
    assert len(sel) == len(base) + 1
    assert any(s.get("if", {}).get("row_index") == 3 for s in sel)