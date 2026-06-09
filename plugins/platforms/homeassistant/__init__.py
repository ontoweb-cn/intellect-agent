"""Home Assistant gateway adapter — migrated from gateway/platforms/homeassistant.py (P2-2 Batch 4)."""


def register_plugin(manager):
    from plugins.platforms.homeassistant.adapter import HomeAssistantAdapter
    manager.register_adapter("homeassistant", HomeAssistantAdapter)
