"""Central configuration: weights, decay parameters, thresholds, paths.

Every number an SME might challenge lives here, documented, per SR 11-7:
the scorecard weights, propagation decay, calibration fallback, and the
decision-band thresholds are all expert-set and inspectable.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEMO_DATA_DIR = DATA_DIR / "demo"
OUTPUT_DIR = REPO_ROOT / "output"
PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "conclusion_template.md"

# ---------------------------------------------------------------- graph
EGO_DEPTH_VIEW = 3      # on-screen graph depth (app spec)
EGO_DEPTH_SCORE = 3     # scoring depth (hackathon brief: 2nd + 3rd degree)

# -------------------------------------------- prebuilt-graph ingestion
# The masked HBUS extract (and its synthetic twin) ships GRAPH_NODES +
# GRAPH_EDGES pre-built, keyed on MASKED_CUSTOMER_ID ('CUS_' + 12 digits).
# Placeholder tokens in it are NOT signal — treat as missing:
PLACEHOLDER_ADDRESSES = {"UNKNOWN"}
PLACEHOLDER_PHONES = {"0000000000"}
CRR_UNKNOWN_TOKENS = {"N", "0000"}   # real tokens: L / N / 0000 / H / Low

# Depth-3 egos on the full-scale graph reach ~95% of all 434k nodes (the
# seeds are super-hubs). Scoring caps: expand each frontier node's top-K
# counterparties by flow amount, ALWAYS retain priority neighbours
# (alerted / PEP / high-CRR), stop at MAX nodes. Truncation is recorded on
# the ego and disclosed in the UI + evidence pack — never silent.
EGO_TOPK_SEED = 150      # direct counterparties kept for the case subject
EGO_TOPK_PER_NODE = 25   # counterparties kept per further hop node
EGO_MAX_NODES = 4000     # hard ceiling per scored network

# ------------------------------------------------- UI scale caps (disclosed)
TABLE_MAX_ROWS = 1000    # ranked counterparty table cap (top by risk)
SEARCH_MAX_OPTIONS = 1000
RENDER_MAX_NODES = 1200  # canvas cap per view; alerted/path/expanded always
                         # drawn, remainder ranked by risk (caption discloses)

# ------------------------------------------ Stage D scale guards
STAGE_D_BTW_EXACT_N = 1200    # above this, sample betweenness (k nodes)
STAGE_D_BTW_SAMPLE = 150
STAGE_D_COMMUNITY_LP_N = 800   # above this, label propagation not modularity
                               # (greedy modularity is O(n^2)-ish; LP is ~linear)
CYCLE_SCC_ENUM_MAX = 60        # enumerate short cycles only in SCCs this small;
                               # bigger SCCs are flagged by membership (linear)

# ------------------------------------------------- entity resolution (§2.2)
# A shared phone/email/address linking <= STRONG_GROUP parties is a strong
# identity signal; groups up to MAX_GROUP are down-weighted 1/log2(n);
# anything larger (corporate HQ, call centre) is dropped as noise.
ER_STRONG_GROUP = 5
ER_MAX_GROUP = 20

# ------------------------------------------------------- Stage A scorecard
# Weights sum to 1.0 so base_risk is bounded in [0, 1].
STAGE_A_WEIGHTS = {
    "alerted": 0.30,          # has TM alert (ALERTS carries no dates — recency unknown)
    "watchlist_match": 0.25,  # sanctions / World-Check hit (source TBD, Q5)
    "pep": 0.15,              # PEP_FLAG == Y
    "crr": 0.15,              # KYC Customer Risk Rating band
    "country_risk": 0.10,     # HIGH=1 / MEDIUM=0.5 / STANDARD=0
    "kyc_missing": 0.05,      # opacity is not safety
}
CRR_MAP = {"HIGH": 1.0, "H": 1.0, "MEDIUM": 0.5, "MED": 0.5, "M": 0.5,
           "LOW": 0.1, "L": 0.1, "STANDARD": 0.1}
# ('N' / '0000' are NOT ratings — cleaned to null at ingest so they count
# as KYC-missing, per the integration brief §3.4)
# No watchlist / World-Check source is connected yet (open Q5). While False,
# the UI must render sanctioned counts as "not screened", NEVER as a clean 0.
WATCHLIST_CONNECTED = False
COUNTRY_RISK_MAP = {"HIGH": 1.0, "MEDIUM": 0.5, "STANDARD": 0.0, "LOW": 0.0}

# ------------------------------------------------------- Stage B scorecard
STAGE_B_WEIGHTS = {
    "shared_attribute": 0.40,   # any same_phone / same_email / same_address
    "volume_share": 0.30,       # counterparty's share of subject's total flow
    "country_change": 0.15,     # flow crosses a border vs the subject
    "capital_ratio_shared": 0.15,  # proxy = volume concentration (open Q8)
}

# ---------------------------------------------------- Stage C propagation
PROP_METHOD = "ppr"       # "ppr" (recommended) or "khop" (more traceable)
PPR_ALPHA = 0.85          # damping; restart mass 1-alpha on bad seeds
KHOP_GAMMA = 0.5          # per-hop decay for the bounded diffusion
TIME_DECAY_TAU_DAYS = 180.0   # edge weight decays exp(-age/tau)
BAD_SEED_THRESHOLD = 0.30     # base_risk >= this makes a node a risk seed

# ---------------------------------------------------- Stage D structural
STAGE_D_WEIGHTS = {"cycle": 0.40, "community": 0.30, "centrality": 0.30}
MAX_CYCLE_LEN = 6
MIN_COMMUNITY_SIZE = 3

# ---------------------------------------------------- Stage E aggregation
STAGE_E_WEIGHTS = {"base": 0.35, "rel": 0.20, "prop": 0.30, "struct": 0.15}

# ------------------------------------------------ Stage F/G decision layer
MIN_CALIBRATION_POSITIVES = 20   # fewer weak labels -> fallback mapping
DECISION_T1 = 0.40   # p <  t1 -> No action
DECISION_T2 = 0.75   # p >= t2 -> SAR
# Hard overrides (checked before bands, fully auditable):
OVERRIDE_PROP_RISK = 0.50   # active alert AND prop_risk >= this -> at least EDD
# Case-level proximity-to-risk escalation (§4.G: the case decision weighs the
# network's evidence, not the subject node alone): subject strongly connected
# to at least one alerted/sanctioned entity -> at least EDD.
CASE_PROP_ESCALATION = 0.60

DECISION_NO_ACTION = "No action"
DECISION_EDD = "EDD"
DECISION_SAR = "SAR"

TOP_COUNTERPARTIES = 20  # side-panel "top x riskiest counterparties"
