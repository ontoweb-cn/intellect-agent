"""Feishu gateway adapter — migrated from gateway/platforms/feishu.py (P2-2 Batch 2)."""


def register_plugin(manager):
    from plugins.platforms.feishu.adapter import FeishuAdapter
    manager.register_adapter("feishu", FeishuAdapter)
