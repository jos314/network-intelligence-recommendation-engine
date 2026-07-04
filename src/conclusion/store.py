"""The Copilot conclusion loop (no LLM API available — open question Q13).

Flow, as designed with the team:
  1. The app writes replicable per-case metrics to output/case_metrics/
     (the evidence pack JSON, refreshed on every scoring run).
  2. The analyst opens the repo in VS Code and asks Copilot to follow
     skills/case-conclusion/SKILL.md — Copilot reads the metrics and writes
     a plain-language conclusion to output/conclusions/case_<n>.md.
  3. The app displays that file in the "AI conclusion" card (Refresh button
     re-reads it; the analyst can also edit and save it in-app).
"""
import json

from .. import config

METRICS_DIR = config.OUTPUT_DIR / "case_metrics"
CONCLUSIONS_DIR = config.OUTPUT_DIR / "conclusions"


def write_metrics(evidence: dict):
    """Persist the evidence pack as the machine-readable metrics file."""
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    path = METRICS_DIR / ("case_%s.json" % evidence["case_id"])
    path.write_text(json.dumps(evidence, indent=2, default=str))
    return path


def conclusion_path(case_id):
    return CONCLUSIONS_DIR / ("case_%s.md" % case_id)


def read_conclusion(case_id):
    """The AI-written (or analyst-edited) conclusion, or None if absent."""
    path = conclusion_path(case_id)
    if path.exists():
        text = path.read_text().strip()
        return text or None
    return None


def write_conclusion(case_id, text: str):
    CONCLUSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = conclusion_path(case_id)
    path.write_text((text or "").strip() + "\n")
    return path
