"""BlueBubbles gateway adapter — migrated from gateway/platforms/bluebubbles.py (P2-2 Batch 4)."""


def register_plugin(manager):
    from plugins.platforms.bluebubbles.adapter import BlueBubblesAdapter
    manager.register_adapter("bluebubbles", BlueBubblesAdapter)
