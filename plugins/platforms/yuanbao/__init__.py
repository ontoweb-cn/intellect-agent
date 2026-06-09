"""Yuanbao gateway adapter — migrated from gateway/platforms/yuanbao.py (P2-2 Batch 5)."""


def register_plugin(manager):
    from plugins.platforms.yuanbao.adapter import YuanbaoAdapter
    manager.register_adapter("yuanbao", YuanbaoAdapter)
