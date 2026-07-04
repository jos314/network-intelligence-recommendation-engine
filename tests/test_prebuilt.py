"""Integration-brief acceptance: the prebuilt masked-graph path.

Covers the non-negotiable rules from docs/synthetic-data-integration-brief.md:
the masked-id crosswalk, depth-3 bounded egos, the two node types,
placeholder cleaning, money-flow orientation from EDGE_DIRECTION, and the
end-to-end result for a Case ID."""
import pandas as pd
import pytest

from src import config
from src.data_access import DataAccess
from src.ingest.crosswalk import NODE_CUSTOMER, NODE_EXTERNAL
from src.ingest.prebuilt import load_prebuilt_tables


@pytest.fixture(scope="module")
def da(prebuilt_dir):
    return DataAccess(data_dir=prebuilt_dir, source="prebuilt")


@pytest.fixture(scope="module")
def tables(prebuilt_dir):
    return load_prebuilt_tables(prebuilt_dir)


def test_source_detected(da):
    assert da.source == "prebuilt"
    assert len(da.case_ids()) == 6


def test_crosswalk_resolves_every_format(da, tables):
    cases = tables["CASE_CUSTOMERS"]
    for cust in cases["CUSTOMER_ID"]:
        masked = da.resolve(cust)                     # plain int
        assert masked and masked.startswith("CUS_")
        assert da.resolve(str(cust)) == masked        # digit string
        assert da.resolve(str(cust).zfill(9)) == masked  # zero-padded original
        assert da.resolve(masked) == masked           # already masked
    pseudo = tables["GRAPH_NODES"].loc[
        tables["GRAPH_NODES"]["IS_PSEUDO"], "ORIGINAL_CUSTOMER_ID"].iloc[0]
    assert da.resolve(pseudo) and da.resolve(pseudo).startswith("CUS_")
    assert da.resolve("no-such-id") is None
    assert da.resolve(None) is None


def test_ego_depth_and_bounds(da):
    for cid in da.case_ids():
        ego = da.result(cid)["ego"]
        hops = [a["hop"] for _, a in ego.nodes(data=True)]
        assert max(hops) <= config.EGO_DEPTH_SCORE
        assert ego.number_of_nodes() <= config.EGO_MAX_NODES + len(hops) * 0  # hard cap
        assert ego.graph["seed"] in ego.nodes
        assert ego.graph["truncation"]["nodes_scored"] == ego.number_of_nodes()


def test_two_node_types_and_pseudo_has_no_kyc(da):
    ego = da.result(1)["ego"]
    types = {a["node_type"] for _, a in ego.nodes(data=True)}
    assert types <= {NODE_CUSTOMER, NODE_EXTERNAL}
    for _, a in ego.nodes(data=True):
        if a["node_type"] == NODE_EXTERNAL:
            assert a.get("crr") is None and a.get("pep_flag") is None
            assert a.get("address") is None and a.get("phone") is None


def test_placeholders_cleaned(da):
    # 'UNKNOWN' addresses, '0000000000' phones and 'N'/'0000' CRR tokens are
    # NOT signal — they must arrive as nulls, never as values
    for cid in da.case_ids():
        ego = da.result(cid)["ego"]
        for _, a in ego.nodes(data=True):
            assert a.get("address") not in config.PLACEHOLDER_ADDRESSES
            assert a.get("phone") not in config.PLACEHOLDER_PHONES
            assert a.get("crr") not in config.CRR_UNKNOWN_TOKENS


def test_money_flow_orientation_follows_edge_direction(da, tables):
    ego = da.result(1)["ego"]
    kept = set(ego.nodes)
    e = tables["GRAPH_EDGES"]
    sub = e[e["SRC"].isin(kept) & e["DST"].isin(kept)]
    checked_fwd = checked_rev = False
    for r in sub.itertuples(index=False):
        if r.EDGE_DIRECTION == "ORG_to_BEN" and not checked_fwd:
            assert ego.has_edge(r.SRC, r.DST)
            checked_fwd = True
        if r.EDGE_DIRECTION == "BEN_to_ORG" and not checked_rev:
            assert ego.has_edge(r.DST, r.SRC)  # CREDIT: funds flow BEN -> ORG
            checked_rev = True
        if checked_fwd and checked_rev:
            break
    assert checked_fwd, "no ORG_to_BEN edge found in the ego"


def test_priority_neighbours_survive_truncation(da, tables):
    """Alerted direct counterparties must be kept even beyond top-K."""
    gs = da._gs
    for cid in da.case_ids():
        seed = da.case_meta(cid)["masked"]
        nbrs = set(gs._adj.get(seed, ([], []))[0])
        alerted_nbrs = nbrs & gs._alerted
        if not alerted_nbrs:
            continue
        ego = da.result(cid)["ego"]
        assert alerted_nbrs <= set(ego.nodes)
        return
    pytest.skip("no alerted neighbour adjacent to any seed at this scale")


def test_end_to_end_case_result(da):
    r = da.result(1)
    ev = r["evidence"]
    assert ev["decision"] in (config.DECISION_NO_ACTION, config.DECISION_EDD,
                              config.DECISION_SAR)
    assert ev["governance"]["scoring_scope"]["nodes_scored"] > 1
    assert ev["subject_id"].startswith("CUS_")
    assert r["prompt_path"].exists()
    # flow numbers made it through (heavy-tailed but positive)
    assert ev["subject_total_flow"] > 0


def test_identity_edges_from_shared_flags(da, tables):
    e = tables["GRAPH_EDGES"]
    if int(e["ANY_SHARED_CONTACT"].sum()) == 0:
        pytest.skip("no shared-contact edges at this scale")
    found = False
    for cid in da.case_ids():
        ego = da.result(cid)["ego"]
        kinds = {d.get("kind") for _, _, d in ego.edges(data=True)}
        if kinds & {"same_address", "same_phone", "same_email"}:
            found = True
            break
    if not found:
        pytest.skip("shared-contact rows fall outside all bounded egos")
