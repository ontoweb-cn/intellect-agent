"""Command handler modules for Intellect CLI.

The ``intellect_cli/commands/`` package provides the central slash-command
registry, platform-specific formatters, and autocomplete helpers.

``model.py`` and ``analytics.py`` are CLI subcommand handlers extracted
from ``intellect_cli/main.py`` — they are submodules of this package but
are not re-exported here (import them directly).
"""

import intellect_cli.commands.registry as _registry

# Re-export all non-dunder names from the registry module.  This covers
# both public names (COMMAND_REGISTRY, resolve_command, …) and private
# names used by tests and internal code (_file_size_label, _CMD_NAME_LIMIT,
# …).  Using globals().update() is intentional: ``import *`` alone would
# miss private names since registry.py does not define __all__.
globals().update(
    {k: v for k, v in vars(_registry).items() if not k.startswith("__")}
)

# Clean up module-level references that should not be part of the package
# namespace.  ``_registry`` itself is excluded by the filter above (starts
# with ``_`` but NOT ``__``), so remove it explicitly.
del _registry
