"""Synthetic demo fixture matching the profiled HBUS schema exactly.

The real six tables are not on this machine; this generator reproduces every
schema quirk documented in the project's `_session-context.md` so the whole
pipeline (crosswalk included) runs unchanged when the real data lands in
`data/`:

  * CUSTOMERS.CUSTOMER_ID zero-padded to 9 ("000031155"); sparse KYC (~17%)
  * TRANSACTIONS: pre-aggregated edges; ORIGINATOR_KEY zero-padded,
    BENEFICIARY_KEY = "PSEUDO_<n>"; CREDIT_DEBIT_CODE mostly DEBIT;
    BENEFICIARY_COUNTRY ~32% populated; dates Mar-May 2026
  * CUSTOMER_ACCOUNT_LINK.CUSTOMER_ID plain int; ACCOUNT_ID suffix = type
  * ALERTS.CUSTOMER_ID mixed plain / PSEUDO_
  * COUNTRY_RISK bands HIGH / MEDIUM / STANDARD
  * CASE_CUSTOMERS = the six real subject ids (3 CMB / 3 WPB)

Each case subject gets a distinct planted typology so every stage of the
scorer has something to find:
  1. 49504810  (CMB) circular flow (layering ring)
  2. 102117486 (WPB) PEP neighbour + HIGH-risk corridor
  3. 427024387 (WPB) shared address/phone with an alerted node (nominee)
  4. 960925086 (CMB) fan-out hub to many pseudos, several alerted (smurfing)
  5. 621173077 (WPB) clean -> "No action" demo
  6. 752495751 (CMB) dense cluster moving funds together
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

CASE_SUBJECTS = [
    (1, 49504810, "CMB"),
    (2, 102117486, "WPB"),
    (3, 427024387, "WPB"),
    (4, 960925086, "CMB"),
    (5, 621173077, "WPB"),
    (6, 752495751, "CMB"),
]

_COUNTRIES = [
    ("US", "United States", "STANDARD"),
    ("GB", "United Kingdom", "STANDARD"),
    ("DE", "Germany", "STANDARD"),
    ("MX", "Mexico", "MEDIUM"),
    ("PA", "Panama", "HIGH"),
    ("IR", "Iran", "HIGH"),
    ("KY", "Cayman Islands", "HIGH"),
    ("IT", "Italy", "STANDARD"),
    ("BR", "Brazil", "MEDIUM"),
    ("AE", "United Arab Emirates", "MEDIUM"),
]
_ACCOUNT_TYPES = ["HDD", "DDA", "MVI", "INV", "HTD", "TDA"]

_D0 = date(2026, 3, 1)  # activity window start (Mar-May 2026, as profiled)


def _pad(cid: int) -> str:
    return str(cid).zfill(9)


class _Builder:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.customers = {}   # cid(int) -> dict of KYC fields
        self.txns = []
        self.alerts = set()   # raw ALERTS ids (mixed formats)
        self._next_cid = 500_000_000
        self._next_pseudo = 100_000_000

    def new_customer(self, name=None, country="US", pep=None, crr=None,
                     phone=None, email=None, address=None, cid=None) -> int:
        if cid is None:
            cid = self._next_cid
            self._next_cid += 7
        self.customers[cid] = {
            "CUSTOMER_NAME": name or "Party %d" % cid,
            "ADDRESS": address if address is not None else "%d Main St, City %d" % (cid % 997, cid % 89),
            "PEP_FLAG": pep,
            "PHONE_NUMBER": phone,
            "EMAIL_ADDRESS": email,
            "CRR": crr,
            "_country": country,
        }
        return cid

    def new_pseudo(self) -> str:
        self._next_pseudo += 13
        return "PSEUDO_%d" % self._next_pseudo

    def txn(self, orig: int, benef, amount: float, count=None, code="DEBIT",
            orig_country=None, benef_country=None, day_offset=None):
        """One pre-aggregated TRANSACTIONS row. benef may be int (internal,
        stored zero-padded per the real originator format) or a PSEUDO_ str."""
        if day_offset is None:
            day_offset = int(self.rng.integers(0, 75))
        first = _D0 + timedelta(days=day_offset)
        last = first + timedelta(days=int(self.rng.integers(0, 15)))
        benef_key = benef if isinstance(benef, str) else _pad(benef)
        self.txns.append({
            "CREDIT_DEBIT_CODE": code,
            "ORIGINATOR_KEY": _pad(orig),
            "ORIGINATOR_COUNTRY": orig_country or self.customers.get(orig, {}).get("_country"),
            "BENEFICIARY_KEY": benef_key,
            "BENEFICIARY_COUNTRY": benef_country,
            "txn_count": count if count is not None else int(self.rng.integers(1, 40)),
            "total_amount_base": round(float(amount), 2),
            "first_run_date": pd.Timestamp(first),
            "last_run_date": pd.Timestamp(last),
        })

    def alert(self, who):
        self.alerts.add(who if isinstance(who, str) else str(who))


def generate_tables(seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    b = _Builder(rng)

    # ---- background population -------------------------------------------
    background = []
    for _ in range(400):
        country = _COUNTRIES[int(rng.integers(0, len(_COUNTRIES)))][0]
        kyc = rng.random() < 0.17  # KYC sparsity as profiled
        cid = b.new_customer(
            country=country,
            pep="N" if kyc else None,
            crr=str(rng.choice(["LOW", "MEDIUM", "HIGH"], p=[0.7, 0.2, 0.1])) if kyc else None,
            phone="+1 555 %07d" % rng.integers(0, 9_999_999) if kyc else None,
            email="party%d@mail.com" % rng.integers(0, 10**6) if kyc else None,
        )
        background.append(cid)
    for cid in background:
        for _ in range(int(rng.integers(1, 4))):
            b.txn(cid, b.new_pseudo(), float(rng.lognormal(8.5, 1.2)),
                  benef_country=_COUNTRIES[int(rng.integers(0, len(_COUNTRIES)))][0]
                  if rng.random() < 0.32 else None)
    for cid in rng.choice(background, size=25, replace=False):
        b.alert(int(cid))

    # ---- case 1: circular flow ring (CMB) --------------------------------
    s1 = b.new_customer(name="Alfa Trading LLC", country="US", crr="MEDIUM", cid=49504810)
    ring = [b.new_customer(name="Ring Co %d" % i, country=c)
            for i, c in enumerate(["US", "PA", "KY"])]
    chain = [s1] + ring
    for a, c in zip(chain, chain[1:] + [s1]):
        b.txn(a, c, 250_000 + float(rng.integers(0, 20_000)), count=12,
              benef_country=b.customers[c]["_country"])
    b.alert(ring[1])
    for _ in range(4):
        b.txn(s1, b.new_pseudo(), float(rng.lognormal(9, 1)))

    # ---- case 2: PEP neighbour + HIGH-risk corridor (WPB) -----------------
    s2 = b.new_customer(name="Beatriz Molina", country="MX", crr="MEDIUM", cid=102117486)
    pep = b.new_customer(name="Gov Official X", country="IR", pep="Y", crr="HIGH")
    b.txn(s2, pep, 480_000, count=6, benef_country="IR")
    b.txn(pep, b.new_pseudo(), 450_000, count=3, benef_country="IR")
    b.alert(pep)
    for _ in range(5):
        b.txn(s2, b.new_pseudo(), float(rng.lognormal(8, 1)), benef_country="MX")

    # ---- case 3: nominee via shared address + phone (WPB) -----------------
    shared_addr = "22 Harbour View, George Town"
    shared_phone = "+1 345 9990001"
    s3 = b.new_customer(name="Carlo Verdi", country="IT", address=shared_addr,
                        phone=shared_phone, cid=427024387)
    nominee = b.new_customer(name="CV Holdings Ltd", country="KY",
                             address=shared_addr, phone=shared_phone, crr="HIGH")
    b.alert(nominee)
    b.txn(s3, nominee, 620_000, count=18, benef_country="KY")
    b.txn(nominee, b.new_pseudo(), 600_000, count=4, benef_country="PA")
    for _ in range(3):
        b.txn(s3, b.new_pseudo(), float(rng.lognormal(8, 1)), benef_country="IT")

    # ---- case 4: smurfing fan-out hub (CMB) -------------------------------
    s4 = b.new_customer(name="Delta Imports SA", country="BR", crr="HIGH", cid=960925086)
    mules = []
    for i in range(12):
        p = b.new_pseudo()
        mules.append(p)
        b.txn(s4, p, 9_500 + float(rng.integers(0, 400)), count=30,
              benef_country=str(rng.choice(["PA", "MX", None])) if rng.random() < 0.6 else None)
    for p in mules[:4]:
        b.alert(p)  # pseudonymized alert ids, as seen in the real ALERTS
    b.alert(s4)

    # ---- case 5: clean subject (WPB) --------------------------------------
    s5 = b.new_customer(name="Elena Rossi", country="DE", crr="LOW", pep="N",
                        phone="+49 30 5550101", email="elena.rossi@mail.com",
                        cid=621173077)
    for _ in range(6):
        b.txn(s5, b.new_pseudo(), float(rng.lognormal(7, 0.6)), benef_country="DE")

    # ---- case 6: dense cluster moving funds together (CMB) ----------------
    s6 = b.new_customer(name="Foxtrot Logistics", country="US", cid=752495751)
    cluster = [b.new_customer(name="FX Partner %d" % i, country="US") for i in range(4)]
    group = [s6] + cluster
    for i, a in enumerate(group):
        for cpty in group[i + 1:]:
            b.txn(a, cpty, 80_000 + float(rng.integers(0, 30_000)), count=8,
                  benef_country="US")
    b.alert(cluster[0])
    b.alert(cluster[2])
    b.txn(s6, b.new_pseudo(), 150_000, benef_country="AE")

    # one CREDIT row so direction handling is exercised (funds benef -> orig)
    b.txn(cluster[1], s6, 55_000, count=2, code="CREDIT", benef_country="US")

    # ---- assemble the six tables ------------------------------------------
    customers = pd.DataFrame([
        {"CUSTOMER_ID": _pad(cid), "CUSTOMER_NAME": v["CUSTOMER_NAME"],
         "ADDRESS": v["ADDRESS"], "PEP_FLAG": v["PEP_FLAG"],
         "PHONE_NUMBER": v["PHONE_NUMBER"], "EMAIL_ADDRESS": v["EMAIL_ADDRESS"],
         "CRR": v["CRR"]}
        for cid, v in b.customers.items()
    ])
    transactions = pd.DataFrame(b.txns)
    link_rows = []
    for cid in b.customers:
        for _ in range(int(rng.integers(1, 3))):
            t = str(rng.choice(_ACCOUNT_TYPES))
            link_rows.append({
                "ACCOUNT_ID": "%012d%s" % (rng.integers(0, 10**11), t),
                "CUSTOMER_ID": int(cid),  # plain int, as profiled
                "FROM_DATE": pd.Timestamp(date(2020, 1, 1) + timedelta(days=int(rng.integers(0, 2000)))),
                "TO_DATE": pd.Timestamp(date(2099, 12, 31)),
            })
    account_link = pd.DataFrame(link_rows)
    alerts = pd.DataFrame({"CUSTOMER_ID": sorted(b.alerts)})
    country = pd.DataFrame(_COUNTRIES, columns=["COUNTRY_CODE", "COUNTRY_NAME", "COUNTRY_RISK"])
    case_customers = pd.DataFrame(
        [{"CASE_ID": c, "CUSTOMER_ID": cid, "LOB": lob} for c, cid, lob in CASE_SUBJECTS]
    )
    return {
        "TRANSACTIONS": transactions,
        "CUSTOMERS": customers,
        "CUSTOMER_ACCOUNT_LINK": account_link,
        "ALERTS": alerts,
        "COUNTRY": country,
        "CASE_CUSTOMERS": case_customers,
    }
