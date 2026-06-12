"""Verify that mixin methods with lazy imports from cli don't NameError.

The ``SlashCommandMixin`` methods use ``_cprint``, ``_DIM``, ``_RST``,
``_ACCENT``, ``_BOLD``, and ``_accent_hex`` which are defined in ``cli.py``.
After extracting the mixin to ``cli_slash_handlers.py``, these symbols are
imported lazily inside each method body.  This test ensures every method
that uses those symbols has a working ``from cli import …`` statement.

We verify by compiling each method and checking that ``from cli import …``
appears in its bytecode — this avoids needing a full CLI instance with all
attributes set up.
"""

from __future__ import annotations

import dis
import inspect
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helper: check that a method's bytecode contains a IMPORT_NAME for 'cli'
# ---------------------------------------------------------------------------

def _has_cli_import(method) -> bool:
    """Return True if the method's bytecode imports from 'cli'."""
    try:
        instructions = list(dis.get_instructions(method))
        for instr in instructions:
            if instr.opname == 'IMPORT_NAME' and instr.argval == 'cli':
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Tests: each method must have a 'from cli import …' in its bytecode
# ---------------------------------------------------------------------------

# Methods that use cli.py module-level symbols and need lazy imports.
# Format: (method_name, symbols_used)
METHODS_NEEDING_IMPORTS = [
    ("_handle_agents_command", {"_cprint"}),
    ("_handle_paste_command", {"_cprint", "_DIM", "_RST", "_is_termux_environment", "_termux_example_image_path"}),
    ("_handle_copy_command", {"_cprint", "_assistant_copy_text"}),
    ("_handle_image_command", {"_cprint", "_DIM", "_RST", "_IMAGE_EXTENSIONS", "_is_termux_environment", "_termux_example_image_path", "_resolve_attachment_path", "_split_path_input"}),
    ("_handle_tools_command", {"_cprint", "_ACCENT", "_DIM", "_RST"}),
    ("_handle_handoff_command", {"_cprint"}),
    ("_handle_resume_command", {"_cprint", "_sync_process_session_id"}),
    ("_handle_sessions_command", {"_cprint"}),
    ("_handle_branch_command", {"_cprint", "_sync_process_session_id", "get_intellect_home"}),
    ("_handle_background_command", {"_cprint", "_accent_hex", "_maybe_remap_for_light_mode", "_render_final_assistant_content"}),
    ("_handle_bundles_command", {"_cprint", "_BOLD", "_DIM", "_RST", "_accent_hex", "_escape"}),
    ("_handle_goal_command", {"_cprint", "_DIM", "_RST"}),
    ("_handle_subgoal_command", {"_cprint", "_DIM", "_RST"}),
    ("_handle_skin_command", {"_ACCENT", "display_intellect_home", "save_config_value"}),
    ("_handle_footer_command", {"_cprint", "_Colors", "save_config_value"}),
    ("_handle_reasoning_command", {"_cprint", "_ACCENT", "_DIM", "_RST", "_parse_reasoning_config", "save_config_value"}),
    ("_handle_busy_command", {"_cprint", "_ACCENT", "_DIM", "_RST", "save_config_value"}),
    ("_handle_fast_command", {"_cprint", "_ACCENT", "_DIM", "_RST", "save_config_value"}),
    ("_handle_voice_command", {"_cprint"}),
]


class TestMixinLazyImports:
    """Verify lazy imports exist in mixin method bytecode."""

    @pytest.fixture(autouse=True)
    def _load_mixin(self):
        from intellect_cli.cli_slash_handlers import SlashCommandMixin
        self.mixin = SlashCommandMixin

    @pytest.mark.parametrize("method_name,expected_symbols", METHODS_NEEDING_IMPORTS)
    def test_method_has_cli_import(self, method_name, expected_symbols):
        """Verify {method_name} has 'from cli import …' in its bytecode."""
        method = getattr(self.mixin, method_name, None)
        assert method is not None, f"{method_name} not found on mixin"
        assert _has_cli_import(method), (
            f"{method_name} uses {expected_symbols} but has no 'from cli import' "
            f"in its bytecode. Add a lazy import at the top of the method body."
        )
