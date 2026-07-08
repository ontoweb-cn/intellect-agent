"""Tests for image_generate edit path (HP-103 MVP)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.image_gen_provider import ImageGenProvider, error_response, success_response
from tools.image_generation_tool import (
    _handle_image_generate,
    _validate_source_image_path,
)


class _StubProvider(ImageGenProvider):
    supports_edit_flag = False

    @property
    def name(self) -> str:
        return "stub"

    @property
    def supports_edit(self):
        return self.supports_edit_flag

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        return success_response(
            image="/tmp/out.png", model="m", prompt=prompt, provider="stub", aspect_ratio=aspect_ratio,
        )

    def edit(self, prompt, source_image, aspect_ratio="landscape", **kwargs):
        return success_response(
            image="/tmp/edited.png",
            model="m",
            prompt=prompt,
            provider="stub",
            aspect_ratio=aspect_ratio,
            extra={"source_image": source_image},
        )


class TestValidateSourceImagePath:
    def test_rejects_forbidden_path(self, tmp_path, monkeypatch):
        secret = tmp_path / ".env"
        secret.write_text("KEY=x", encoding="utf-8")
        monkeypatch.setattr(
            "tools.path_security.is_forbidden_path",
            lambda p: str(p).endswith(".env"),
        )
        _, err_json = _validate_source_image_path(str(secret))
        data = json.loads(err_json)
        assert data["success"] is False
        assert data["error_type"] == "forbidden_path"

    def test_rejects_missing_file(self, tmp_path):
        _, err_json = _validate_source_image_path(str(tmp_path / "nope.png"))
        data = json.loads(err_json)
        assert data["error_type"] == "invalid_argument"

    def test_rejects_symlink_to_forbidden_target(self, tmp_path, monkeypatch):
        secret = tmp_path / ".env"
        secret.write_text("KEY=x", encoding="utf-8")
        link = tmp_path / "innocent.png"
        link.symlink_to(secret)
        monkeypatch.setattr(
            "tools.path_security.is_forbidden_path",
            lambda p: str(p).endswith(".env"),
        )
        _, err_json = _validate_source_image_path(str(link))
        data = json.loads(err_json)
        assert data["success"] is False
        assert data["error_type"] == "forbidden_path"


class TestImageEditDispatch:
    def test_source_image_without_edit_provider_errors(self, monkeypatch):
        monkeypatch.setenv("INTELLECT_HOME", "/tmp")
        img = Path("/tmp/test_edit_src.png")
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "resolve", return_value=img), \
             patch.object(Path, "expanduser", return_value=img), \
             patch(
                 "tools.image_generation_tool._read_configured_image_provider",
                 return_value=None,
             ):
            result = json.loads(_handle_image_generate({
                "prompt": "make it blue",
                "source_image": str(img),
            }))
        assert result["success"] is False
        assert result["error_type"] == "capability_error"

    def test_routes_to_provider_edit(self, monkeypatch, tmp_path):
        src = tmp_path / "photo.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n")
        provider = _StubProvider()
        provider.supports_edit_flag = True

        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
        with patch(
            "tools.image_generation_tool._read_configured_image_provider",
            return_value="stub",
        ), patch(
            "tools.image_generation_tool._read_configured_image_model",
            return_value=None,
        ), patch(
            "agent.image_gen_registry.get_provider",
            return_value=provider,
        ), patch(
            "intellect_cli.plugins._ensure_plugins_discovered",
        ):
            result = json.loads(_handle_image_generate({
                "prompt": "add a hat",
                "source_image": str(src),
            }))

        assert result["success"] is True
        assert result["image"] == "/tmp/edited.png"

    def test_unsupported_provider_edit_returns_capability_error(self, monkeypatch, tmp_path):
        src = tmp_path / "photo.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n")
        provider = _StubProvider()
        provider.supports_edit_flag = False

        with patch(
            "tools.image_generation_tool._read_configured_image_provider",
            return_value="stub",
        ), patch(
            "agent.image_gen_registry.get_provider",
            return_value=provider,
        ), patch(
            "intellect_cli.plugins._ensure_plugins_discovered",
        ):
            result = json.loads(_handle_image_generate({
                "prompt": "edit",
                "source_image": str(src),
            }))

        assert result["success"] is False
        assert result["error_type"] == "capability_error"
