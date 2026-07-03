"""P5 / end-to-end acceptance: all 6 cases run, evidence packs are complete,
conclusion prompt files are generated and grounded."""
import json

import pytest

from src import config
from src.pipeline import run_all_cases

RUN = run_all_cases(verbose=False)


def test_all_six_cases_scored():
    assert sorted(RUN["results"]) == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("cid", [1, 2, 3, 4, 5, 6])
def test_evidence_pack_contract(cid):
    ev = RUN["results"][cid]["evidence"]
    for key in ("case_id", "subject_id", "decision", "calibrated_score",
                "top_drivers", "key_paths", "alerted_neighbors",
                "sanctioned_neighbors", "shared_attribute_links",
                "structural_flags", "decision_reasons", "governance"):
        assert key in ev, key
    assert ev["decision"] in (config.DECISION_NO_ACTION, config.DECISION_EDD,
                              config.DECISION_SAR)
    assert 0.0 <= ev["calibrated_score"] <= 1.0
    assert ev["decision_reasons"]
    json.dumps(ev, default=str)   # must serialize for the prompt file


@pytest.mark.parametrize("cid", [1, 2, 3, 4, 5, 6])
def test_prompt_file_written_and_grounded(cid):
    path = RUN["results"][cid]["prompt_path"]
    assert path.exists()
    text = path.read_text()
    ev = RUN["results"][cid]["evidence"]
    assert ev["decision"] in text
    assert ev["subject_id"] in text
    assert "do not invent" in text


def test_clean_subject_is_no_action():
    assert RUN["results"][5]["evidence"]["decision"] == config.DECISION_NO_ACTION


def test_planted_typologies_escalate():
    for cid in (1, 2, 3, 4, 6):
        assert RUN["results"][cid]["evidence"]["decision"] in (
            config.DECISION_EDD, config.DECISION_SAR), "case %d" % cid


def test_drivers_ranked_by_magnitude():
    for cid, r in RUN["results"].items():
        mags = [abs(d["magnitude"]) for d in r["evidence"]["top_drivers"]]
        assert mags == sorted(mags, reverse=True)
