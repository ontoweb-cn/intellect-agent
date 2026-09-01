"""Gate-1 cache regression: pre-A1 vs current sanitize_api_messages.

Runs scripts/verify/cache_regression_m1.py logic as pytest. Skips when git
history is unavailable (wheel installs). Any byte divergence = a prompt-
cache invalidation regression in the message pipeline (M1 gate-1 clause).
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_m1_cache_regression_clean():
    script = REPO / "scripts" / "verify" / "cache_regression_m1.py"
    if not script.exists():
        pytest.skip("regression script missing")
    import shutil
    import sys

    git = shutil.which("git") or "/usr/bin/git"
    try:
        subprocess.run(
            [git, "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True
        )
    except FileNotFoundError:
        pytest.skip("git binary unavailable in sanitized test env")
    except subprocess.CalledProcessError:
        pytest.skip("git history unavailable (wheel install)")
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=REPO, capture_output=True,
        text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        f"cache regression diverged:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"
    )
