"""Loader for the prebuilt masked-graph extract (8 parquet tables).

The real masked HBUS files — and their synthetic twin from
scripts/generate_synthetic_aml_data.py — ship the graph PRE-BUILT:

  GRAPH_NODES  (434k x 11)  node master keyed on MASKED_CUSTOMER_ID
  GRAPH_EDGES  (802k x 24)  directed edges with features already joined

plus the six raw tables for reference/enrichment. This module finds the
files, loads them, and cleans the placeholder tokens that are NOT signal
(integration brief §3.4): ADDRESS 'UNKNOWN', PHONE '0000000000',
CRR 'N'/'0000' all become null so KYC-missingness fires instead of a fake
match or a fake rating.

Search order: data/ first (where the real extract will land), then
data/synthetic/ (the generated twin). Nothing downstream knows which.
"""
from pathlib import Path

import pandas as pd

from .. import config

PREBUILT_TABLES = ["GRAPH_NODES", "GRAPH_EDGES", "CASE_CUSTOMERS",
                   "CUSTOMERS", "COUNTRY", "ALERTS", "TRANSACTIONS"]
# the account-link file exists under two spellings across extracts
_ACCOUNT_LINK_NAMES = ["CUSTOMERS_ACCOUNT_LINK", "CUSTOMER_ACCOUNT_LINK"]


def find_prebuilt_dir() -> Path:
    """Directory containing GRAPH_NODES + GRAPH_EDGES parquet, or None."""
    for base in (config.DATA_DIR, config.DATA_DIR / "synthetic"):
        if (base / "GRAPH_NODES.parquet").exists() and \
           (base / "GRAPH_EDGES.parquet").exists():
            return base
    return None


def _clean_nodes(nodes: pd.DataFrame) -> pd.DataFrame:
    nodes = nodes.copy()
    addr = nodes["ADDRESS"].astype("object")
    nodes["ADDRESS"] = addr.where(~addr.isin(config.PLACEHOLDER_ADDRESSES), None)
    phone = nodes["PHONE_NUMBER"].astype("object")
    nodes["PHONE_NUMBER"] = phone.where(~phone.isin(config.PLACEHOLDER_PHONES), None)
    crr = nodes["CRR"].astype("object")
    nodes["CRR"] = crr.where(~crr.isin(config.CRR_UNKNOWN_TOKENS), None)
    return nodes


def load_prebuilt_tables(base: Path) -> dict:
    """Load the 8 tables (placeholders cleaned on GRAPH_NODES)."""
    tables = {}
    for name in PREBUILT_TABLES:
        path = base / ("%s.parquet" % name)
        if not path.exists():
            raise FileNotFoundError("prebuilt table missing: %s" % path)
        tables[name] = pd.read_parquet(path)
    link = None
    for name in _ACCOUNT_LINK_NAMES:
        path = base / ("%s.parquet" % name)
        if path.exists():
            link = pd.read_parquet(path)
            break
    tables["CUSTOMER_ACCOUNT_LINK"] = link  # may be None; only used for counts
    tables["GRAPH_NODES"] = _clean_nodes(tables["GRAPH_NODES"])
    return tables
