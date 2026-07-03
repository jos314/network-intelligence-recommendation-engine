"""Stage H — learned aggregator (v2 upgrade seam, NOT part of the MVP).

Contract: fit on the Stage A-D per-node features, predict a raw_risk in
[0, 1] with the same name, so it can replace stage_e_aggregate.score_aggregate
without touching calibration, decision, explainability, or the UI.

With only 6 labelled case subjects and 692 weak alert labels, training this
today would memorize, not learn (see §0 of the build plan). Turn it on once
real dispositions exist, or pre-train on synthetic AML data (AMLworld) /
Elliptic. Candidate models: sklearn regression/GBM on the stage features, or
an inductive GNN (GraphSAGE / CARE-GNN via PyTorch Geometric).
"""


def score_aggregate_learned(ego, model) -> None:
    """Drop-in replacement for Stage E once `model` exists.

    `model` must expose predict_proba/predict over the feature vector
    [base_risk, rel_risk, prop_risk, struct_risk] (+ optionally the raw
    stage components). Not implemented in the MVP by design.
    """
    raise NotImplementedError(
        "Stage H is a designed-in v2 upgrade: train a model on real labels "
        "(or synthetic pre-training) and implement the predict call here."
    )
