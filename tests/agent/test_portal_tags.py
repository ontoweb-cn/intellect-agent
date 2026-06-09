"""Tests for agent.portal_tags — ONTOWEB Portal request tag contract."""

from __future__ import annotations


def test_intellect_client_tag_includes_current_version():
    """The client tag must reflect intellect_cli.__version__ verbatim."""
    from intellect_cli import __version__
    from agent.portal_tags import intellect_client_tag

    assert intellect_client_tag() == f"client=intellect-client-v{__version__}"


def test_intellect_client_tag_format():
    """The client tag has the exact shape ONTOWEB Portal expects."""
    from agent.portal_tags import intellect_client_tag

    tag = intellect_client_tag()
    assert tag.startswith("client=intellect-client-v")
    # No spaces, no commas — single tag value
    assert " " not in tag
    assert "," not in tag


def test_ontoweb_portal_tags_contains_product_and_client():
    """Every ONTOWEB Portal request gets BOTH the product tag and the version tag."""
    from agent.portal_tags import intellect_client_tag, ontoweb_portal_tags

    tags = ontoweb_portal_tags()
    assert "product=intellect-agent" in tags
    assert intellect_client_tag() in tags
    assert len(tags) == 2


def test_ontoweb_portal_tags_returns_fresh_list():
    """Callers mutate the returned list; we must not share state across calls."""
    from agent.portal_tags import ontoweb_portal_tags

    a = ontoweb_portal_tags()
    a.append("client=test-mutation")
    b = ontoweb_portal_tags()
    assert "client=test-mutation" not in b


def test_auxiliary_client_ontoweb_extra_body_uses_helper():
    """auxiliary_client.ONTOWEB_EXTRA_BODY must match the canonical helper output."""
    from agent.auxiliary_client import NOUS_EXTRA_BODY, ONTOWEB_EXTRA_BODY
    from agent.portal_tags import ontoweb_portal_tags

    expected = {"tags": ontoweb_portal_tags()}
    assert ONTOWEB_EXTRA_BODY == expected
    assert NOUS_EXTRA_BODY == expected  # deprecated alias


def test_ontoweb_provider_profile_uses_helper():
    """The OntoWeb provider profile (main agent loop) must use the canonical tags."""
    from agent.portal_tags import ontoweb_portal_tags
    from providers import get_provider_profile

    profile = get_provider_profile("ontoweb")
    assert profile is not None
    body = profile.build_extra_body()
    assert body["tags"] == ontoweb_portal_tags()
