"""CI smoke: real Rust DelegationRegistry (HP-205b)."""

import pytest

from intellect_rust import HAS_DELEGATION_REGISTRY, DelegationRegistry


@pytest.mark.skipif(
    not HAS_DELEGATION_REGISTRY,
    reason="intellect_community_core not built (maturin develop)",
)
class TestDelegationRegistrySmoke:
    def test_register_complete_drain_up_to(self):
        reg = DelegationRegistry()
        parent = "agent:main:cli:rust-smoke"
        hid = reg.register(parent, "verify drain_completions_up_to")
        assert hid.startswith("d-")

        assert reg.complete(hid, "completed", "done", "") is True
        assert reg.drain_completions_up_to(parent, 1) == [hid]
        assert reg.drain_completions_up_to(parent, 1) == []

    def test_requeue_completions(self):
        reg = DelegationRegistry()
        parent = "agent:main:cli:rust-requeue"
        hid = reg.register(parent, "requeue path")
        reg.complete(hid, "failed", "", "boom")
        assert reg.drain_completions_up_to(parent, 1) == [hid]

        reg.requeue_completions(parent, [hid])
        assert reg.drain_completions_up_to(parent, 1) == [hid]
