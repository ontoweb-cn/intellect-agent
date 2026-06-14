#!/usr/bin/env python3
"""Shim entry point for platforms expecting ./main.py at repo root.

The real CLI entry point is ``intellect_cli/main.py``.
This file exists to satisfy Gitee's default Python build plugin
and other CI/CD platforms that expect a top-level main.py.
"""

if __name__ == "__main__":
    import sys
    print("This is a shim. Use 'intellect' CLI or 'python -m intellect_cli.main'.", file=sys.stderr)
    sys.exit(0)
