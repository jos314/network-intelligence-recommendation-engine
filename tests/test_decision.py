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
    assert any("OVERRIDE" in r for r in d["reasons"])


def test_alert_plus_propagation_floors_at_edd():
    d = node_decision(_attrs(0.05, alerted=True, prop=config.OVERRIDE_PROP_RISK))
    assert d["decision"] == config.DECISION_EDD
    assert any("OVERRIDE" in r for r in d["reasons"])


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
