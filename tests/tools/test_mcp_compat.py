"""Unit tests for tools.mcp_compat — the MCP SDK v1/v2 shim layer.

These exercise the pure compatibility helpers directly, without needing a
live MCP server. Covered behaviours:

* field reads work under both the v1 camelCase and v2 snake_case spellings;
* ``supported_kwargs`` trims parameters the installed SDK dropped (and keeps
  a subclass's own parameters when it forwards ``**kwargs`` to a base);
* OAuth callback results take the shape the installed SDK consumes;
* the SDK server's ``call_tool`` result is unwrapped consistently across the
  v1 tuple and v2 ``CallToolResult`` shapes.
"""

from __future__ import annotations

from types import SimpleNamespace

from tools import mcp_compat


# ---------------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------------


def test_sdk_major_version_is_int():
    assert isinstance(mcp_compat.sdk_major_version(), int)


def test_is_v2_agrees_with_major_version():
    assert mcp_compat.is_v2() is (mcp_compat.sdk_major_version() >= 2)


def test_http_lib_matches_sdk_major():
    """httpx2 ships with mcp 2.x; httpx with 1.x."""
    assert mcp_compat.http_lib().__name__ == ("httpx2" if mcp_compat.is_v2() else "httpx")


# ---------------------------------------------------------------------------
# mcp_field — snake_case (v2) with a camelCase (v1) fallback
# ---------------------------------------------------------------------------


def test_mcp_field_reads_snake_case():
    obj = SimpleNamespace(structured_content={"a": 1})
    assert mcp_compat.mcp_field(obj, "structured_content") == {"a": 1}


def test_mcp_field_falls_back_to_camel_case():
    obj = SimpleNamespace(structuredContent={"a": 1})
    assert mcp_compat.mcp_field(obj, "structured_content") == {"a": 1}


def test_mcp_field_prefers_snake_case_when_both_present():
    obj = SimpleNamespace(structured_content="new", structuredContent="old")
    assert mcp_compat.mcp_field(obj, "structured_content") == "new"


def test_mcp_field_returns_default_when_absent():
    assert mcp_compat.mcp_field(SimpleNamespace(), "is_error", False) is False
    assert mcp_compat.mcp_field(SimpleNamespace(), "is_error") is None


def test_mcp_field_preserves_falsy_values():
    """An explicit False/empty must not fall through to the default."""
    assert mcp_compat.mcp_field(SimpleNamespace(is_error=False), "is_error", True) is False
    assert mcp_compat.mcp_field(SimpleNamespace(content=[]), "content", "x") == []


def test_mcp_field_single_word_name_has_no_camel_variant():
    obj = SimpleNamespace(content="body")
    assert mcp_compat.mcp_field(obj, "content") == "body"


# ---------------------------------------------------------------------------
# supported_kwargs — trim params the installed SDK no longer accepts
# ---------------------------------------------------------------------------


def test_supported_kwargs_drops_unknown_keyword():
    def target(alpha, beta=1):
        return alpha, beta

    assert set(mcp_compat.supported_kwargs(target, {"alpha": 1, "beta": 2, "gone": 3})) == {
        "alpha",
        "beta",
    }


def test_supported_kwargs_passes_everything_to_var_kwargs():
    def target(**kwargs):
        return kwargs

    assert mcp_compat.supported_kwargs(target, {"anything": 1, "else": 2}) == {
        "anything": 1,
        "else": 2,
    }


def test_supported_kwargs_keeps_subclass_params_across_mro():
    """A subclass forwarding **kwargs to its base still owns its own params,
    while params the base dropped (the real v2 ``timeout`` case) are trimmed."""

    class Base:
        # Stands in for the installed SDK base: no `timeout` parameter.
        def __init__(self, server_url, client_metadata=None):
            self.server_url = server_url

    class Sub(Base):
        def __init__(self, *args, server_name="", **kwargs):
            super().__init__(*args, **kwargs)
            self.server_name = server_name

    trimmed = mcp_compat.supported_kwargs(
        Sub,
        {"server_url": "u", "client_metadata": None, "server_name": "s", "timeout": 300},
    )
    assert set(trimmed) == {"server_url", "client_metadata", "server_name"}
    assert "timeout" not in trimmed


def test_supported_kwargs_passes_through_uninspectable_target():
    builtin = dict.__init__
    assert mcp_compat.supported_kwargs(builtin, {"whatever": 1}) == {"whatever": 1}


# ---------------------------------------------------------------------------
# authorization_code_result — shape the installed SDK consumes
# ---------------------------------------------------------------------------


def test_authorization_code_result_v1_is_tuple(monkeypatch):
    monkeypatch.setattr(mcp_compat, "is_v2", lambda: False)
    assert mcp_compat.authorization_code_result("abc", "xyz") == ("abc", "xyz")


def test_authorization_code_result_v2_shape_is_used_when_available():
    """On v2 the callback must return an object with .code/.state; if the
    installed SDK lacks that type the helper degrades to the v1 tuple."""
    try:
        from mcp.shared.auth import AuthorizationCodeResult  # noqa: F401
    except ImportError:
        # Older SDK without the result type: tuple fallback is correct.
        assert mcp_compat.authorization_code_result("abc", "xyz") == ("abc", "xyz")
        return

    result = mcp_compat.authorization_code_result("abc", "xyz")
    assert result.code == "abc"
    assert result.state == "xyz"


# ---------------------------------------------------------------------------
# extract_tool_result_text — v1 tuple vs v2 CallToolResult
# ---------------------------------------------------------------------------


def _block(text):
    return SimpleNamespace(type="text", text=text)


def test_extract_tool_result_text_from_v1_tuple():
    assert mcp_compat.extract_tool_result_text(([_block("hello")], {"meta": 1})) == "hello"


def test_extract_tool_result_text_from_v2_object():
    result = SimpleNamespace(content=[_block("world")], is_error=False)
    assert mcp_compat.extract_tool_result_text(result) == "world"


def test_extract_tool_result_text_from_bare_string():
    assert mcp_compat.extract_tool_result_text("raw") == "raw"


def test_extract_tool_result_text_empty_when_no_text_blocks():
    assert mcp_compat.extract_tool_result_text(([], {})) == ""
    assert mcp_compat.extract_tool_result_text(SimpleNamespace(content=[])) == ""
