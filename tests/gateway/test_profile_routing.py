"""Tests for gateway.profile_routing (B1-1 / MP-02)."""

from gateway.profile_routing import (
    ProfileRoute,
    ProfileRouteRejected,
    match_profile_route,
    parse_profile_routes,
)


ROUTES = [
    {"name": "thread", "platform": "discord", "profile": "p-thread",
     "chat_id": "c1", "thread_id": "t1"},
    {"name": "channel", "platform": "discord", "profile": "p-channel",
     "guild_id": "g1", "chat_id": "c1"},
    {"name": "guild", "platform": "discord", "profile": "p-guild",
     "guild_id": "g1"},
    {"name": "tg", "platform": "telegram", "profile": "p-tg",
     "chat_id": "12345"},
]


def test_specificity_sorting():
    parsed = parse_profile_routes(ROUTES)
    # specificity: thread(14) > channel(6) > chat-only(4) > guild-only(2)
    assert [r.name for r in parsed] == ["thread", "channel", "tg", "guild"]


def test_most_specific_wins():
    parsed = parse_profile_routes(ROUTES)
    # Exact thread beats channel beats guild.
    assert match_profile_route(parsed, "discord", "g1", "c1", "t1").profile == "p-thread"
    assert match_profile_route(parsed, "discord", "g1", "c1", None).profile == "p-channel"
    assert match_profile_route(parsed, "discord", "g1", "c9", None).profile == "p-guild"
    assert match_profile_route(parsed, "discord", "g2", None, None) is None  # default


def test_conjunctive_matching():
    # Channel route declares guild+chat: chat match alone must NOT satisfy it.
    r = ProfileRoute("half", "discord", "p-x", chat_id="c1")
    assert r.matches("discord", chat_id="c1") is True  # no guild declared
    strict = ProfileRoute("strict", "discord", "p-y", guild_id="g1", chat_id="c1")
    assert strict.matches("discord", chat_id="c1") is False  # guild missing
    assert strict.matches("discord", guild_id="g1", chat_id="c1") is True


def test_parent_chain_channel_covers_threads():
    parsed = parse_profile_routes(ROUTES)
    # A forum/thread post whose PARENT is c1 is covered by the channel route.
    r = match_profile_route(
        parsed, "discord", "g1", "c-thread-42", None, parent_chat_id="c1"
    )
    assert r is not None and r.profile == "p-channel"


def test_disabled_routes_skipped():
    routes = [
        ProfileRoute("off", "telegram", "p-off", chat_id="1", enabled=False),
        ProfileRoute("on", "telegram", "p-on", chat_id="1"),
    ]
    assert match_profile_route(routes, "telegram", chat_id="1").profile == "p-on"


def test_platform_mismatch_never_matches():
    parsed = parse_profile_routes(ROUTES)
    assert match_profile_route(parsed, "slack", chat_id="12345") is None


def test_parse_skips_incomplete_entries():
    raw = [
        {"name": "no-profile", "platform": "discord"},   # missing profile
        {"name": "no-platform", "profile": "p"},          # missing platform
        "not-a-dict",
        {"name": "ok", "platform": "telegram", "profile": "p-ok"},
    ]
    parsed = parse_profile_routes(raw)
    assert [r.name for r in parsed] == ["ok"]


def test_parse_rejects_path_traversal_profile(monkeypatch):

    def _reject(name):
        if "/" in name or ".." in name:
            raise ValueError("invalid profile name")

    monkeypatch.setattr(
        "intellect_cli.profiles.validate_profile_name", _reject
    )
    raw = [
        {"name": "evil", "platform": "discord", "profile": "../../etc"},
        {"name": "ok", "platform": "discord", "profile": "fine"},
    ]
    parsed = parse_profile_routes(raw)
    assert [r.profile for r in parsed] == ["fine"]


def test_empty_config_returns_empty():
    assert parse_profile_routes(None) == []
    assert parse_profile_routes([]) == []
    assert match_profile_route([], "telegram") is None


def test_rejected_error_is_runtime_error():
    # Consumer-side contract: the routing layer reports; the caller raises.
    assert issubclass(ProfileRouteRejected, RuntimeError)
