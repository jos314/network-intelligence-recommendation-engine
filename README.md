# Network Intelligence & Recommendation Engine

Counterparty network risk at the point of review for AML investigators.
Given a case subject, the engine builds the subject's ego-network from six
relational tables (HBUS PoC schema), scores every connected entity with a
transparent staged pipeline, turns the calibrated score into one of
**{No action, EDD, SAR}**, explains the drivers, and writes a grounded LLM
prompt file for the case conclusion.

Design doc: the vault note *Build Plan — App Architecture & Algorithms*
(Obsidian, `Projects/AI-Enabled Network Intelligence & Recommendation Engine/`).

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the full pipeline (all 6 cases) — uses the synthetic demo fixture
# until the real tables are dropped into data/
.venv/bin/python -m src.pipeline

# tests
.venv/bin/python -m pytest -q

# the analyst app (http://127.0.0.1:8050)
.venv/bin/python -m src.app.app
```

## Real data

Drop the six tables into `data/` as `TRANSACTIONS`, `CUSTOMERS`,
`CUSTOMER_ACCOUNT_LINK`, `ALERTS`, `COUNTRY`, `CASE_CUSTOMERS` with any of
`.parquet` / `.csv` / `.xlsx` — they are picked up automatically (the demo
fixture is used only when they are absent). ID columns are read as strings
so zero-padding and `PSEUDO_` forms survive; the crosswalk normalizes them.

## Pipeline (data → decision → explanation)

| Stage | Module | What it does |
|---|---|---|
| P0 crosswalk | `src/ingest/crosswalk.py` | one canonical id per entity across all raw formats |
| P0 entity resolution | `src/ingest/entity_resolution.py` | shared phone/email/address links with over-linking guard |
| P0 data quality | `src/ingest/quality.py` | the §2.3 checklist (Excel truncation, risk bands, coverage) |
| P1 graph | `src/graph/build.py` | heterogeneous directed graph; txn edges oriented along money flow |
| P1 ego | `src/graph/ego.py` | depth-K BFS around the seed, hop distances |
| P2 Stage A | `src/scoring/stage_a_base.py` | scorecard on own characteristics (alert, PEP, CRR, country, opacity) |
| P2 Stage B | `src/scoring/stage_b_relationship.py` | level-1 relationship risk (volume share, shared attrs, corridors) |
| P2 Stage C | `src/scoring/stage_c_propagation.py` | personalized PageRank **and** traceable K-hop max-path diffusion |
| P2 Stage D | `src/scoring/stage_d_structural.py` | cycles, dense groups, centrality |
| P2 Stage E | `src/scoring/stage_e_aggregate.py` | weighted aggregation — **the seam for Stage H** |
| P3 Stage F | `src/decision/calibration.py` | Platt scaling on weak alert labels; documented fallback |
| P3 Stage G | `src/decision/rules.py` | hard overrides + threshold bands + case-level escalations |
| P5 explain | `src/explain/` | exact additive driver attribution, key risk paths, evidence pack |
| P5 conclusion | `src/conclusion/prompt_writer.py` | `output/conclusion_prompt_case_<n>.md` for manual LLM paste |
| P4 app | `src/app/app.py` | Dash + cytoscape ego-graph, size=risk, red=SAR/yellow=EDD, ranked table |
| v2 Stage H | `src/scoring/stage_h_learned.py` | learned aggregator seam (GraphSAGE/CARE-GNN) — intentionally not trained |

All tunable numbers (scorecard weights, decay, thresholds) live in
`src/config.py`, documented per SR 11-7.

## Repo layout

```
src/ingest/     loaders + crosswalk + entity resolution + quality + demo fixture
src/graph/      unified graph build, ego extraction
src/scoring/    stages A–E (+ H seam), each a pure function over the ego graph
src/decision/   calibration (F) + rules/thresholds (G)
src/explain/    drivers + paths + evidence pack
src/conclusion/ prompt-file writer
src/app/        Dash app
prompts/        conclusion prompt template
tests/          acceptance tests per build phase
data/           real tables (gitignored)
output/         evidence prompt files (gitignored)
```
