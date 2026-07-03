"""P0 acceptance: the ID crosswalk collapses every raw format to one canonical id."""
import pandas as pd
import pytest

from src.ingest.crosswalk import Crosswalk, canonical_id, node_type


@pytest.mark.parametrize("raw,expected", [
    ("000031155", "31155"),        # zero-padded CUSTOMERS / ORIGINATOR_KEY
    (31155, "31155"),              # plain int CUSTOMER_ACCOUNT_LINK / CASE_CUSTOMERS
    ("31155", "31155"),
    (31155.0, "31155"),            # float that survived a cast
    ("PSEUDO_101595944", "PSEUDO_101595944"),  # pseudonym kept verbatim
    ("576700078", "576700078"),    # plain ALERTS id
    (" 000049504810 ", "49504810"),
    (None, None),
    ("<NA>", None),
    ("", None),
])
def test_canonical_id(raw, expected):
    assert canonical_id(raw) == expected


def test_nan_is_none():
    assert canonical_id(float("nan")) is None
    assert canonical_id(pd.NA) is None


def test_node_type():
    assert node_type("31155") == "customer"
    assert node_type("PSEUDO_1") == "external_pseudo"


def test_registry_merges_raw_forms():
    xw = Crosswalk()
    assert xw.add("000031155", "CUSTOMERS") == "31155"
    assert xw.add(31155, "CASE_CUSTOMERS") == "31155"
    assert len(xw) == 1
    e = xw.entry("31155")
    assert e["raw_forms"] == {"000031155", "31155"}
    assert e["source_tables"] == {"CUSTOMERS", "CASE_CUSTOMERS"}


def test_case_subjects_join_across_tables():
    """The 6 case subjects must resolve identically from every table format."""
    from src.ingest.loaders import load_tables
    from src.ingest.crosswalk import build_crosswalk
    tables = load_tables()["tables"]
    xw = build_crosswalk(tables)
    for cid in tables["CASE_CUSTOMERS"]["CUSTOMER_ID"]:
        canon = canonical_id(cid)
        entry = xw.entry(canon)
        assert "CASE_CUSTOMERS" in entry["source_tables"]
        assert "CUSTOMERS" in entry["source_tables"]     # KYC row joins
        assert "TRANSACTIONS" in entry["source_tables"]  # txn edges join
