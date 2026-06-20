"""Shared helpers for classifying tool result payloads.

Since v0.6.2 delegates to the Rust extension.
"""

from __future__ import annotations

from typing import Any

from intellect_rust import rust_file_mutation_landed


FILE_MUTATING_TOOL_NAMES = frozenset({"write_file", "patch"})


def file_mutation_result_landed(tool_name: str, result: Any) -> bool:
    """Return True when a file mutation result proves the write landed.

    Delegates to the Rust extension (mandatory since v0.6.2).
    """
    return rust_file_mutation_landed(tool_name, result)
