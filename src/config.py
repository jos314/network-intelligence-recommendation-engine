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
EGO_DEPTH_SCORE = 4     # scoring depth cap (hackathon spec)

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
