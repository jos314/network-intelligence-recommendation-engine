"""Stage F — calibration (§4.F): raw_risk -> calibrated probability so the
decision thresholds mean something.

Labels available today are WEAK: ALERTS membership as noisy positives (692),
everything else negative. With enough positives we fit Platt (logistic
sigmoid) — isotonic needs more data than we have. Below the floor we fall
back to a documented identity mapping and flag calibrated=False so the
governance notes (SR 11-7) record that thresholds ride on an uncalibrated
score. Re-fit on refresh; monitor drift.
"""
import numpy as np
from sklearn.linear_model import LogisticRegression

from .. import config


class Calibrator:
    def __init__(self):
        self.model = None
        self.calibrated = False
        self.n_pos = 0
        self.n = 0

    def fit(self, raw_scores, labels) -> "Calibrator":
        raw = np.asarray(raw_scores, dtype=float).reshape(-1, 1)
        y = np.asarray(labels, dtype=int)
        self.n, self.n_pos = len(y), int(y.sum())
        if self.n_pos >= config.MIN_CALIBRATION_POSITIVES and self.n_pos < self.n:
            self.model = LogisticRegression()  # Platt scaling on the 1-D score
            self.model.fit(raw, y)
            self.calibrated = True
        return self

    def transform(self, raw_scores):
        raw = np.asarray(raw_scores, dtype=float)
        if self.calibrated:
            return self.model.predict_proba(raw.reshape(-1, 1))[:, 1]
        return np.clip(raw, 0.0, 1.0)  # documented fallback: identity

    def describe(self) -> dict:
        return {"calibrated": self.calibrated, "method": "platt" if self.calibrated else "identity-fallback",
                "n": self.n, "n_pos": self.n_pos}


def fit_calibrator_from_egos(egos) -> Calibrator:
    """Fit one calibrator on the union of all scored ego-networks,
    using ALERTS membership as the weak positive label."""
    raw, labels, seen = [], [], set()
    for ego in egos:
        for n, attrs in ego.nodes(data=True):
            if n in seen or "raw_risk" not in attrs:
                continue
            seen.add(n)
            raw.append(attrs["raw_risk"])
            labels.append(1 if attrs.get("alerted") else 0)
    return Calibrator().fit(raw, labels)


def apply_calibration(ego, calibrator: Calibrator) -> None:
    nodes = list(ego.nodes)
    p = calibrator.transform([ego.nodes[n].get("raw_risk", 0.0) for n in nodes])
    for n, pi in zip(nodes, p):
        ego.nodes[n]["final_risk"] = float(pi)
