"""The data-access layer (integration brief §6).

Given a Case ID or a Customer ID in any format, returns the resolved seed
node, its depth-3 ego subgraph (nodes + edges with the full attribute
contract), and the scored/decided/explained result — ready for the graph
view and the evidence pack. Placeholders and the masked-id crosswalk are
handled per brief §3-4.

Source selection (env NIRE_DATA_SOURCE overrides, tests force "demo"):
  * "prebuilt": GRAPH_NODES/GRAPH_EDGES parquet found in data/ or
    data/synthetic/ — the masked extract or its synthetic twin. Cases are
    scored LAZILY (first selection computes, then cached): a full-scale
    bounded ego takes seconds, and eager-scoring six of them would stall
    startup.
  * "demo": the small in-code fixture via the legacy loaders (fast; used
    by the test suite and when no parquet is present).
"""
import os
import threading

from . import config
from .conclusion.prompt_writer import write_conclusion_prompt
from .conclusion.store import write_metrics
from .decision.calibration import fit_calibrator_from_egos, apply_calibration
from .decision.rules import apply_decisions
from .explain.evidence import build_evidence_pack
from .graph.prebuilt_source import PrebuiltGraphSource
from .ingest.prebuilt import find_prebuilt_dir, load_prebuilt_tables


class DataAccess:
    def __init__(self, data_dir=None, source=None):
        source = source or os.environ.get("NIRE_DATA_SOURCE", "auto")
        base = data_dir or (find_prebuilt_dir() if source in ("auto", "prebuilt") else None)
        self._results = {}
        self._calibrator = None
        self._lock = threading.Lock()  # Dash fires parallel callbacks on case switch

        if source != "demo" and base is not None:
            self.source = "prebuilt"
            self._gs = PrebuiltGraphSource(load_prebuilt_tables(base))
            self._cases = self._gs.case_meta()
            self._legacy = None
        else:
            self.source = "demo"
            from .pipeline import offline_prep
            self._legacy = offline_prep(verbose=False)
            self._gs = None
            self._cases = {
                cid: {"customer_id": None, "masked": seed, "lob": None,
                      "name": self._legacy["graph"].nodes[seed].get("name") or seed}
                for cid, seed in self._legacy["cases"].items()
            }
            for cid, meta in self._cases.items():
                meta["lob"] = self._legacy["graph"].nodes[meta["masked"]].get("lob")

    # ------------------------------------------------------------- lookups
    def case_ids(self):
        return sorted(self._cases.keys())

    def case_meta(self, case_id) -> dict:
        return self._cases[case_id]

    def resolve(self, any_id):
        """Customer id in any format -> graph node id (None if absent)."""
        if self._gs is not None:
            return self._gs.resolve(any_id)
        from .ingest.crosswalk import canonical_id
        canon = canonical_id(any_id)
        return canon if canon in self._legacy["graph"] else None

    # ------------------------------------------------------------- scoring
    def _build_ego(self, seed, depth):
        if self._gs is not None:
            return self._gs.ego_graph(seed, depth=depth)
        from .graph.ego import ego_network
        return ego_network(self._legacy["graph"], seed, depth=depth)

    def result(self, case_id) -> dict:
        """Scored + decided + explained case (cached after first call)."""
        if case_id in self._results:
            return self._results[case_id]
        with self._lock:
            if case_id in self._results:  # computed while we waited
                return self._results[case_id]
            from .pipeline import score_stages
            seed = self._cases[case_id]["masked"]
            ego = self._build_ego(seed, config.EGO_DEPTH_SCORE)
            score_stages(ego)

            # one calibrator per session, fitted on the first scored
            # network's weak labels; recorded in every pack's governance
            if self._calibrator is None:
                self._calibrator = fit_calibrator_from_egos([ego])
            apply_calibration(ego, self._calibrator)
            apply_decisions(ego)
            evidence = build_evidence_pack(ego, case_id,
                                           calibration=self._calibrator.describe())
            prompt_path = write_conclusion_prompt(evidence)
            write_metrics(evidence)  # feeds the Copilot conclusion skill
            out = {"ego": ego, "evidence": evidence, "prompt_path": prompt_path}
            self._results[case_id] = out
            return out

    def calibration(self) -> dict:
        if self._calibrator is None:
            return {"calibrated": False, "method": "not fitted yet",
                    "n": 0, "n_pos": 0}
        return self._calibrator.describe()
