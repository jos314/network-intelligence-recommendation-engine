"""P0 — ID crosswalk (§2.1 of the build plan).

The same customer appears three ways across the six tables:
  * CUSTOMERS.CUSTOMER_ID / TRANSACTIONS.ORIGINATOR_KEY -> zero-padded string ("000031155")
  * CUSTOMER_ACCOUNT_LINK / CASE_CUSTOMERS .CUSTOMER_ID -> plain int (31155)
  * TRANSACTIONS.BENEFICIARY_KEY / some ALERTS rows      -> pseudonym ("PSEUDO_101595944")

Canonical id = integer value as a string for real customers; PSEUDO_ ids verbatim.
"""
from typing import Optional

import pandas as pd

NODE_CUSTOMER = "customer"
NODE_EXTERNAL = "external_pseudo"


def canonical_id(raw) -> Optional[str]:
    """Normalize any raw identifier to its canonical form (None for blanks)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if pd.api.types.is_scalar(raw) and pd.isna(raw):
        return None
    s = str(raw).strip()
    if not s or s.upper() in ("<NA>", "NAN", "NONE"):
        return None
    if s.upper().startswith("PSEUDO_"):
        return s
    # zero-padded / plain numerics collapse to the integer value as a string
    if s.isdigit():
        return str(int(s))
    # floats that survived a cast, e.g. "31155.0"
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


def node_type(canon: str) -> str:
    return NODE_EXTERNAL if canon.upper().startswith("PSEUDO_") else NODE_CUSTOMER


class Crosswalk:
    """Registry: canonical_id -> {raw_forms, node_type, source_tables}."""

    def __init__(self):
        self._reg = {}

    def add(self, raw, source_table: str) -> Optional[str]:
        canon = canonical_id(raw)
        if canon is None:
            return None
        entry = self._reg.setdefault(
            canon, {"raw_forms": set(), "node_type": node_type(canon), "source_tables": set()}
        )
        entry["raw_forms"].add(str(raw).strip())
        entry["source_tables"].add(source_table)
        return canon

    def add_series(self, series: pd.Series, source_table: str) -> pd.Series:
        """Vectorized add; returns the canonicalized series."""
        canon = series.map(canonical_id)
        for raw, c in zip(series, canon):
            if c is not None:
                entry = self._reg.setdefault(
                    c, {"raw_forms": set(), "node_type": node_type(c), "source_tables": set()}
                )
                entry["raw_forms"].add(str(raw).strip())
                entry["source_tables"].add(source_table)
        return canon

    def __contains__(self, canon: str) -> bool:
        return canon in self._reg

    def __len__(self) -> int:
        return len(self._reg)

    def entry(self, canon: str) -> dict:
        return self._reg[canon]

    def ids(self):
        return self._reg.keys()


def build_crosswalk(tables: dict) -> Crosswalk:
    """Register every identifier column of the six tables."""
    xw = Crosswalk()
    xw.add_series(tables["CUSTOMERS"]["CUSTOMER_ID"], "CUSTOMERS")
    xw.add_series(tables["TRANSACTIONS"]["ORIGINATOR_KEY"], "TRANSACTIONS")
    xw.add_series(tables["TRANSACTIONS"]["BENEFICIARY_KEY"], "TRANSACTIONS")
    xw.add_series(tables["CUSTOMER_ACCOUNT_LINK"]["CUSTOMER_ID"], "CUSTOMER_ACCOUNT_LINK")
    xw.add_series(tables["ALERTS"]["CUSTOMER_ID"], "ALERTS")
    xw.add_series(tables["CASE_CUSTOMERS"]["CUSTOMER_ID"], "CASE_CUSTOMERS")
    return xw
