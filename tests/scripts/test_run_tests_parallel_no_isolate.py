"""Tests for no_isolate batching in scripts/run_tests_parallel.py."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import run_tests_parallel as rtp  # noqa: E402


def test_file_requests_no_isolate_detects_module_marker(tmp_path):
    _file_requests_no_isolate = rtp._file_requests_no_isolate

    marked = tmp_path / "test_marked.py"
    marked.write_text(
        'import pytest\npytestmark = pytest.mark.no_isolate\n',
        encoding="utf-8",
    )
    plain = tmp_path / "test_plain.py"
    plain.write_text("def test_x(): pass\n", encoding="utf-8")

    assert _file_requests_no_isolate(marked) is True
    assert _file_requests_no_isolate(plain) is False


def test_partition_files_splits_batches():
    _partition_files = rtp._partition_files

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        iso = tdp / "iso.py"
        iso.write_text("def test_one(): pass\n", encoding="utf-8")
        fast = tdp / "fast.py"
        fast.write_text("pytestmark = pytest.mark.no_isolate\n", encoding="utf-8")
        isolated, batches = _partition_files([iso, fast])
        assert isolated == [iso]
        assert len(batches) == 1
        assert batches[0] == [fast]
