"""P2 acceptance: every ego node gets base/rel/prop/struct/final, parts stored
separately; propagation decays with distance from bad seeds."""
import pytest

from src.pipeline import offline_prep, score_ego

PREP = offline_prep(verbose=False)


@pytest.fixture(scope="module", params=sorted(PREP["cases"]))
def scored(request):
    return score_ego(PREP["graph"], PREP["cases"][request.param])


def test_all_parts_present_and_bounded(scored):
    for n, a in scored.nodes(data=True):
        parts = a["risk_parts"]
        assert set(parts) == {"base", "rel", "prop", "struct"}
        for v in parts.values():
            assert 0.0 <= v <= 1.0
        assert 0.0 <= a["raw_risk"] <= 1.0


def test_components_stored_for_decomposition(scored):
    for n, a in scored.nodes(data=True):
        assert "base_components" in a
        assert "struct_components" in a


def test_rel_risk_only_on_level1(scored):
    for n, a in scored.nodes(data=True):
        if a.get("hop") != 1:
            assert a["rel_risk"] == 0.0


def test_propagation_decays_with_hops():
    """Case 5's clean subject: prop risk should shrink as hops from any bad
    seed grow — verified on the khop tracer, whose scores are the path scores."""
    ego = score_ego(PREP["graph"], PREP["cases"][5], method="khop")
    by_path_len = {}
    for n, a in ego.nodes(data=True):
        p = a.get("risk_path")
        if p and a["prop_risk"] > 0:
            by_path_len.setdefault(len(p), []).append(a["prop_risk"])
    lens = sorted(by_path_len)
    for a_len, b_len in zip(lens, lens[1:]):
        assert max(by_path_len[b_len]) <= max(by_path_len[a_len]) + 1e-9


def test_cycle_detected_in_case1():
    ego = score_ego(PREP["graph"], PREP["cases"][1])
    assert ego.nodes[PREP["cases"][1]]["in_cycle"]


def test_shared_attribute_flags_in_case3():
    ego = score_ego(PREP["graph"], PREP["cases"][3])
    l1_shared = [a for n, a in ego.nodes(data=True)
                 if a.get("hop") == 1 and a.get("rel_shared_kinds")]
    assert l1_shared, "nominee with shared address/phone must be flagged"
    kinds = set().union(*(a["rel_shared_kinds"] for a in l1_shared))
    assert "same_address" in kinds and "same_phone" in kinds
