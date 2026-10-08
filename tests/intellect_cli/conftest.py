"""Fixtures shared across intellect_cli kanban tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def all_assignees_spawnable(monkeypatch):
    """Pretend every assignee maps to a real Intellect profile.

    Most dispatcher tests use synthetic assignees ("alice", "bob") that
    don't correspond to actual profile directories on disk. Without this
    patch, the dispatcher's profile-exists guard (PR #20105) routes
    those tasks into ``skipped_nonspawnable`` instead of spawning, which
    would break tests that assert spawn behavior.
    """
    from intellect_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)


@pytest.fixture(autouse=True)
def _suppress_concurrent_intellect_gate(request, monkeypatch):
    """Default ``_detect_concurrent_intellect_instances`` to ``[]`` for every test.

    The Windows update path now refuses to proceed when another
    ``intellect.exe`` is detected (issue #26670). On a developer's Windows
    machine running the test suite via ``intellect`` itself, this would
    flag the running agent as a concurrent instance and abort every
    ``cmd_update`` test. Tests that want to exercise the gate explicitly
    re-patch ``_detect_concurrent_intellect_instances`` with their own
    return value — autouse here gives a clean default without touching
    the rest of the suite.

    Tests that need to call the REAL function (e.g. unit tests for the
    helper itself) opt out with ``@pytest.mark.real_concurrent_gate``.
    """
    if request.node.get_closest_marker("real_concurrent_gate"):
        return
    try:
        from intellect_cli import main as _cli_main
    except Exception:
        return
    monkeypatch.setattr(
        _cli_main, "_detect_concurrent_intellect_instances", lambda *_a, **_k: []
    )


@pytest.fixture(autouse=True)
def _offline_model_discovery(monkeypatch):
    """Stub the remote model-discovery pulls the picker pipeline makes.

    ``list_authenticated_providers()`` unconditionally fetches the remote
    OntoWeb catalog manifest, and on machines with a Claude Code login the
    anthropic row probes api.anthropic.com live — each can take 25s+ on a
    flaky network, tripping the 30s per-test isolation timeout even though
    the tests exercise local logic. No intellect_cli test asserts the real
    remote behavior (test_model_catalog.py tests its own module's
    internals); tests that need specific catalog data patch the fetchers
    themselves, which overrides this default.
    """
    monkeypatch.setattr(
        "intellect_cli.models.get_curated_ontoweb_model_ids",
        lambda: [],
        raising=False,
    )
    monkeypatch.setattr(
        "intellect_cli.models._fetch_anthropic_models",
        lambda *a, **k: [],
        raising=False,
    )
    # The credential pool's singleton seeding exchanges the local `gh` CLI
    # token for a Copilot API token on every load — a real GitHub API call
    # that is irrelevant to every picker/pool test here. Identity stub keeps
    # the seeding flow intact without the network round-trip.
    monkeypatch.setattr(
        "intellect_cli.copilot_auth.get_copilot_api_token",
        lambda raw_token, *a, **k: raw_token,
        raising=False,
    )
