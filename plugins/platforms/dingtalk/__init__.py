"""DingTalk gateway adapter — migrated from gateway/platforms/dingtalk.py (P2-2 Batch 2)."""


def register_plugin(manager):
    from plugins.platforms.dingtalk.adapter import DingTalkAdapter
    manager.register_adapter("dingtalk", DingTalkAdapter)
