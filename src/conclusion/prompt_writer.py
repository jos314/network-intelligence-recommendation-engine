"""§5.3 — conclusion via LLM, no in-app API.

The app writes a filled prompt file (fixed instructions + evidence pack);
the analyst pastes it into an LLM (Copilot / Claude / a local Ollama) and
attaches the narrative back to the case. Deterministic and grounded: the
prompt forbids facts beyond the pack.
"""
import json
from pathlib import Path

from .. import config


def write_conclusion_prompt(evidence: dict, out_dir: Path = None) -> Path:
    out_dir = Path(out_dir) if out_dir else config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    template = config.PROMPT_TEMPLATE.read_text()
    filled = template.replace("{decision}", str(evidence["decision"]))
    filled = filled.replace("{evidence_json}", json.dumps(evidence, indent=2, default=str))
    path = out_dir / ("conclusion_prompt_case_%s.md" % evidence["case_id"])
    path.write_text(filled)
    return path
