"""Matrix gateway adapter — migrated from gateway/platforms/matrix.py (P2-2 Batch 3)."""


def register_plugin(manager):
    from plugins.platforms.matrix.adapter import MatrixAdapter
    manager.register_adapter("matrix", MatrixAdapter)
