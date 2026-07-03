"""P4 acceptance — the analyst screen renders every case with the plan's
encodings: size=risk, decision colours, seed diamond, filters, key-path
highlight, progressive expansion, ranked table."""
import pytest

from src import config
from src.app.app import (CASES, RUN, _counterparty_frame, _elements,
                         _layout_spec, _render)


@pytest.mark.parametrize("case_id", CASES)
def test_render_all_cases(case_id):
    (els, style, layout, data, cols, kpis, panel, drivers, paths, opts,
     theme) = _render(case_id, config.EGO_DEPTH_VIEW, 0.0,
                      ["txn", "identity"], "force", [], "dark", None, [])
    nodes = [e for e in els if "source" not in e["data"]]
    edges = [e for e in els if "source" in e["data"]]
    assert nodes and edges and data and cols and kpis
    seeds = [n for n in nodes if n["data"]["is_seed"]]
    assert len(seeds) == 1
    assert theme == "theme-dark"
    assert all("risk" in n["data"] and "decision" in n["data"] for n in nodes)


def test_depth_filter_reduces_elements():
    full = _elements(1, config.EGO_DEPTH_VIEW, 0.0, ["txn", "identity"])
    lvl1 = _elements(1, 1, 0.0, ["txn", "identity"])
    assert len(lvl1) <= len(full)


def test_edge_family_toggle():
    only_txn = _elements(3, 3, 0.0, ["txn"])
    kinds = {e["data"]["kind"] for e in only_txn if "source" in e["data"]}
    assert kinds <= {"txn"}


def test_key_path_highlight_marks_elements():
    els = _elements(3, 3, 0.0, ["txn", "identity"], highlight=True)
    assert any("onpath" in e.get("classes", "") for e in els)


def test_expansion_reveals_nodes_beyond_depth():
    ego = RUN["results"][1]["ego"]
    beyond = [n for n, a in ego.nodes(data=True) if a.get("hop", 0) >= 2]
    if not beyond:
        pytest.skip("ego has no depth-2 nodes")
    els = _elements(1, 1, 0.0, ["txn", "identity"], expanded=set(beyond))
    ids = {e["data"]["id"] for e in els if "source" not in e["data"]}
    assert set(beyond) <= ids


def test_layout_modes():
    live = _layout_spec("live", "X")
    assert live["name"] == "cose" and live["animate"] and not live["randomize"]
    rings = _layout_spec("rings", "X")
    assert rings["name"] == "breadthfirst" and 'id = "X"' in rings["roots"]
    assert _layout_spec("force", "X")["name"] == "cose"


def test_edge_weights_data_driven():
    # thickness scale must come from the ego's own volumes, never a constant:
    # the largest txn edge always normalizes to exactly 1.0
    for cid in CASES:
        els = _elements(cid, config.EGO_DEPTH_SCORE, 0.0, ["txn", "identity"])
        txn_w = [e["data"]["weight"] for e in els
                 if "source" in e["data"] and e["data"]["kind"] == "txn"]
        if txn_w:
            assert max(txn_w) == 1.0
            assert all(0.0 <= w <= 1.0 for w in txn_w)


def test_counterparty_frame_ranked():
    frame = _counterparty_frame(4)
    risks = frame["final_risk"].tolist()
    assert risks == sorted(risks, reverse=True)
    assert {"entity", "id", "decision", "alerted"} <= set(frame.columns)
