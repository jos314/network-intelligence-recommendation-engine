"""Test harness defaults.

The app and pipeline tests run on the small in-code demo fixture — the
full-scale synthetic parquet (if generated into data/synthetic) would make
every _render call score a hub-scale network. The prebuilt-graph path gets
its own dedicated tests against a --scale-reduced generated dataset
(tests/test_prebuilt.py), which constructs DataAccess explicitly.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("NIRE_DATA_SOURCE", "demo")

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def prebuilt_dir(tmp_path_factory):
    """Generate a small-scale synthetic extract once per test session."""
    out = tmp_path_factory.mktemp("synth")
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "generate_synthetic_aml_data.py"),
         "--out", str(out), "--scale", "0.02"],
        check=True, capture_output=True, cwd=str(REPO))
    return out
