"""Intellect WebUI — web-based interface for Intellect Agent.

This package was integrated from the standalone intellect-webui repository
into the intellect-agent project as the ``webui/`` subdirectory. It provides
a web-based dashboard for managing agent sessions, viewing conversations,
and configuring settings.

Documentation: ``docs/webui/``

Start the WebUI server:
    intellect webui start         # Start in background
    intellect webui stop          # Stop the server
    intellect webui status        # Show running status
    intellect webui logs          # View server logs

Or run directly:
    python -m webui.server
"""
