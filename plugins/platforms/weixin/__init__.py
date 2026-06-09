"""Weixin gateway adapter — migrated from gateway/platforms/weixin.py (P2-2 Batch 4)."""


def register_plugin(manager):
    from plugins.platforms.weixin.adapter import WeixinAdapter
    manager.register_adapter("weixin", WeixinAdapter)
