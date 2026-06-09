"""API Server gateway adapter — migrated from gateway/platforms/api_server.py (P2-2 Batch 5)."""


def register_plugin(manager):
    from plugins.platforms.api_server.adapter import APIServerAdapter
    manager.register_adapter("api_server", APIServerAdapter)
