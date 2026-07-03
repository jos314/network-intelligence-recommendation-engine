"""P0 acceptance: shared-attribute links built with the over-linking guard."""
import pandas as pd

from src import config
from src.ingest.entity_resolution import (normalize_address, normalize_email,
                                          normalize_phone, shared_attribute_links)


def test_normalizers():
    assert normalize_phone("+1 (345) 999-0001") == "13459990001"
    assert normalize_phone("<NA>") is None
    assert normalize_phone("911") is None            # too short
    assert normalize_email(" Ana.SV@Mail.com ") == "ana.sv@mail.com"
    assert normalize_email("not-an-email") is None
    assert normalize_address("22, Harbour View;  George Town") == "22 harbour view george town"


def _customers(rows):
    return pd.DataFrame(rows, columns=["CUSTOMER_ID", "PHONE_NUMBER",
                                       "EMAIL_ADDRESS", "ADDRESS"])


def test_small_group_links_are_strong():
    df = _customers([("000000001", "+1 555 0000001", None, None),
                     ("000000002", "+1 555 0000001", None, None)])
    links = shared_attribute_links(df)
    assert len(links) == 1
    row = links.iloc[0]
    assert {row["src"], row["dst"]} == {"1", "2"}
    assert row["kind"] == "same_phone"
    assert row["weight"] == 1.0


def test_overlinking_guard_drops_hq_values():
    """A value shared by > ER_MAX_GROUP parties (corporate HQ) is noise."""
    n = config.ER_MAX_GROUP + 1
    df = _customers([(str(i), None, None, "1 Corporate HQ Plaza") for i in range(n)])
    links = shared_attribute_links(df)
    assert links.empty


def test_midsize_group_downweighted():
    n = config.ER_STRONG_GROUP + 3
    df = _customers([(str(i), None, "team@corp.com", None) for i in range(n)])
    links = shared_attribute_links(df)
    assert not links.empty
    assert (links["weight"] < 1.0).all()
