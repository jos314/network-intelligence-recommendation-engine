"""P3 acceptance: calibrated score + hard rules + thresholds -> the 3 outputs."""
import numpy as np

from src import config
from src.decision.calibration import Calibrator
from src.decision.rules import node_decision


def _attrs(p, alerted=False, prop=0.0, watchlist=0.0):
    return {"final_risk": p, "alerted": alerted, "prop_risk": prop,
            "base_components": {"watchlist_match": watchlist}}


def test_threshold_bands():
    assert node_decision(_attrs(config.DECISION_T1 - 0.01))["decision"] == config.DECISION_NO_ACTION
    assert node_decision(_attrs(config.DECISION_T1))["decision"] == config.DECISION_EDD
    assert node_decision(_attrs(config.DECISION_T2))["decision"] == config.DECISION_SAR


def test_watchlist_override_beats_low_score():
    d = node_decision(_attrs(0.05, watchlist=1.0))
    assert d["decision"] == config.DECISION_SAR
    assert any(r.startswith("Override") for r in d["reasons"])
    # never claim "confirmed" while there is no real screening source
    assert not any("confirmed" in r.lower() for r in d["reasons"])


def test_alert_plus_propagation_floors_at_edd():
    d = node_decision(_attrs(0.05, alerted=True, prop=config.OVERRIDE_PROP_RISK))
    assert d["decision"] == config.DECISION_EDD
    assert any(r.startswith("Override") for r in d["reasons"])


def test_reasons_are_case_narrative_english():
    # no debug tokens — an investigator should be able to paste these
    for p in (0.1, 0.5, 0.9):
        for r in node_decision(_attrs(p))["reasons"]:
            assert "t1=" not in r and "t2=" not in r and "p=" not in r
            assert "ESCALATION:" not in r and "OVERRIDE:" not in r


def test_reasons_always_documented():
    for p in (0.1, 0.5, 0.9):
        assert node_decision(_attrs(p))["reasons"]


def test_calibrator_platt_with_enough_labels():
    rng = np.random.default_rng(0)
    raw = np.concatenate([rng.uniform(0.5, 1.0, 40), rng.uniform(0.0, 0.5, 200)])
    y = np.concatenate([np.ones(40), np.zeros(200)])
    cal = Calibrator().fit(raw, y)
    assert cal.calibrated and cal.describe()["method"] == "platt"
    p = cal.transform([0.1, 0.9])
    assert p[1] > p[0]           # monotone
    assert (0 <= p).all() and (p <= 1).all()


def test_calibrator_fallback_with_few_positives():
    raw = [0.1, 0.9, 0.5]
    y = [0, 1, 0]
    cal = Calibrator().fit(raw, y)
    assert not cal.calibrated
    assert list(cal.transform([0.3])) == [0.3]   # documented identity fallback


def test_displayed_score_never_contradicts_its_band():
    """p=0.7496 once displayed as '0.750' beside an EDD decision and a
    '>= 0.75 SAR' legend — precision must grow until the shown value sits
    on the same side of both thresholds as the true score."""
    from src.decision.rules import node_decision

    r = node_decision({"final_risk": 0.7496})
    band_line = next(x for x in r["reasons"] if "band" in x)
    assert r["decision"] == config.DECISION_EDD
    assert "0.7496" in band_line          # not the contradictory "0.750"

    r2 = node_decision({"final_risk": 0.75})
    assert r2["decision"] == config.DECISION_SAR

    r3 = node_decision({"final_risk": 0.62})   # far from thresholds: 2 dp
    assert "0.62" in next(x for x in r3["reasons"] if "band" in x)
