"""Table loading: real HBUS files from data/ when present, demo fixture otherwise.

Drop the real tables into data/ as TRANSACTIONS.(parquet|csv|xlsx), CUSTOMERS.*,
CUSTOMER_ACCOUNT_LINK.*, ALERTS.*, COUNTRY.*, CASE_CUSTOMERS.* and they are
picked up automatically; nothing else changes.
"""
import sys
from pathlib import Path

import pandas as pd

from .. import config
from .synthetic import generate_tables

TABLE_NAMES = ["TRANSACTIONS", "CUSTOMERS", "CUSTOMER_ACCOUNT_LINK",
               "ALERTS", "COUNTRY", "CASE_CUSTOMERS"]
_EXTS = [".parquet", ".csv", ".xlsx"]

# Columns that must stay strings: a numeric cast silently destroys the
# zero-padding / PSEUDO_ mix the crosswalk exists to fix.
_STR_COLS = {
    "TRANSACTIONS": ["ORIGINATOR_KEY", "BENEFICIARY_KEY"],
    "CUSTOMERS": ["CUSTOMER_ID"],
    "ALERTS": ["CUSTOMER_ID"],
}


def _find_file(name: str) -> Path:
    for ext in _EXTS:
        p = config.DATA_DIR / (name + ext)
        if p.exists():
            return p
    return None


def _read(path: Path, name: str) -> pd.DataFrame:
    dtype = {c: str for c in _STR_COLS.get(name, [])}
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
        for c in _STR_COLS.get(name, []):
            if c in df.columns:
                df[c] = df[c].astype(str)
        return df
    if path.suffix == ".csv":
        return pd.read_csv(path, dtype=dtype)
    return pd.read_excel(path, dtype=dtype)


def load_tables(prefer_real: bool = True, seed: int = 42) -> dict:
    """Load the six tables. Returns {"tables": ..., "source": "real"|"demo"}."""
    paths = {n: _find_file(n) for n in TABLE_NAMES}
    if prefer_real and all(p is not None for p in paths.values()):
        tables = {n: _read(p, n) for n, p in paths.items()}
        return {"tables": tables, "source": "real"}
    missing = [n for n, p in paths.items() if p is None]
    if prefer_real and len(missing) < len(TABLE_NAMES):
        print("WARNING: partial real data in data/ — missing %s; using DEMO fixture."
              % ", ".join(missing), file=sys.stderr)
    return {"tables": generate_tables(seed=seed), "source": "demo"}
