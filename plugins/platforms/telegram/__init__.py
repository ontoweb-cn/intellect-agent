"""Telegram gateway adapter — migrated from gateway/platforms/telegram.py (P2-2 Batch 1)."""


def register_plugin(manager):
    """Register the Telegram platform adapter with the plugin manager."""
    from plugins.platforms.telegram.adapter import TelegramAdapter
    manager.register_adapter("telegram", TelegramAdapter)
