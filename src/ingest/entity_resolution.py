"""P0 — shared-attribute entity resolution (§2.2 of the build plan).

Normalize PHONE_NUMBER / EMAIL_ADDRESS / ADDRESS, then link any two parties
sharing a normalized value. Over-linking guard (blocking + Fellegi-Sunter
style weighting): values shared by <= ER_STRONG_GROUP parties are strong
(weight 1.0), up to ER_MAX_GROUP are down-weighted 1/log2(n), larger groups
(corporate HQ address, call-centre phone) are dropped as noise.
"""
import math
import re
from typing import List, Optional

import pandas as pd

from .. import config
from .crosswalk import canonical_id

_PUNCT = re.compile(r"[^\w\s@.+]")
_WS = re.compile(r"\s+")


def _norm_common(v) -> Optional[str]:
    if v is None or (pd.api.types.is_scalar(v) and pd.isna(v)):
        return None
    s = str(v).strip().lower()
    if not s or s in ("<na>", "nan", "none"):
        return None
    return s


def normalize_phone(v) -> Optional[str]:
    s = _norm_common(v)
    if s is None:
        return None
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) >= 7 else None


def normalize_email(v) -> Optional[str]:
    s = _norm_common(v)
    if s is None or "@" not in s:
        return None
    return _WS.sub("", s)


def normalize_address(v) -> Optional[str]:
    s = _norm_common(v)
    if s is None:
        return None
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s or None


_NORMALIZERS = {
    "same_phone": ("PHONE_NUMBER", normalize_phone),
    "same_email": ("EMAIL_ADDRESS", normalize_email),
    "same_address": ("ADDRESS", normalize_address),
}


def shared_attribute_links(customers: pd.DataFrame) -> pd.DataFrame:
    """Mine identity links among CUSTOMERS.

    Returns a DataFrame with columns [src, dst, kind, value, weight]
    (undirected: one row per unordered pair).
    """
    rows: List[dict] = []
    ids = customers["CUSTOMER_ID"].map(canonical_id)
    for kind, (col, norm) in _NORMALIZERS.items():
        if col not in customers.columns:
            continue
        values = customers[col].map(norm)
        frame = pd.DataFrame({"id": ids, "value": values}).dropna()
        frame = frame.drop_duplicates(subset=["id", "value"])
        for value, group in frame.groupby("value"):
            members = sorted(group["id"].unique())
            n = len(members)
            if n < 2 or n > config.ER_MAX_GROUP:
                continue  # singleton, or high-frequency noise -> dropped
            weight = 1.0 if n <= config.ER_STRONG_GROUP else 1.0 / math.log2(n)
            for i in range(n):
                for j in range(i + 1, n):
                    rows.append({"src": members[i], "dst": members[j],
                                 "kind": kind, "value": value, "weight": weight})
    return pd.DataFrame(rows, columns=["src", "dst", "kind", "value", "weight"])
