"""End-to-end orchestrator: data -> graph -> score -> decide -> explain.

Two runtimes, per the architecture (§1):
  * offline prep (expensive, once): load tables, crosswalk/ER, unified graph;
  * interactive (per case): ego-network -> stages A-E -> calibrate -> decide
    -> evidence pack -> conclusion prompt file.

CLI:  .venv/bin/python -m src.pipeline [--case N|all] [--depth K] [--method ppr|khop]
"""
import argparse
import json

from . import config
from .conclusion.prompt_writer import write_conclusion_prompt
from .decision.calibration import apply_calibration, fit_calibrator_from_egos
from .decision.rules import apply_decisions
from .explain.evidence import build_evidence_pack
from .graph.build import build_unified_graph
from .graph.ego import ego_network
from .ingest.crosswalk import canonical_id
from .ingest.loaders import load_tables
from .ingest.quality import print_report, run_quality_checks
from .scoring.stage_a_base import score_base
from .scoring.stage_b_relationship import score_relationship
from .scoring.stage_c_propagation import score_propagation
from .scoring.stage_d_structural import score_structural
from .scoring.stage_e_aggregate import score_aggregate


def offline_prep(seed: int = 42, verbose: bool = True) -> dict:
    loaded = load_tables(seed=seed)
    tables = loaded["tables"]
    if verbose:
        print("Data source: %s" % loaded["source"].upper())
        print_report(run_quality_checks(tables))
    g, xw = build_unified_graph(tables)
    cases = {
        int(r["CASE_ID"]): canonical_id(r["CUSTOMER_ID"])
        for _, r in tables["CASE_CUSTOMERS"].iterrows()
    }
    return {"graph": g, "crosswalk": xw, "tables": tables,
            "cases": cases, "source": loaded["source"]}


def score_stages(ego, method: str = None):
    """Stages A-E over an already-extracted ego-network (uncalibrated).
    Shared by the legacy path and the prebuilt-graph DataAccess."""
    score_base(ego)
    score_relationship(ego)
    score_propagation(ego, method=method)
    score_structural(ego)
    score_aggregate(ego)
    return ego


def score_ego(g, seed_id: str, depth: int = None, method: str = None):
    """Stages A-E over one subject's ego-network (uncalibrated)."""
    return score_stages(ego_network(g, seed_id, depth=depth), method=method)


def run_all_cases(depth: int = None, method: str = None, seed: int = 42,
                  verbose: bool = True) -> dict:
    """Full run: every case scored, one shared calibrator, packs + prompts."""
    prep = offline_prep(seed=seed, verbose=verbose)
    egos = {cid: score_ego(prep["graph"], s, depth=depth, method=method)
            for cid, s in prep["cases"].items()}

    calibrator = fit_calibrator_from_egos(egos.values())
    if verbose:
        print("Calibration: %s" % calibrator.describe())

    results = {}
    for cid, ego in egos.items():
        apply_calibration(ego, calibrator)
        apply_decisions(ego)
        evidence = build_evidence_pack(ego, cid, calibration=calibrator.describe())
        prompt_path = write_conclusion_prompt(evidence)
        results[cid] = {"ego": ego, "evidence": evidence, "prompt_path": prompt_path}
        if verbose:
            print("Case %d — subject %s -> %s (p=%.2f) | network %d nodes | %s"
                  % (cid, evidence["subject_id"], evidence["decision"],
                     evidence["calibrated_score"], ego.number_of_nodes(),
                     prompt_path.name))
    return {"prep": prep, "calibrator": calibrator, "results": results}


def main():
    ap = argparse.ArgumentParser(description="Counterparty network risk pipeline")
    ap.add_argument("--case", default="all", help="case id (1-6) or 'all'")
    ap.add_argument("--depth", type=int, default=None,
                    help="ego depth (default: %d for scoring)" % config.EGO_DEPTH_SCORE)
    ap.add_argument("--method", choices=["ppr", "khop"], default=None,
                    help="propagation method (default: %s)" % config.PROP_METHOD)
    ap.add_argument("--json", action="store_true", help="print evidence packs as JSON")
    args = ap.parse_args()

    run = run_all_cases(depth=args.depth, method=args.method)
    if args.case != "all":
        run["results"] = {int(args.case): run["results"][int(args.case)]}
    if args.json:
        for cid, r in run["results"].items():
            print(json.dumps(r["evidence"], indent=2, default=str))


if __name__ == "__main__":
    main()
