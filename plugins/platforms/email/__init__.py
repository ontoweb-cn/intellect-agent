"""Email gateway adapter — migrated from gateway/platforms/email.py (P2-2 Batch 3)."""


def register_plugin(manager):
    from plugins.platforms.email.adapter import EmailAdapter
    manager.register_adapter("email", EmailAdapter)
