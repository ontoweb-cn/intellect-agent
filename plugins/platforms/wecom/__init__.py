"""WeCom gateway adapter — migrated from gateway/platforms/wecom.py (P2-2 Batch 2)."""


def register_plugin(manager):
    from plugins.platforms.wecom.adapter import WeComAdapter
    manager.register_adapter("wecom", WeComAdapter)
