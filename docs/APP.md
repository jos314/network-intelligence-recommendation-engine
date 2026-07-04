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

* **Header** — case selector, signed-in user chip, sign out, dark/light
  toggle (☾/☀).
* **KPI row** — decision chip, calibrated risk with gauge, alerted
  neighbours, sanctioned/watchlist count, ego size, typology flags, LOB.
* **Graph card** — the subject's ego-network.
* **Side card** — entity search, "Expand next hop", node inspector with
  risk decomposition, the node's top-20 riskiest counterparties.
* **Why card** — exact top risk drivers (red raises, green lowers) and the
  decision rationale.
* **Key risk paths card** — the propagated-risk chains, shared-attribute
  links to the subject, and download buttons (evidence pack JSON,
  conclusion prompt MD).
* **Table card** — every counterparty, sortable/filterable, CSV export.

## Graph encodings

| Visual | Meaning |
|---|---|
| node **size** | `final_risk` (bigger = riskier) |
| node **colour** | decision: red = SAR, yellow = EDD, grey = No action |
| red **ring** | TM-alerted in the last 3 months |
| blue **diamond** | the case subject (seed) |
| solid edge + arrow | transaction, arrow along **money flow**; thickness = amount relative to the ego's largest flow |
| dashed blue edge | identity link (same phone / email / address) |
| blue **glow** | the key propagated-risk path (when highlighting is on) |

## Controls

| Control | What it does |
|---|---|
| Depth (1–3) | how many hops around the subject are rendered (default 1 — overwhelm control) |
| Min risk | hides nodes below the chosen `final_risk` |
| transactions / identity links | toggle the two edge families separately |
| live physics / force / rings by hop | layout: animated springs (drag a node and the neighbourhood re-settles, Obsidian-style) / static force / concentric rings by hop distance |
| highlight key risk path | draws the top Stage-C path on the graph |
| ◎ Center subject | pans/zooms onto the seed diamond and selects it |
| Expand next hop | reveals the focused node's neighbours beyond the current depth |
| click a node | focuses it in the inspector |
| Search entity… | focus any node by name/id (ranked by risk) |

## Analyst workflow (per case)

1. Sign in, pick the case.
2. Read the KPI row — decision, calibrated score, and why at a glance.
3. Work the graph: enable the key-path highlight, expand suspicious nodes,
   check shared-attribute links (nominee signatures).
4. Work the table top-down — it is the same ego-network as rows, ranked by
   risk.
5. Download the **evidence pack** for the audit trail and the **conclusion
   prompt**; paste the prompt into the approved LLM; attach the returned
   narrative to the case.

## Implementation notes

* One big render callback drives graph, KPIs, panels, and table from
  `(case, depth, min-risk, edge kinds, layout, highlight, theme, focus,
  expanded)`. Stores (`dcc.Store`) hold auth, theme, focus, and expansion
  state.
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
