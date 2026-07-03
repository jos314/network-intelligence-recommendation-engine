"""P1 acceptance: directed attributed graph with correct money-flow orientation,
and depth-K ego extraction with hop distances."""
import pandas as pd
import pytest

from src.graph.build import TXN, build_unified_graph
from src.graph.ego import ego_network


def _tiny_tables(code_a="DEBIT", code_b="CREDIT"):
    customers = pd.DataFrame({
        "CUSTOMER_ID": ["000000001", "000000002"],
        "CUSTOMER_NAME": ["A", "B"],
        "ADDRESS": [None, None], "PEP_FLAG": [None, None],
        "PHONE_NUMBER": [None, None], "EMAIL_ADDRESS": [None, None],
        "CRR": [None, None],
    })
    txn = pd.DataFrame([
        {"CREDIT_DEBIT_CODE": code_a, "ORIGINATOR_KEY": "000000001",
         "ORIGINATOR_COUNTRY": "US", "BENEFICIARY_KEY": "PSEUDO_9",
         "BENEFICIARY_COUNTRY": "PA", "txn_count": 3, "total_amount_base": 1000.0,
         "first_run_date": pd.Timestamp("2026-03-01"),
         "last_run_date": pd.Timestamp("2026-03-05")},
        {"CREDIT_DEBIT_CODE": code_b, "ORIGINATOR_KEY": "000000002",
         "ORIGINATOR_COUNTRY": "US", "BENEFICIARY_KEY": "PSEUDO_9",
         "BENEFICIARY_COUNTRY": None, "txn_count": 1, "total_amount_base": 500.0,
         "first_run_date": pd.Timestamp("2026-04-01"),
         "last_run_date": pd.Timestamp("2026-04-02")},
    ])
    return {
        "TRANSACTIONS": txn, "CUSTOMERS": customers,
        "CUSTOMER_ACCOUNT_LINK": pd.DataFrame({"ACCOUNT_ID": ["000000000001DDA"],
                                               "CUSTOMER_ID": [1],
                                               "FROM_DATE": [pd.Timestamp("2020-01-01")],
                                               "TO_DATE": [pd.Timestamp("2099-12-31")]}),
        "ALERTS": pd.DataFrame({"CUSTOMER_ID": ["PSEUDO_9"]}),
        "COUNTRY": pd.DataFrame({"COUNTRY_CODE": ["US", "PA"],
                                 "COUNTRY_NAME": ["United States", "Panama"],
                                 "COUNTRY_RISK": ["STANDARD", "HIGH"]}),
        "CASE_CUSTOMERS": pd.DataFrame({"CASE_ID": [1], "CUSTOMER_ID": [1], "LOB": ["CMB"]}),
    }


def test_debit_orients_originator_to_beneficiary():
    g, _ = build_unified_graph(_tiny_tables())
    assert g.has_edge("1", "PSEUDO_9")        # DEBIT: orig -> benef
    assert g.has_edge("PSEUDO_9", "2")        # CREDIT: benef -> orig
    assert not g.has_edge("PSEUDO_9", "1")
    assert not g.has_edge("2", "PSEUDO_9")


def test_node_attributes():
    g, _ = build_unified_graph(_tiny_tables())
    assert g.nodes["1"]["is_case_subject"]
    assert g.nodes["1"]["account_types"] == ["DDA"]
    assert g.nodes["PSEUDO_9"]["alerted"]
    assert g.nodes["PSEUDO_9"]["node_type"] == "external_pseudo"
    assert g.nodes["1"]["country"] == "US"
    assert g.nodes["1"]["country_risk"] == "STANDARD"


def test_ego_depth_and_hops():
    g, _ = build_unified_graph(_tiny_tables())
    ego1 = ego_network(g, "1", depth=1)
    assert set(ego1.nodes) == {"1", "PSEUDO_9"}   # reachability is undirected
    assert ego1.nodes["PSEUDO_9"]["hop"] == 1
    ego2 = ego_network(g, "1", depth=2)
    assert set(ego2.nodes) == {"1", "PSEUDO_9", "2"}
    assert ego2.nodes["2"]["hop"] == 2


def test_ego_missing_seed_raises():
    g, _ = build_unified_graph(_tiny_tables())
    with pytest.raises(KeyError):
        ego_network(g, "nope")
