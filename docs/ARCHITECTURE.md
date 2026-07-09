# Architecture

Counterparty network risk at the point of review for AML investigators.
Given a case subject, the engine extracts the subject's ego-network from a
masked counterparty graph, scores every connected entity with a transparent
staged pipeline, turns the calibrated score into **{No action, EDD, SAR}**,
explains the drivers, and writes a grounded LLM prompt file for the case
conclusion.

The graph comes from one of two interchangeable sources behind
`src/data_access.py` (see DATA.md): a **prebuilt masked graph**
(`GRAPH_NODES`/`GRAPH_EDGES` — what runs today) or the **six raw tables**
built from scratch (demo fixture / tests). Everything downstream of
`DataAccess` is identical for both.

Companion docs: [DATA.md](DATA.md) (tables & identity resolution) ·
[SCORING.md](SCORING.md) (stages A–H & decision layer) ·
[APP.md](APP.md) (the analyst screen).

## Design principles

1. **Explainable by construction.** Every stage's output is an inspectable
   column; the aggregate is a documented weighted sum, so driver attribution
   is *exact*, not approximated. Required by the brief and SR 11-7.
2. **The 3 outputs come from a decision layer, not a black box.** With only
   6 labelled subjects and 692 weak alert labels, a supervised 3-class model
   would memorize, not learn. The learned model is a designed-in v2 upgrade
   (Stage H) behind a clean seam — see SCORING.md.
3. **Nothing is hard-coded to the demo fixture.** Every screen element and
   score derives from whatever `DataAccess` returns; dropping real files into
   `data/` (prebuilt graph or six raw tables) changes everything seamlessly
   (see DATA.md → "Switching to real data").

## System overview

```mermaid
flowchart TD
    subgraph SRC [Data source — DataAccess picks one]
        PB[(Prebuilt masked graph:\nGRAPH_NODES + GRAPH_EDGES\ncrosswalk + ER pre-baked)]
        RAW[(6 raw tables)] --> XW[ID crosswalk +\nentity resolution] --> BLD[(Unified graph)]
    end
    L[Login] --> C[Case selection]
    C --> R[resolve seed via crosswalk]
    PB --> R
    BLD --> R
    R --> E[Ego-network builder\nbounded BFS depth 3 around seed]
    E --> S[Scoring stages A–E]
    S --> F[Calibration F]
    F --> D[Decision layer G\nNo action / EDD / SAR]
    S --> X[Explainability\ndrivers + key paths]
    D --> EV[Evidence pack]
    X --> EV
    EV --> P[case_metrics_case_n.json]
    P -.VS Code Copilot + SKILL.-> CC[AI-written conclusion]
    S --> V[Graph view + ranked table]
    D --> V
```

## Two runtimes

* **Data preparation** — depends on the source. **Prebuilt** (`data_access.py`
  → `PrebuiltGraphSource`): load `GRAPH_NODES`/`GRAPH_EDGES` parquet; the
  crosswalk and shared-contact resolution are already baked in, so there is
  no build step. **Raw** (`src/pipeline.py: prepare()`): load the six tables,
  run the crosswalk + entity resolution, build the unified graph once.
* **Interactive** (per case, both sources): resolve the seed via the crosswalk
  → extract its **bounded** depth-3 ego-network → score → decide → explain →
  render. The screen never loads the full graph — only one subject's
  neighbourhood, prioritized top-K by flow — which is what keeps the
  visualization from being overwhelmed on the hub-scale graph. Cases score
  lazily on first open and cache.

## Module map

```
src/
├── config.py            all tunable numbers (weights, decay, thresholds),
│                        documented per SR 11-7 — SMEs challenge THIS file
├── data_access.py       THE SEAM: picks prebuilt vs demo, resolves a seed,
│                        returns its scored ego (lazy + cached)
├── ingest/
│   ├── prebuilt.py      find + load the GRAPH_NODES/EDGES extract;
│   │                    _clean_nodes strips placeholder tokens
│   ├── loaders.py       raw six tables from data/, demo fixture otherwise
│   ├── crosswalk.py     raw path P0: one canonical id per entity (DATA.md)
│   ├── entity_resolution.py  raw path P0: shared phone/email/address links
│   ├── quality.py       raw path P0: data-quality checklist, every ingest
│   └── synthetic.py     demo fixture replicating the real schema quirks
├── graph/
│   ├── prebuilt_source.py  prebuilt path: resolve() + bounded ego_graph()
│   │                    over GRAPH_NODES/EDGES (ER + crosswalk pre-baked)
│   ├── build.py         raw path P1: heterogeneous directed graph; txn edges
│   │                    oriented along MONEY FLOW via CREDIT_DEBIT_CODE
│   └── ego.py           depth-K BFS, hop distances, node flow summary
├── scoring/             P2: stages A–E, each a pure function (SCORING.md)
│   └── stage_h_learned.py   v2 seam — swap for GraphSAGE/CARE-GNN later
├── decision/            P3: calibration (F) + rules/thresholds (G)
├── explain/             P5: exact driver attribution, key paths, evidence pack
├── conclusion/          P5: case-metrics JSON + conclusion store (Copilot loop)
└── app/                 P4: Dash analyst screen (APP.md)
    └── auth.py          login seam — swap for SSO/LDAP without touching UI
```

Both graph sources expose the same shape to `DataAccess`, so `scoring/`,
`decision/`, `explain/`, and `app/` never know which one is active.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| App shell | Dash (Plotly) | one Python stack end-to-end, matches the pandas data model |
| Graph render | dash-cytoscape | interactive network, style-by-data, force layouts — the "Obsidian feel" |
| Graph compute | NetworkX | ego-networks are small; swap for igraph only if profiling says so |
| ML / calibration | scikit-learn | Platt scaling (`LogisticRegression`) on weak labels |
| Attribution | exact additive contributions | the scorer is linear-additive, so these ARE the Shapley values; `shap` becomes necessary only when Stage H lands |
| Learned model (v2) | PyTorch Geometric / DGL | GraphSAGE baseline, CARE-GNN for camouflage/imbalance |

## Extension seams (deliberate)

| Seam | Today | Later |
|---|---|---|
| `app/auth.py: verify_credentials` | sha256 vs `data/users.json` / env / demo | SSO, LDAP, OAuth |
| `scoring/stage_e_aggregate.py` | documented weighted sum | Stage H learned aggregator, same inputs/outputs |
| `decision/calibration.py` | Platt on weak alert labels, identity fallback | isotonic / refit schedule once real labels exist |
| `conclusion/` metrics + store | VS Code Copilot follows `skills/case-conclusion/SKILL.md` over the exported `case_metrics_case_n.json` → app card | local model (Ollama) or API for one-click conclusions |
| `data_access.py` source | prebuilt graph or six raw tables in `data/` | database connection — only this seam changes |
