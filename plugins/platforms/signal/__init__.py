"""Signal gateway adapter — migrated from gateway/platforms/signal.py (P2-2 Batch 1)."""


def register_plugin(manager):
    """Register the Signal platform adapter with the plugin manager."""
    from plugins.platforms.signal.adapter import SignalAdapter
    manager.register_adapter("signal", SignalAdapter)
