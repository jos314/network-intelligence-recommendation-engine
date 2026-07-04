#!/usr/bin/env python3
"""
generate_synthetic_aml_data.py  (v2 — calibrated to the real HBUS distributions)
================================================================================
Synthetic AML dataset generator for the "AI-Enabled Network Intelligence &
Recommendation Engine" hackathon project.

WHY: the real (masked) HBUS parquet files live on another machine and can't be
transferred. This reproduces that data model at the SAME scale and now with
DISTRIBUTIONS calibrated from inspect_real_data.py, so the app can be built and
demoed anywhere, then swapped onto the real files unchanged.

REAL STRUCTURE THIS MIRRORS (from the inspection):
  * 398,390 CUSTOMERS, but only ~74,428 are "active" (appear in the graph, hold
    accounts, carry KYC). The other ~324k are name+address-only shells.
  * GRAPH_NODES = 74,428 HBUS + 359,618 PSEUDO externals = 434,046.
  * Canonical graph id = 'CUS_' + 12 digits (MASKED_CUSTOMER_ID). A crosswalk
    (ORIGINAL_CUSTOMER_ID: zero-padded-9 for HBUS, 'PSEUDO_n' for externals) is
    REQUIRED to go from CASE_CUSTOMERS (plain int) to the graph — mirrors reality.
  * Heavy tails: total_amount_base median ~6k / max ~7.5B; txn_count median 4;
    accounts/customer median 7 (only ~63,807 customers hold accounts).
  * Real encodings: CRR in {L,N,0000,H,Low}; PEP almost all 'N' (few 'Y');
    ADDRESS is literally 'UNKNOWN' ~68% of the time; phone placeholder '0000000000';
    EDGE_DIRECTION in {ORG_to_BEN, BEN_to_ORG}; CREDIT ~23%; PSEUDO beneficiary ~77%.

Values tagged  # real  come straight from the inspection.  # CALIBRATE  = still a guess.

DEPS: pandas, numpy, pyarrow.   Optional: faker (falls back to built-in pools).
RUN : python generate_synthetic_aml_data.py --out ./synthetic_data
      python generate_synthetic_aml_data.py --scale 0.05     # quick smoke test
"""
from __future__ import annotations
import argparse, os, string, warnings
import numpy as np
import pandas as pd
warnings.simplefilter("ignore", FutureWarning)

# ============================== CONFIG ======================================
CONFIG = dict(
    N_CUSTOMERS      = 398_390,     # real  CUSTOMERS rows
    N_ACTIVE         = 74_428,      # real  customers that appear in the graph (IS_HBUS)
    N_PSEUDO         = 359_618,     # real  external beneficiaries (IS_PSEUDO)  -> nodes 434,046
    N_TXN_EDGES      = 802_061,     # real
    N_ACCOUNT_LINKS  = 1_048_575,   # matches the inspected file (real untruncated ~1.25M; bump if you want)
    N_ACCT_CUSTOMERS = 63_807,      # real  customers that hold accounts (subset of active)
    N_ALERTS         = 692,         # real
    N_GRAPH_ALERTED  = 162,         # real  GRAPH_NODES.HAS_PREV_TM_ALERT == 1
    SEED             = 42,

    # KYC fill RATE AMONG ACTIVE customers (real counts / 74,428)
    ACTIVE_FILL_CRR   = 0.997,      # 74,186
    ACTIVE_FILL_PEP   = 0.917,      # 68,286
    ACTIVE_FILL_PHONE = 0.873,      # 64,949
    ACTIVE_FILL_EMAIL = 0.940,      # 69,959
    ACTIVE_FILL_REL   = 0.994,      # 74,000 relationship-start dates
    FILL_ADDRESS      = 0.996,      # real  overall (active + inactive)
    ADDRESS_UNKNOWN_FRAC = 0.681,   # real  270,413 / 396,817 addresses are literally 'UNKNOWN'
    PHONE_PLACEHOLDER_FRAC = 0.011, # real  698 / 64,949 phones are '0000000000'
    PEP_Y_FRAC        = 0.00063,    # real  43 'Y' out of 68,286

    CRR_VALUES  = ["L", "N", "0000", "H", "Low"],          # real tokens
    CRR_WEIGHTS = [0.9310, 0.0389, 0.0204, 0.0097, 0.00001],# real freq (L dominates)

    # transactions
    CREDIT_FRAC      = 0.234,       # real  187,998 / 802,061
    PSEUDO_BENE_FRAC = 0.766,       # real  % beneficiary PSEUDO
    FILL_ORIG_COUNTRY= 0.809,       # real  648,889 / 802,061
    FILL_BENE_COUNTRY= 0.322,       # real  258,100 / 802,061
    US_HOME_FRAC     = 0.94,        # real  originator country ~94% US
    AMOUNT_LOG_MEAN  = 8.70,        # real  median ~6,000  (ln)
    AMOUNT_LOG_SIGMA = 2.75,        # real  p99 ~3.6M, max ~7.5B
    TXN_LOG_MEAN     = 1.39,        # real  median 4
    TXN_LOG_SIGMA    = 1.29,        # real  p99 ~80, mean ~10.6
    ORIG_ZIPF        = 1.30,        # real  out-degree median 1, p90 8, p99 28
    PSEUDO_ZIPF      = 1.05,        # real  in-degree median 1, p99 14, hub max ~31k
    DATE_START       = "2026-03-02",
    DATE_END         = "2026-05-29",

    # accounts
    ACCT_LOG_MEAN    = 1.95,        # real  accounts/customer median ~7
    ACCT_LOG_SIGMA   = 1.20,        # real  p90 ~35, p99 ~115
    ACCT_ACTIVE_FRAC = 0.413,       # real  % TO_DATE == 2099-12-31
    ACCT_1900_FRAC   = 0.08,        # real  FROM_DATE floor of 1900 for a placeholder slice

    SEEDS     = [49504810, 102117486, 427024387, 960925086, 621173077, 752495751],
    SEED_LOB  = ["CMB", "WPB", "WPB", "CMB", "WPB", "CMB"],
    N_ID_CLUSTERS = 500,            # shared phone/email/address clusters (shell/nominee typology)

    # real account-type suffixes with real weights
    ACCT_SUFFIXES = ["MVI","TDA","DDA","HDD","TSY","CPI","INV","AFS","HTD","HGN","LIQ","NIE","ILS","SBA","SLS"],
    ACCT_SUFFIX_W = [291925,240145,186539,93480,55401,46495,30319,29355,26073,15570,8676,6213,4683,4017,3966],
)

# originator/home countries: ~94% US, then the real tail
HOME_CC_POOL = (["US"] * 235 + ["GB"] * 3 + ["CA", "CN", "HK", "FR", "AU", "IS", "SG", "JP", "MX", "DE", "IN", "BR"])

REAL_COUNTRIES = [
    ("US","UNITED STATES"),("GB","UNITED KINGDOM"),("MX","MEXICO"),("CA","CANADA"),("DE","GERMANY"),
    ("FR","FRANCE"),("ES","SPAIN"),("IT","ITALY"),("NL","NETHERLANDS"),("CH","SWITZERLAND"),("IN","INDIA"),
    ("CN","CHINA"),("HK","HONG KONG"),("SG","SINGAPORE"),("AE","UNITED ARAB EMIRATES"),("BR","BRAZIL"),
    ("AU","AUSTRALIA"),("JP","JAPAN"),("KR","SOUTH KOREA"),("RU","RUSSIAN FEDERATION"),("TR","TURKEY"),
    ("SA","SAUDI ARABIA"),("ZA","SOUTH AFRICA"),("AR","ARGENTINA"),("CL","CHILE"),("CO","COLOMBIA"),
    ("PE","PERU"),("AF","AFGHANISTAN"),("IR","IRAN"),("KP","NORTH KOREA"),("SY","SYRIA"),("MM","MYANMAR"),
    ("YE","YEMEN"),("SS","SOUTH SUDAN"),("LY","LIBYA"),("SO","SOMALIA"),("VE","VENEZUELA"),("AO","ANGOLA"),
    ("DZ","ALGERIA"),("AG","ANTIGUA AND BARBUDA"),("IQ","IRAQ"),("ZW","ZIMBABWE"),("HT","HAITI"),
    ("AL","ALBANIA"),("PA","PANAMA"),("CY","CYPRUS"),("MT","MALTA"),("LB","LEBANON"),("NG","NIGERIA"),
    ("PK","PAKISTAN"),("KY","CAYMAN ISLANDS"),("VG","BRITISH VIRGIN ISLANDS"),("LU","LUXEMBOURG"),
    ("PH","PHILIPPINES"),("PF","FRENCH POLYNESIA"),("IS","ICELAND"),("IE","IRELAND"),("PT","PORTUGAL"),
    ("BE","BELGIUM"),("SE","SWEDEN"),("NO","NORWAY"),("DK","DENMARK"),("FI","FINLAND"),("PL","POLAND"),
    ("AT","AUSTRIA"),("GR","GREECE"),
]
STANDARD_MAJORS = {"US","GB","CA","DE","FR","AU","JP","CH","NL","SE","NO","SG","IE","NZ","FI","DK","IS","AT","BE"}


# ---------------------------------------------------------------------------
def sample_unique_ints(rng, n, low, high, exclude=None):
    exclude = set() if exclude is None else set(int(x) for x in exclude)
    out = set()
    while len(out) < n:
        need = n - len(out)
        batch = rng.integers(low, high, size=int(need * 1.15) + 16)
        out.update(int(v) for v in batch.tolist() if v not in exclude)
    return np.array(list(out)[:n], dtype="int64")


def zpad(int_ids, width=9):
    return pd.Series(int_ids).astype("int64").astype(str).str.zfill(width)


def make_pools(rng, n_pool=20_000):
    try:
        from faker import Faker
        fk = Faker(["en_US", "en_GB"]); Faker.seed(int(rng.integers(1e9)))
        persons = [fk.name() for _ in range(n_pool)]
        streets = [fk.street_address() for _ in range(4000)]
        cities  = [f"{fk.city()} {fk.state_abbr()} {fk.postcode()}" for _ in range(4000)]
        addrs = [f"{rng.choice(streets)}, {rng.choice(cities)}" for _ in range(n_pool)]
        companies = [fk.company() for _ in range(4000)]
    except Exception:
        fn = ["JAMES","MARY","JOHN","PATRICIA","ROBERT","JENNIFER","HUA","WEI","AMIT","PRIYA",
              "CARLOS","MARIA","AHMED","FATIMA","ERIC","MICHELLE","SOFIA","OMAR","YUKI","LARS"]
        ln = ["SMITH","JOHNSON","GARCIA","ZHANG","WANG","LI","BAHREE","YADAV","RIVERA","KHAN",
              "PATEL","ROSSI","MUELLER","SILVA","TANAKA","KIM","OKAFOR","HANSEN","NOVAK","STUART"]
        persons = [f"{rng.choice(fn)} {rng.choice(ln)}" for _ in range(n_pool)]
        st = ["MAIN ST","OAK AVE","RODEO RD","PARK BLVD","CEDAR LN","2ND ST","ELM DR","5TH AVE"]
        ct = ["LOS ANGELES CA 90046","BRONX NY 10465","BELLEVUE WA 98008","MIAMI FL 33101","HOUSTON TX 77002"]
        addrs = [f"{rng.integers(1,9999)} {rng.choice(st)}, {rng.choice(ct)}" for _ in range(n_pool)]
        companies = [f"{rng.choice(ln).title()} {s}" for s in ["Inc","LLC","Ltd","Group","Holdings","Partners"] * 700]
    suf = ["Inc","LLC","Ltd","US Inc","Group","Holdings","Corp"]
    companies = [f"{c.split(',')[0]} {rng.choice(suf)}" for c in companies]
    doms = ["gmail.com","yahoo.com","hotmail.com","icloud.com","outlook.com","aol.com","live.com"]
    return (np.array(persons, dtype=object), np.array(companies, dtype=object),
            np.array(addrs, dtype=object), doms)


def build_country(rng, cfg):
    rows = list(REAL_COUNTRIES)
    used = {c for c, _ in rows}
    while len(rows) < 272:
        code = rng.choice(list(string.ascii_uppercase)) + rng.choice(list(string.ascii_uppercase))
        if code not in used:
            used.add(code); rows.append((code, f"COUNTRY {code}"))
    codes = [c for c, _ in rows][:272]; names = [n for _, n in rows][:272]
    # assign risk: majors STANDARD; then ~137 HIGH / ~88 MEDIUM / rest STANDARD  (real: HIGH freq 137)
    risk = np.array(["STANDARD"] * 272, dtype=object)
    idx = [i for i, c in enumerate(codes) if c not in STANDARD_MAJORS]
    rng.shuffle(idx)
    for i in idx[:137]:
        risk[i] = "HIGH"
    for i in idx[137:137 + 88]:
        risk[i] = "MEDIUM"
    df = pd.DataFrame({"COUNTRY_CODE": codes, "COUNTRY_NAME": names, "COUNTRY_RISK": risk})
    df.loc[df.index[-1], "COUNTRY_CODE"] = None                 # 1 null code (real 271/272)
    return df, {c: r for c, r in zip(codes, risk)}


def build_customers(rng, cfg, persons, companies, addrs, doms):
    n, na = cfg["N_CUSTOMERS"], cfg["N_ACTIVE"]
    rest = sample_unique_ints(rng, n - len(cfg["SEEDS"]), 10_000, 999_999_999, exclude=cfg["SEEDS"])
    ids = np.concatenate([np.array(cfg["SEEDS"], dtype="int64"), rest])   # seeds first
    active = np.zeros(n, dtype=bool); active[:na] = True                  # first N_ACTIVE are active

    # names: ~12% companies
    name = persons[rng.integers(0, len(persons), n)].astype(object)
    cm = rng.random(n) < 0.12
    name[cm] = companies[rng.integers(0, len(companies), cm.sum())]

    # address: 99.6% present; of those 68.1% literally 'UNKNOWN'
    addr = np.array([pd.NA] * n, dtype=object)
    has_addr = rng.random(n) < cfg["FILL_ADDRESS"]
    real_addr = has_addr & (rng.random(n) >= cfg["ADDRESS_UNKNOWN_FRAC"])
    addr[has_addr] = "UNKNOWN"
    addr[real_addr] = addrs[rng.integers(0, len(addrs), real_addr.sum())]

    def active_fill(rate):
        m = np.zeros(n, dtype=bool); m[active] = rng.random(active.sum()) < rate; return m

    pep = np.array([pd.NA] * n, dtype=object)
    pm = active_fill(cfg["ACTIVE_FILL_PEP"])
    pep[pm] = np.where(rng.random(pm.sum()) < cfg["PEP_Y_FRAC"], "Y", "N")

    phone = np.array([pd.NA] * n, dtype=object)
    hm = active_fill(cfg["ACTIVE_FILL_PHONE"])
    ph = rng.integers(2_000_000_000, 9_999_999_999, hm.sum()).astype(str)
    plc = rng.random(hm.sum()) < cfg["PHONE_PLACEHOLDER_FRAC"]
    ph[plc] = "0000000000"
    phone[hm] = ph

    email = np.array([pd.NA] * n, dtype=object)
    em = active_fill(cfg["ACTIVE_FILL_EMAIL"])
    h = np.char.add(np.char.replace(name[em].astype("U"), " ", ".").astype("U"),
                    rng.integers(1, 999, em.sum()).astype("U"))
    email[em] = np.char.lower(np.char.add(np.char.add(h, "@"), np.array(doms)[rng.integers(0, len(doms), em.sum())].astype("U")))

    crr = np.array([pd.NA] * n, dtype=object)
    crm = active_fill(cfg["ACTIVE_FILL_CRR"])
    crr[crm] = rng.choice(cfg["CRR_VALUES"], crm.sum(), p=np.array(cfg["CRR_WEIGHTS"]) / sum(cfg["CRR_WEIGHTS"]))

    customers = pd.DataFrame({
        "CUSTOMER_ID": zpad(ids, 9).astype("string"),
        "CUSTOMER_NAME": pd.array(name.astype(str), dtype="string"),
        "ADDRESS": pd.array(addr, dtype="string"),
        "PEP_FLAG": pep, "PHONE_NUMBER": pd.array(phone, dtype="string"),
        "EMAIL_ADDRESS": email, "CRR": crr,
    })
    meta = pd.DataFrame({"cid": ids, "active": active, "addr": addr, "phone": phone,
                         "email": email, "pep": pep, "crr": crr, "has_crr": crm})
    return customers, meta


def plant_typologies(rng, cfg, customers, meta, active_ids):
    extra = []
    pool = rng.choice(active_ids, size=min(len(active_ids), cfg["N_ID_CLUSTERS"] * 6), replace=False)
    row_of = pd.Series(np.arange(len(meta)), index=meta["cid"].to_numpy())
    ci = {c: customers.columns.get_loc(c) for c in ("ADDRESS", "PHONE_NUMBER", "EMAIL_ADDRESS")}
    mi = {c: meta.columns.get_loc(c) for c in ("addr", "phone", "email")}
    pi = 0
    for k in range(cfg["N_ID_CLUSTERS"]):
        size = int(rng.integers(3, 7))
        if pi + size > len(pool):
            break
        members = pool[pi:pi + size]; pi += size
        rows = [int(row_of[m]) for m in members]
        a = f"{int(rng.integers(100,9999))} SHELL PLAZA STE {k}, DOVER DE 199010001"
        customers.iloc[rows, ci["ADDRESS"]] = a; meta.iloc[rows, mi["addr"]] = a
        if rng.random() < 0.6:
            p = str(int(rng.integers(2_000_000_000, 9_999_999_999)))
            customers.iloc[rows, ci["PHONE_NUMBER"]] = p; meta.iloc[rows, mi["phone"]] = p
        if rng.random() < 0.5:
            e = f"cluster{k}@maildrop.example"
            customers.iloc[rows, ci["EMAIL_ADDRESS"]] = e; meta.iloc[rows, mi["email"]] = e
        for i in range(len(members)):
            extra.append((int(members[i]), int(members[(i + 1) % len(members)])))
        if k < len(cfg["SEEDS"]):
            extra.append((int(cfg["SEEDS"][k]), int(members[0])))
    ring = [int(cfg["SEEDS"][0])] + [int(x) for x in rng.choice(active_ids, 3, replace=False)]
    for i in range(len(ring)):
        extra.append((ring[i], ring[(i + 1) % len(ring)]))
    return extra


def build_edges(rng, cfg, active_ids, pseudo_ids, extra_edges):
    n = cfg["N_TXN_EDGES"]
    na, npd = len(active_ids), len(pseudo_ids)
    wo = 1.0 / np.power(np.arange(1, na + 1), cfg["ORIG_ZIPF"]); wo /= wo.sum()
    wp = 1.0 / np.power(np.arange(1, npd + 1), cfg["PSEUDO_ZIPF"]); wp /= wp.sum()

    ex_src = [int(s) for s, d in extra_edges]; ex_dst = [int(d) for s, d in extra_edges]
    nb = n - len(ex_src)                                    # background edges (extra = planted HBUS->HBUS)

    # originators: cover every active once, then weighted heavy tail
    bg_src = np.empty(nb, dtype="int64"); ncov = min(na, nb)
    bg_src[:ncov] = rng.permutation(active_ids)[:ncov]
    if nb > ncov:
        bg_src[ncov:] = rng.choice(active_ids, nb - ncov, p=wo)

    # beneficiaries: PSEUDO_BENE_FRAC pseudo (cover every pseudo once, then popularity), rest HBUS
    is_p = rng.random(nb) < cfg["PSEUDO_BENE_FRAC"]
    bg_dst = np.empty(nb, dtype=object)
    pidx = np.where(is_p)[0]; cov = min(npd, len(pidx)); perm = rng.permutation(pseudo_ids)
    bg_dst[pidx[:cov]] = perm[:cov]
    if len(pidx) > cov:
        bg_dst[pidx[cov:]] = rng.choice(pseudo_ids, len(pidx) - cov, p=wp)
    hidx = np.where(~is_p)[0]
    bg_dst[hidx] = rng.choice(active_ids, len(hidx))

    src = np.concatenate([np.array(ex_src, dtype="int64"), bg_src]).astype("int64")
    dst = np.concatenate([np.array(ex_dst, dtype=object), bg_dst])
    order = rng.permutation(n); src, dst = src[order], dst[order]
    dst_is_p = np.array([isinstance(x, str) for x in dst])

    credit = rng.random(n) < cfg["CREDIT_FRAC"]
    cdc = np.where(credit, "CREDIT", "DEBIT").astype(object)
    txn = np.maximum(1, np.round(rng.lognormal(cfg["TXN_LOG_MEAN"], cfg["TXN_LOG_SIGMA"], n))).astype("int64")
    amt = np.round(rng.lognormal(cfg["AMOUNT_LOG_MEAN"], cfg["AMOUNT_LOG_SIGMA"], n), 2)
    d0, d1 = np.datetime64(cfg["DATE_START"]), np.datetime64(cfg["DATE_END"])
    span = (d1 - d0).astype("timedelta64[D]").astype(int)
    fr = d0 + rng.integers(0, span, n).astype("timedelta64[D]")
    lr = np.minimum(fr + rng.integers(0, 30, n).astype("timedelta64[D]"), d1)
    return pd.DataFrame({"src_int": src, "dst_obj": dst, "dst_is_p": dst_is_p, "CREDIT_DEBIT_CODE": cdc,
                         "txn_count": txn, "total_amount_base": amt, "first_run_date": fr, "last_run_date": lr})


def add_countries(rng, cfg, e, home_cc):
    n = len(e)
    src_home = home_cc(e["src_int"].to_numpy())
    oc = src_home.copy(); oc[~(rng.random(n) < cfg["FILL_ORIG_COUNTRY"])] = None
    bc = np.array([None] * n, dtype=object)
    bm = rng.random(n) < cfg["FILL_BENE_COUNTRY"]
    bc[bm] = np.array(HOME_CC_POOL)[rng.integers(0, len(HOME_CC_POOL), bm.sum())]
    src_country = src_home.copy(); src_country[~(rng.random(n) < 0.9997)] = None
    dst_country = np.array([None] * n, dtype=object)                 # ~13% populated (HBUS bene)
    hb = ~e["dst_is_p"].to_numpy()
    dm = hb & (rng.random(n) < 0.55)
    di = pd.to_numeric(e["dst_obj"], errors="coerce").to_numpy()
    dst_country[dm] = home_cc(np.nan_to_num(di[dm]).astype("int64"))
    e["ORIGINATOR_COUNTRY"] = oc; e["BENEFICIARY_COUNTRY"] = bc
    e["SRC_COUNTRY"] = src_country; e["DST_COUNTRY"] = dst_country
    return e


def build_account_links(rng, cfg, active_ids):
    holders = rng.choice(active_ids, cfg["N_ACCT_CUSTOMERS"], replace=False)
    k = np.maximum(1, np.round(rng.lognormal(cfg["ACCT_LOG_MEAN"], cfg["ACCT_LOG_SIGMA"], len(holders)))).astype(int)
    k = np.maximum(1, np.round(k * cfg["N_ACCOUNT_LINKS"] / k.sum())).astype(int)
    cust = np.repeat(holders, k); m = len(cust)
    base = zpad(rng.integers(0, 10**12, m), 12).to_numpy().astype("U")
    suf = np.array(cfg["ACCT_SUFFIXES"])[rng.choice(len(cfg["ACCT_SUFFIXES"]), m,
                                                     p=np.array(cfg["ACCT_SUFFIX_W"]) / sum(cfg["ACCT_SUFFIX_W"]))]
    acct = np.char.add(base, suf.astype("U"))
    yrs = np.where(rng.random(m) < cfg["ACCT_1900_FRAC"], 1900, rng.integers(1988, 2026, m))
    frm = pd.to_datetime(pd.Series(yrs).astype(str) + "-01-01") + pd.to_timedelta(rng.integers(0, 360, m), "D")
    active = rng.random(m) < cfg["ACCT_ACTIVE_FRAC"]
    to = np.where(active, np.datetime64("2099-12-31"),
                  (frm + pd.to_timedelta(rng.integers(30, 3000, m), "D")).to_numpy())
    df = pd.DataFrame({"ACCOUNT_ID": pd.array(acct, dtype=object), "CUSTOMER_ID": cust.astype("int64"),
                       "FROM_DATE": pd.to_datetime(frm), "TO_DATE": pd.to_datetime(to)})
    rel = df.groupby("CUSTOMER_ID")["FROM_DATE"].min()
    return df, rel


def build_alerts(rng, cfg, active_ids, pseudo_ids, near_ids):
    n = cfg["N_ALERTS"]; n_ps = int(n * 0.15); n_hb = n - n_ps
    forced = np.array(near_ids[:min(len(near_ids), 20, n_hb)], dtype="int64")
    rest = rng.choice(active_ids, max(0, n_hb - len(forced)), replace=False)
    hb = np.concatenate([forced, rest])[:n_hb]
    ids = list(pd.Series(hb).astype("int64").astype(str)) + list(rng.choice(pseudo_ids, n_ps))
    rng.shuffle(ids)
    # graph-level alert flag hits only ~162 nodes (subset near seeds + random active)
    n_extra = max(0, min(cfg["N_GRAPH_ALERTED"], len(active_ids)) - len(forced))
    extra = rng.choice(active_ids, n_extra, replace=False) if n_extra else np.array([], dtype="int64")
    alerted = set(int(x) for x in np.concatenate([forced, extra]))
    return pd.DataFrame({"CUSTOMER_ID": pd.array(ids, dtype=object)}), alerted


def build_graph(rng, cfg, meta, active_ids, pseudo_ids, e, risk_map, alerted, rel):
    # ---- MASKED canonical ids + crosswalk ----
    masked_active = np.array([f"CUS_{i+1:012d}" for i in range(len(active_ids))], dtype=object)
    masked_pseudo = np.array([f"CUS_{len(active_ids)+i+1:012d}" for i in range(len(pseudo_ids))], dtype=object)
    amap = dict(zip(active_ids.tolist(), masked_active.tolist()))
    pmap = dict(zip(pseudo_ids.tolist(), masked_pseudo.tolist()))
    full = {**amap, **pmap}

    src_m = pd.Series(e["src_int"]).map(amap).to_numpy()
    dst_m = pd.Series(e["dst_obj"]).map(full).to_numpy()
    orig_orig = zpad(e["src_int"], 9).to_numpy()                       # TRANSACTIONS ORIGINATOR_KEY
    bene_orig = np.where(e["dst_is_p"].to_numpy(), e["dst_obj"].to_numpy(),
                         pd.to_numeric(e["dst_obj"], errors="coerce").astype("Int64").astype(str).to_numpy())

    # ---- GRAPH_NODES ----
    am = meta.set_index("cid").reindex(active_ids)
    hbus = pd.DataFrame({
        "MASKED_CUSTOMER_ID": masked_active, "ORIGINAL_CUSTOMER_ID": zpad(active_ids, 9).to_numpy(),
        "ADDRESS": am["addr"].to_numpy(), "PEP_FLAG": am["pep"].to_numpy(),
        "PHONE_NUMBER": am["phone"].to_numpy(), "EMAIL_ADDRESS": am["email"].to_numpy(),
        "CRR": am["crr"].to_numpy(), "IS_PSEUDO": False, "IS_HBUS": True,
        "HAS_PREV_TM_ALERT": pd.Series(active_ids).isin(alerted).astype("int64").to_numpy(),
    })
    reld = (pd.Timestamp("1990-01-01") + pd.to_timedelta(rng.integers(0, 13150, len(active_ids)), "D")).to_numpy()
    keep_rel = rng.random(len(active_ids)) < cfg["ACTIVE_FILL_REL"]   # ~74k of 74,428 active
    hbus["RELATIONSHIP_START_DATE"] = pd.to_datetime(np.where(keep_rel, reld, np.datetime64("NaT")))
    ps = pd.DataFrame({
        "MASKED_CUSTOMER_ID": masked_pseudo, "ORIGINAL_CUSTOMER_ID": pseudo_ids.astype(object),
        "ADDRESS": pd.NA, "PEP_FLAG": pd.NA, "PHONE_NUMBER": pd.NA, "EMAIL_ADDRESS": pd.NA, "CRR": pd.NA,
        "IS_PSEUDO": True, "IS_HBUS": False, "HAS_PREV_TM_ALERT": 0, "RELATIONSHIP_START_DATE": pd.NaT})
    graph_nodes = pd.concat([hbus, ps], ignore_index=True)

    # ---- TRANSACTIONS ----
    transactions = pd.DataFrame({
        "CREDIT_DEBIT_CODE": e["CREDIT_DEBIT_CODE"], "ORIGINATOR_KEY": pd.array(orig_orig, dtype="string"),
        "ORIGINATOR_COUNTRY": e["ORIGINATOR_COUNTRY"], "BENEFICIARY_KEY": pd.array(bene_orig.astype(str), dtype="string"),
        "BENEFICIARY_COUNTRY": e["BENEFICIARY_COUNTRY"], "txn_count": e["txn_count"],
        "total_amount_base": e["total_amount_base"], "first_run_date": e["first_run_date"],
        "last_run_date": e["last_run_date"]})

    # ---- GRAPH_EDGES ----
    rmap = lambda a: pd.Series(a).map(risk_map).to_numpy()
    oc, bcc = e["ORIGINATOR_COUNTRY"].to_numpy(), e["BENEFICIARY_COUNTRY"].to_numpy()
    sc, dc = e["SRC_COUNTRY"].to_numpy(), e["DST_COUNTRY"].to_numpy()
    # both same-country flags derive from the sparse SRC/DST home-country pair (real: ~8.75% each)
    same = ((pd.notna(sc)) & (pd.notna(dc)) & (sc == dc)).astype("int64")
    same_txn = same; same_flow = same
    # shared-contact flags on HBUS->HBUS edges (planted clusters make these fire)
    mbi = meta.set_index("cid")
    so = mbi.reindex(e["src_int"].to_numpy()); do = mbi.reindex(pd.to_numeric(e["dst_obj"], errors="coerce").to_numpy())
    hb = ~e["dst_is_p"].to_numpy()
    def shared(col):
        a = np.asarray(so[col].to_numpy(), object); b = np.asarray(do[col].to_numpy(), object)
        m = hb & np.asarray(pd.notna(a)) & np.asarray(pd.notna(b))
        out = np.zeros(len(a), bool); idx = np.where(m)[0]
        if len(idx):
            out[idx] = (a[idx] == b[idx]) & (a[idx] != "UNKNOWN") & (a[idx] != "0000000000")
        return out.astype("int64")
    sa, sp, se = shared("addr"), shared("phone"), shared("email")
    graph_edges = pd.DataFrame({
        "CREDIT_DEBIT_CODE": e["CREDIT_DEBIT_CODE"], "TOTAL_AMOUNT_BASE": e["total_amount_base"],
        "BENEFICIARY_KEY": dst_m, "ORIGINATOR_COUNTRY": e["ORIGINATOR_COUNTRY"], "LAST_RUN_DATE": e["last_run_date"],
        "BENEFICIARY_COUNTRY": e["BENEFICIARY_COUNTRY"], "ORIGINATOR_KEY": src_m, "TXN_COUNT": e["txn_count"],
        "FIRST_RUN_DATE": e["first_run_date"], "SRC": src_m, "DST": dst_m,
        "EDGE_DIRECTION": np.where(e["CREDIT_DEBIT_CODE"].to_numpy() == "DEBIT", "ORG_to_BEN", "BEN_to_ORG"),
        "ORIGINATOR_COUNTRY_RISK": rmap(oc), "BENEFICIARY_COUNTRY_RISK": rmap(bcc), "SAME_COUNTRY_TXN": same_txn,
        "SRC_COUNTRY": sc, "DST_COUNTRY": dc, "SAME_COUNTRY_FLOW": same_flow,
        "SRC_COUNTRY_RISK": rmap(sc), "DST_COUNTRY_RISK": rmap(dc),
        "SHARED_ADDRESS": sa, "SHARED_PHONE": sp, "SHARED_EMAIL": se,
        "ANY_SHARED_CONTACT": ((sa | sp | se) > 0).astype("int64")})
    return graph_nodes, transactions, graph_edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./synthetic_data")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    cfg = dict(CONFIG)
    if a.scale != 1.0:
        for k in ("N_CUSTOMERS","N_ACTIVE","N_PSEUDO","N_TXN_EDGES","N_ACCOUNT_LINKS","N_ACCT_CUSTOMERS","N_ALERTS","N_GRAPH_ALERTED"):
            cfg[k] = max(len(cfg["SEEDS"]) + 5, int(cfg[k] * a.scale))
    os.makedirs(a.out, exist_ok=True)
    rng = np.random.default_rng(cfg["SEED"])

    print("[1/7] pools");    persons, companies, addrs, doms = make_pools(rng)
    print("[2/7] COUNTRY");  country, risk_map = build_country(rng, cfg)
    print(f"[3/7] CUSTOMERS {cfg['N_CUSTOMERS']:,}"); customers, meta = build_customers(rng, cfg, persons, companies, addrs, doms)
    active_ids = meta.loc[meta["active"], "cid"].to_numpy()
    extra = plant_typologies(rng, cfg, customers, meta, active_ids)
    pseudo_ids = np.array([f"PSEUDO_{v}" for v in sample_unique_ints(rng, cfg["N_PSEUDO"], 1, 999_999_999)], dtype=object)
    _pool = np.array(HOME_CC_POOL)                          # random per active (decorrelate from degree)
    home = {int(c): str(x) for c, x in zip(active_ids.tolist(), rng.choice(_pool, len(active_ids)).tolist())}
    for s in cfg["SEEDS"]:
        home.setdefault(int(s), "US")
    home_cc = lambda arr: np.array([home.get(int(x), "US") if not pd.isna(x) else None for x in arr], dtype=object)

    print(f"[4/7] TRANSACTIONS {cfg['N_TXN_EDGES']:,} + typologies"); e = build_edges(rng, cfg, active_ids, pseudo_ids, extra)
    e = add_countries(rng, cfg, e, home_cc)
    near = pd.to_numeric(e.loc[np.isin(e["src_int"], cfg["SEEDS"]) & (~e["dst_is_p"]), "dst_obj"], errors="coerce").dropna().astype("int64").to_numpy()
    print("[5/7] ACCOUNT_LINK + ALERTS"); acct, rel = build_account_links(rng, cfg, active_ids)
    alerts, alerted = build_alerts(rng, cfg, active_ids, pseudo_ids, near)
    case = pd.DataFrame({"CASE_ID": np.arange(1, len(cfg["SEEDS"]) + 1, dtype="int64"),
                         "CUSTOMER_ID": np.array(cfg["SEEDS"], dtype="int64"), "LOB": cfg["SEED_LOB"]})
    print("[6/7] GRAPH_NODES + GRAPH_EDGES"); graph_nodes, transactions, graph_edges = build_graph(rng, cfg, meta, active_ids, pseudo_ids, e, risk_map, alerted, rel)

    print(f"[7/7] writing -> {a.out}")
    tables = {"TRANSACTIONS": transactions, "CUSTOMERS": customers, "CUSTOMERS_ACCOUNT_LINK": acct,
              "COUNTRY": country, "ALERTS": alerts, "CASE_CUSTOMERS": case,
              "GRAPH_NODES": graph_nodes, "GRAPH_EDGES": graph_edges}
    for nm, df in tables.items():
        df.to_parquet(os.path.join(a.out, f"{nm}.parquet"), index=False)
        print(f"   {nm:<24} -> {len(df):>10,} rows x {df.shape[1]:>2} cols")
    print("\nDONE. NOTE: graph uses MASKED 'CUS_' ids; map CASE_CUSTOMERS -> GRAPH_NODES.ORIGINAL_CUSTOMER_ID first.")


if __name__ == "__main__":
    main()
