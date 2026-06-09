"""WhatsApp gateway adapter — migrated from gateway/platforms/whatsapp.py (P2-2 Batch 1)."""


def register_plugin(manager):
    """Register the WhatsApp platform adapter with the plugin manager."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    manager.register_adapter("whatsapp", WhatsAppAdapter)
