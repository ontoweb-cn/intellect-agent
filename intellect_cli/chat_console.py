"""Rich Console adapter for prompt_toolkit's patch_stdout context.

Drop-in replacement for Rich Console — routes rendered ANSI output
through _cprint so colors render correctly inside the interactive chat loop.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from io import StringIO

from rich.console import Console


class ChatConsole:
    """Rich Console adapter for prompt_toolkit interactive chat."""

    def __init__(self, cprint_fn=None):
        self._buffer = StringIO()
        self._inner = Console(
            file=self._buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
        )
        # Allow injection of the print function to avoid circular imports.
        if cprint_fn is not None:
            self._cprint = cprint_fn

    def _cprint(self, text: str) -> None:
        """Fallback print — overridden via cprint_fn injection."""
        print(text)

    def print(self, *args, **kwargs):
        self._buffer.seek(0)
        self._buffer.truncate()
        self._inner.width = shutil.get_terminal_size((80, 24)).columns
        self._inner.print(*args, **kwargs)
        output = self._buffer.getvalue()
        for line in output.rstrip("\n").split("\n"):
            self._cprint(line)

    @contextmanager
    def status(self, *_args, **_kwargs):
        """No-op Rich-compatible status context for slash command helpers."""
        yield self
