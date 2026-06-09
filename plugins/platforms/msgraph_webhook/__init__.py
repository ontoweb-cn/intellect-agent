"""Microsoft Graph webhook adapter — migrated from gateway/platforms/msgraph_webhook.py (P2-2 Batch 4)."""


def register_plugin(manager):
    from plugins.platforms.msgraph_webhook.adapter import MSGraphWebhookAdapter
    manager.register_adapter("msgraph_webhook", MSGraphWebhookAdapter)
