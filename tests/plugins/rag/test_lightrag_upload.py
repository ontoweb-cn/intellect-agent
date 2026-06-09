"""LightRAG multimodal upload filename hints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from plugins.rag.lightrag.upload import (
    build_process_options,
    build_upload_filename,
)


def test_build_process_options_multimodal_flags():
    assert build_process_options(
        analyze_images=True,
        analyze_tables=True,
        analyze_equations=True,
        chunking="p",
    ) == "iteP"


def test_build_upload_filename_mineru_iet(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_text("pdf", encoding="utf-8")
    name = build_upload_filename(
        path,
        parse_engine="mineru",
        process_options="iet",
    )
    assert name == "report.[mineru-iet].pdf"


def test_build_upload_filename_options_only(tmp_path):
    path = tmp_path / "memo.docx"
    path.write_text("doc", encoding="utf-8")
    name = build_upload_filename(path, process_options="iet")
    assert name == "memo.[-iet].docx"


def test_build_upload_filename_invalid_engine(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="parse_engine"):
        build_upload_filename(path, parse_engine="bogus")


def test_upload_document_sends_hinted_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "lightrag").mkdir()
    (home / "lightrag" / "config.json").write_text(
        json.dumps({"server": {"base_url": "http://test"}}),
        encoding="utf-8",
    )
    doc = tmp_path / "paper.pdf"
    doc.write_text("content", encoding="utf-8")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/documents/upload" in str(request.url):
            files = request.read()
            captured["body"] = files.decode("utf-8", errors="replace")
            return httpx.Response(200, json={"track_id": "t1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from plugins.rag.lightrag.client import LightRAGClientManager

    mgr = LightRAGClientManager(
        json.loads((home / "lightrag" / "config.json").read_text(encoding="utf-8"))
    )
    mgr._client._client = httpx.Client(transport=transport)
    mgr.upload_document(
        str(doc),
        scope="auto",
        parse_engine="mineru",
        analyze_images=True,
        analyze_tables=True,
        analyze_equations=True,
    )
    assert "paper.[mineru-ite].pdf" in captured.get("body", "")
