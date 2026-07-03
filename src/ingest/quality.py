"""P0 — data-quality checklist (§2.3), run at ingest and reported, never fatal.

Each check maps to an open question in the build plan (Q2-Q4, Q6).
"""
import pandas as pd

EXCEL_ROW_LIMIT = 1_048_575  # 2**20 - 1


def run_quality_checks(tables: dict) -> list:
    """Return a list of {check, status, detail} dicts. status: ok|warn."""
    findings = []

    country = tables["COUNTRY"]
    bands = sorted(country["COUNTRY_RISK"].dropna().unique())
    findings.append({
        "check": "COUNTRY_RISK bands (Q2)",
        "status": "warn" if "STANDARD" in bands else "ok",
        "detail": "bands seen: %s (spec said HIGH/MEDIUM/LOW; STANDARD treated as LOW)" % bands,
    })

    link = tables["CUSTOMER_ACCOUNT_LINK"]
    truncated = len(link) == EXCEL_ROW_LIMIT
    findings.append({
        "check": "CUSTOMER_ACCOUNT_LINK Excel truncation (Q4)",
        "status": "warn" if truncated else "ok",
        "detail": "%d rows%s" % (len(link), " = 2^20-1, likely truncated at Excel's limit" if truncated else ""),
    })

    txn = tables["TRANSACTIONS"]
    codes = txn["CREDIT_DEBIT_CODE"].value_counts().to_dict()
    findings.append({
        "check": "CREDIT_DEBIT_CODE coverage (Q3)",
        "status": "warn" if "CREDIT" not in codes else "ok",
        "detail": "code counts: %s" % codes,
    })

    ben_cov = txn["BENEFICIARY_COUNTRY"].notna().mean()
    findings.append({
        "check": "BENEFICIARY_COUNTRY coverage",
        "status": "warn" if ben_cov < 0.5 else "ok",
        "detail": "%.0f%% populated — corridor features degrade gracefully on the beneficiary side" % (100 * ben_cov),
    })

    pseudo_share = txn["BENEFICIARY_KEY"].astype(str).str.upper().str.startswith("PSEUDO_").mean()
    findings.append({
        "check": "Pseudonymized beneficiaries (Q6)",
        "status": "ok",
        "detail": "%.0f%% of beneficiaries are PSEUDO_ externals with no KYC row" % (100 * pseudo_share),
    })

    for col in ("PEP_FLAG", "PHONE_NUMBER", "EMAIL_ADDRESS", "CRR"):
        cov = tables["CUSTOMERS"][col].notna().mean()
        findings.append({
            "check": "CUSTOMERS.%s coverage" % col,
            "status": "warn" if cov < 0.5 else "ok",
            "detail": "%.0f%% populated" % (100 * cov),
        })
    return findings


def print_report(findings: list) -> None:
    for f in findings:
        print("[%s] %s — %s" % (f["status"].upper(), f["check"], f["detail"]))
