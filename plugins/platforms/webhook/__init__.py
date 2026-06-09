"""Webhook gateway adapter — migrated from gateway/platforms/webhook.py (P2-2 Batch 3)."""


def register_plugin(manager):
    from plugins.platforms.webhook.adapter import WebhookAdapter
    manager.register_adapter("webhook", WebhookAdapter)
