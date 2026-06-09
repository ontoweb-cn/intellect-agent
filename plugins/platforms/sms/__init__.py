"""SMS gateway adapter — migrated from gateway/platforms/sms.py (P2-2 Batch 3)."""


def register_plugin(manager):
    from plugins.platforms.sms.adapter import SmsAdapter
    manager.register_adapter("sms", SmsAdapter)
