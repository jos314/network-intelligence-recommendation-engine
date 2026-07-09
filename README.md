# Network Intelligence & Recommendation Engine

Counterparty network risk at the point of review for AML investigators.
Given a case subject, the engine extracts the subject's ego-network from a
masked counterparty graph (or builds one from six raw HBUS PoC tables),
scores every connected entity with a transparent staged pipeline, turns the
calibrated score into one of **{No action, EDD, SAR}**, explains the drivers,
and exports grounded per-case metrics for the AI conclusion.

## Documentation

| Doc | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | system overview, runtimes, module map, tech stack, extension seams |
| [docs/DATA.md](docs/DATA.md) | the two data paths (prebuilt masked graph + six raw tables), ID crosswalk, entity resolution, switching to real data |
| [docs/SCORING.md](docs/SCORING.md) | stages A–H, weights, calibration, decision layer, governance |
| [docs/synthetic-data-integration-brief.md](docs/synthetic-data-integration-brief.md) | the prebuilt `GRAPH_NODES`/`GRAPH_EDGES` extract: schema, crosswalk, loader |
| [docs/APP.md](docs/APP.md) | login, screen guide, graph encodings, analyst workflow |

Design origin: the vault note *Build Plan — App Architecture & Algorithms*
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

**Login:** demo credentials are `analyst` / `riskdemo` until you create
`data/users.json` or set `NIRE_USER` / `NIRE_PASSWORD` — see
[docs/APP.md](docs/APP.md#login). The auth module
(`src/app/auth.py`) is a deliberate placeholder seam for SSO/LDAP.

## Data sources (priority order)

1. **Prebuilt masked graph** — `GRAPH_NODES.parquet` + `GRAPH_EDGES.parquet`
   (+ the six raw tables) in `data/` or `data/synthetic/`. This is the real
   masked HBUS extract's format; generate its full-scale synthetic twin with:

   ```bash
   .venv/bin/python scripts/generate_synthetic_aml_data.py --out data/synthetic
   ```

   Cases score lazily (~10 s each, cached). Egos are bounded on this
   hub-scale graph: top-K counterparties by flow per hop, ALL alerted/PEP/
   high-CRR neighbours always retained, truncation disclosed in the UI and
   in `governance.scoring_scope`. See docs/synthetic-data-integration-brief.md.

2. **Six raw tables** in `data/` as `TRANSACTIONS`, `CUSTOMERS`,
   `CUSTOMER_ACCOUNT_LINK`, `ALERTS`, `COUNTRY`, `CASE_CUSTOMERS`
   (`.parquet` / `.csv` / `.xlsx`) — the original P0/P1 path builds the
   graph from scratch.

3. **Demo fixture** — small in-code dataset, used when nothing else exists
   (and by the test suite via `NIRE_DATA_SOURCE=demo`).

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
| P5 conclusion | `src/conclusion/store.py` | exports `output/case_metrics/case_<n>.json`; VS Code Copilot + `skills/case-conclusion/SKILL.md` writes the narrative the app displays (`prompt_writer.py` keeps the legacy paste-into-LLM file) |
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
