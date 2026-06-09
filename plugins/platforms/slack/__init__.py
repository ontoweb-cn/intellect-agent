"""Slack gateway adapter — migrated from gateway/platforms/slack.py (P2-2 Batch 1)."""


def register_plugin(manager):
    """Register the Slack platform adapter with the plugin manager."""
    from plugins.platforms.slack.adapter import SlackAdapter
    manager.register_adapter("slack", SlackAdapter)
