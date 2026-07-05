# The analyst app

Dash + cytoscape single-page app. Run it with
`.venv/bin/python -m src.app.app` and open http://127.0.0.1:8050.

## Login

The app opens on a sign-in gate. Credential sources, in priority order
(`src/app/auth.py`):

1. `data/users.json` — `{"username": "<sha256 hex of password>", ...}`
   (gitignored, next to the data). Generate a hash with:
   `python3 -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"`
2. `NIRE_USER` + `NIRE_PASSWORD` environment variables (single user)
3. **Demo fallback** — `analyst` / `riskdemo`, active only when neither
   source exists; the server prints a warning so this can't silently reach
   production.

The session lives in browser session-storage: a refresh keeps you signed
in, closing the tab signs you out. This is the documented placeholder for
real auth (open question Q12) — `verify_credentials()` is the single seam
to replace with SSO/LDAP/OAuth; the UI doesn't change.

## Screen layout

* **Header** (sticky) — case selector, signed-in user chip, sign out,
  dark/light toggle (☾/☀).
* **KPI row** — case decision (with an "escalated by network evidence" tag
  when the network raised it above the subject's own band), risk gauge with
  **threshold ticks at t1/t2** and the band caption, alerted-within-2-hops
  count (hover lists the names), watchlist status, **activity window**,
  **subject total flow**, scored-network size, typology flags (hover for
  plain English), LOB.
* **Decision rationale strip** — the reasons, in case-narrative English,
  visible without scrolling.
* **Graph card** — the ego-network, plus a **"showing X of Y" caption**
  that warns when filters hide risk-relevant entities.
* **Side card** — entity search, "Expand next hop of <entity>" / "Reset
  expansion", and the inspector: click a **node** for KYC + flow summary +
  risk decomposition + its top counterparties (each clickable), or click an
  **edge** for the relationship itself — total amount, transaction count,
  activity window, corridor (or the shared identity value).
* **Why card** — exact top risk drivers.
* **Key risk paths card** — propagated-risk chains, shared-attribute links,
  download buttons (evidence pack JSON, conclusion prompt MD).
* **Table card** — every counterparty with **type, total amount, # txns,
  first/last seen, % of subject flow (direct counterparties only)**; native
  sort, CSV export (of the filtered set), and **click any cell to select
  and inspect the whole row**. Its **custom filter bar** replaces Dash's
  native filter: a case-insensitive text search (entity or id), Decision /
  Type / alerted-only pickers, a **Clear filters** button, and (under "More
  filters") numeric **operator dropdowns** (≥ > = < ≤) for Risk / Amount /
  # Txns and **date-range pickers** for First/Last seen — leave one side
  blank for a one-sided before/after, fill both for a between window. All
  filters reset on case switch.

## Honesty rules (what the screen refuses to fake)

* **Watchlist**: with no screening source connected, the KPI reads
  "— · not screened", never 0.
* **Calibration**: when the calibrator fell back to identity (too few
  labels), the gauge is labelled "Risk score (uncalibrated)" with a ⚠
  tooltip carrying the label counts.
* **Filters disclose**: alerted nodes are exempt from the min-risk cut, the
  highlighted key path is force-included beyond the depth slider, and the
  caption counts whatever remains hidden.
* **Alert recency**: the extract carries no alert dates; every label says
  "recency unknown" instead of implying freshness.

## Graph encodings

| Visual | Meaning |
|---|---|
| node **size** | `final_risk` (bigger = riskier) |
| node **colour** | decision: red = SAR, yellow = EDD, grey = No action |
| dashed **rectangle** | external counterparty (`PSEUDO_`) — not a bank customer, no KYC held |
| red **ring** | has a TM alert (extract carries no dates — recency unknown) |
| blue **diamond** | the case subject (seed) |
| solid edge + arrow | transaction, arrow along **money flow**; thickness = amount relative to the ego's largest flow |
| dashed blue edge | identity link (same phone / email / address) |
| blue **glow** | the key propagated-risk path (when highlighting is on) |

## Controls

| Control | What it does |
|---|---|
| entities / clusters | the drill-down view vs the broad community view (hexagons sized by member count; click one to list members, click a member to open it in entity view) |
| Show top (10/25/50/100) | how many of the subject's riskiest DIRECT counterparties form the baseline view |
| Min risk | live-filters the view while dragging |
| **double-click a node** | reveals that node's own top-K riskiest counterparties (next hop, down to 3) — the core drill-down gesture |
| Expand next hop of <entity> / Reset expansion | button equivalent of double-click, and the undo |
| key risk path | force-draws the top Stage-C path |
| ◎ Center subject | pans/zooms onto the seed diamond and focuses it |
| Reset view | entities mode, top 25, min risk 0, no expansions |
| Advanced | edge-family toggles and layout modes (live physics / force / rings) |
| click a node / edge / cluster / table row | inspects it (the camera never moves on click) |
| Search entity… | focus any scored node by name/id |

## The AI conclusion loop (no LLM API — VS Code Copilot)

1. The app exports per-case metrics to `output/case_metrics/case_<n>.json`
   automatically on every scoring run.
2. In VS Code, ask Copilot (Claude via Copilot) to follow
   `skills/case-conclusion/SKILL.md` — it writes plain-language conclusions
   to `output/conclusions/case_<n>.md` under a strict grounding contract.
3. Press ↻ Refresh in the app's AI-conclusion card (or paste/edit and Save —
   the card is the case file's narrative).

## Analyst workflow (per case)

1. Sign in, pick the case.
2. Read the summary band — decision, calibrated score, AI conclusion, and
   the case facts in one glance.
3. Work the graph: enable the key-path highlight, expand suspicious nodes,
   check shared-attribute links (nominee signatures).
4. Work the table top-down — it is the same ego-network as rows, ranked by
   risk.
5. Download the **evidence pack** for the audit trail and the **conclusion
   prompt**; paste the prompt into the approved LLM; attach the returned
   narrative to the case.

## Implementation notes

* One render callback drives graph, KPIs, cards, and table from
  `(case, depth, min-risk, edge kinds, layout, highlight, theme, expanded)`;
  focus (node/edge inspection) deliberately lives OUTSIDE it, in its own
  small callbacks, so tapping never re-renders elements or steals the
  camera. The viewport re-fits only when a `view-sig` of the real view
  inputs changes, and layout dicts are identity-cached for the same reason.
* Parallel transaction rows between the same pair are aggregated into one
  drawn edge (summed amount/count, spanned dates) whose data feeds the edge
  inspector.
* Table filter/sort and the search box reset on case switch (analytic state
  must not leak across investigations); depth/min-risk persist and are
  disclosed by the caption.
* Theming is a single CSS-variable system (`assets/style.css`) switched by
  a `className` on `#root`; Dash 4's own design tokens are remapped inside
  the same file so dcc components follow the theme. The cytoscape canvas
  cannot read CSS variables, so its palette is mirrored in `CY` in `app.py`.
* Two clientside callbacks talk to the live cytoscape instance (found via
  `#graph._cyreg`): the center-subject animation, and a fit-once +
  spring-on-release handler that gives the live-physics feel.
* **Lesson learned (regression-guarded):** never place a callback `Input`
  component inside callback-rendered children — Dash re-fires the callback
  when the component is re-created. The download buttons are static and the
  download callback additionally requires a real click.
