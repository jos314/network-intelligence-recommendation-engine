# Scoring — how an entity gets its risk score and decision

Every entity in a case's ego-network ends with `final_risk ∈ [0,1]`, and the
case ends with one of **{No action, EDD, SAR}**. The pipeline is a sequence
of named stages; each stage's output is a stored column, so any score can be
decomposed after the fact. All weights and thresholds live in
`src/config.py` — that file is the model documentation SMEs sign off on
(SR 11-7).

The stages run over whatever ego-network `DataAccess` returns and are
**identical for both data sources** (see DATA.md). On the prebuilt masked
graph, the entity signals below map to `GRAPH_NODES` columns
(`HAS_PREV_TM_ALERT`, `PEP_FLAG`, `CRR`) and edge features already joined on
`GRAPH_EDGES` (`TOTAL_AMOUNT_BASE`, the `SHARED_*` contact flags,
`..._COUNTRY_RISK`, `EDGE_DIRECTION`); on the raw path they come from the
crosswalked six tables.

```
A own traits   B relationship   C propagation   D structure
      \              |               |              /
       └──────────── E aggregation (weighted sum) ─┘
                          │
                 F calibration (Platt)
                          │
             G decision layer → {No action, EDD, SAR}
```

## Stage A — base risk (the entity's own characteristics)

A transparent scorecard; each signal is normalized to [0,1] and combined as
a weighted sum (weights sum to 1.0, so `base_risk ∈ [0,1]`):

| Signal | Source | Weight | Component |
|---|---|---|---|
| TM-alerted | ALERTS membership | 0.30 | 1.0 if alerted |
| Watchlist / sanctions | name match (source open — Q5) | 0.25 | 1.0 exact / fuzzy-scaled |
| PEP | `PEP_FLAG` | 0.15 | 1.0 if Y |
| KYC risk | `CRR` | 0.15 | HIGH=1.0, MEDIUM=0.5, LOW/STANDARD=0.1 |
| Country risk | `COUNTRY_RISK` | 0.10 | HIGH=1.0, MEDIUM=0.5, STANDARD/LOW=0.0 |
| KYC opacity | share of missing KYC fields | 0.05 | opacity ≠ safety |

## Stage B — relationship risk (level-1 counterparties of the subject)

For each **direct** counterparty of the case subject:

| Feature | Weight | Meaning |
|---|---|---|
| shared attribute | 0.40 | any same-phone / same-email / same-address link to the subject (classic nominee/shell signature) |
| volume share | 0.30 | this counterparty's share of the subject's total flow |
| country change | 0.15 | the flow crosses a border vs the subject's country |
| capital ratio shared | 0.15 | **proxy** = volume concentration (definition open — Q8) |

Nodes at hop ≥ 2 have `rel_risk = 0`; their risk arrives via Stage C.

## Stage C — risk propagation (guilt-by-association, levels 2–K)

Risk spreads from **bad seeds** (nodes with `base_risk ≥ 0.30`) across the
ego-network, decaying with hops and time. Association spreads both ways, so
edges are traversed undirected here (direction still feeds Stage D).

Edge weights combine size and recency:
`w = min(log10(amount)/6, 1) · exp(−age_days/τ)` with τ = 180 days for
transaction edges; identity links use their frequency-based ER weight.

Two interchangeable formalisms (`config.PROP_METHOD`):

* **`ppr`** (default) — personalized PageRank / TrustRank: restart
  distribution concentrated on bad seeds weighted by `base_risk`,
  damping α = 0.85, over the weighted adjacency. Smooth, order-preserving.
* **`khop`** — bounded K-hop diffusion:
  `prop(v) = max over paths ≤ K hops of base(seed) · γ^len · Π(edge w)`,
  hop-decay γ = 0.5. Fully traceable.

The k-hop **max-path tracer always runs** (even under ppr) because the
explanation layer needs the *path that carried the most risk* — that is the
`subject → mule → sanctioned entity` chain shown in the app and the
evidence pack.

## Stage D — structural / typology features

| Component | Weight | Detects |
|---|---|---|
| cycle membership | 0.40 | circular flows / layering (cycles ≤ 6 on directed txn edges) |
| community risk | 0.30 | dense groups moving funds together (greedy modularity; mean base risk of communities ≥ 3) |
| centrality | 0.30 | hubs, brokers, bridge nodes (degree + betweenness) |

## Stage E — aggregation (the Stage-H seam)

```
raw_risk = 0.35·base + 0.20·rel + 0.30·prop + 0.15·struct
```

The four parts are stored separately on every node — the app's "risk
decomposition" bars and the exact driver attribution read them directly.
**This weighted sum is the seam for Stage H:** a learned model
(GraphSAGE / CARE-GNN) replaces the sum with the same inputs and outputs
once labels (or pre-training on synthetic AML data) allow. Nothing else in
the system changes.

## Stage F — calibration

Raw scores are mapped to honest probabilities with **Platt scaling**
(logistic regression) against the available weak labels (TM alerts as
positives). Guard: with fewer than 20 positives the calibrator falls back
to the identity mapping and says so — thresholds then operate on raw scores,
documented rather than silently miscalibrated. Re-fit is expected when real
SAR/EDD dispositions arrive (open question Q1).

## Stage G — decision layer (where the "3 outputs" come from)

Checked in order, every step auditable:

1. **Hard overrides**
   * confirmed sanctions / exact watchlist hit → **SAR** candidate
   * active TM alert **and** `prop_risk ≥ 0.50` → at least **EDD**
2. **Threshold bands** on calibrated p: `p < 0.40` → No action;
   `0.40 ≤ p < 0.75` → EDD; `p ≥ 0.75` → SAR
3. **Case-level escalations** — the case decision weighs the network, not
   just the subject node:
   * subject with `prop_risk ≥ 0.60` and ≥ 1 alerted/sanctioned neighbour
     → at least EDD
   * sanctioned neighbour in the ego-network → at least EDD

Thresholds are cost-sensitive by intent (a missed SAR is far costlier than
a false positive) and need SME sign-off before production.

## Explainability

* **Drivers** — because Stage E is linear-additive, each feature's exact
  contribution is `stage_weight × feature_weight × component`. These are
  the true Shapley values of the model; no approximation is involved.
  (`shap` becomes necessary only when Stage H replaces the sum.)
* **Key paths** — from the Stage-C tracer: the top propagated-risk paths
  touching the subject, highlighted in the graph view.
* **Evidence pack** — one machine-written JSON per case (decision, score,
  drivers, paths, alerted/sanctioned neighbours, shared-attribute links,
  structural flags, governance metadata). It feeds the app, the audit
  trail, and the conclusion loop.
* **AI conclusion loop** (no LLM API — VS Code Copilot) — the app exports
  per-case metrics to `output/case_metrics/case_<n>.json` on every scoring
  run. In VS Code, Copilot follows `skills/case-conclusion/SKILL.md` (fixed
  instructions + output contract: "≤200 words, no invented facts, honour the
  governance caveats") to write `output/conclusions/case_<n>.md`, which the
  app's AI-conclusion card then displays (refresh / edit / save). The
  contract keeps the write grounded in the exported metrics. See APP.md.

## Governance notes (SR 11-7)

* Every tunable is in `src/config.py` with its rationale.
* Calibration degrades loudly, never silently.
* The data-quality checklist runs on every ingest.
* Concept drift: scores decay in meaning as behaviour shifts; re-fit
  calibration on a schedule and monitor alert-rate drift once live.

## Open questions that shape v2

Ground-truth labels (Q1), COUNTRY_RISK band set (Q2), CREDIT semantics
confirmation (Q3), account-link truncation (Q4), watchlist source (Q5),
capital-ratio definition (Q8), propagation method preference (Q10),
edge-thickness = risk vs amount (Q11), conclusion LLM (Q13). The full list
lives in the vault build plan.
