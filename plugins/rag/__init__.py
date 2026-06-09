"""RAG provider plugin discovery.

Scans bundled ``plugins/rag/<name>/`` and user-installed directories.
Only ONE provider active at a time via ``rag.provider`` in config.yaml.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from intellect_cli.config import cfg_get

logger = logging.getLogger(__name__)

_RAG_PLUGINS_DIR = Path(__file__).parent


def _get_user_plugins_dir() -> Optional[Path]:
    try:
        from intellect_constants import get_intellect_home
        d = get_intellect_home() / "plugins"
        return d if d.is_dir() else None
    except Exception:
        return None


def _get_user_rag_plugins_dir() -> Optional[Path]:
    try:
        from intellect_constants import get_intellect_home
        d = get_intellect_home() / "plugins" / "rag"
        return d if d.is_dir() else None
    except Exception:
        return None


def _is_rag_provider_dir(path: Path) -> bool:
    init_file = path / "__init__.py"
    if not init_file.exists():
        return False
    try:
        source = init_file.read_text(errors="replace")[:8192]
        return "register_rag_provider" in source or "RAGProvider" in source
    except Exception:
        return False


def _iter_provider_dirs() -> List[Tuple[str, Path]]:
    seen: set = set()
    dirs: List[Tuple[str, Path]] = []

    if _RAG_PLUGINS_DIR.is_dir():
        for child in sorted(_RAG_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if not (child / "__init__.py").exists():
                continue
            seen.add(child.name)
            dirs.append((child.name, child))

    user_rag = _get_user_rag_plugins_dir()
    if user_rag:
        for child in sorted(user_rag.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if child.name in seen:
                continue
            if not (child / "__init__.py").exists():
                continue
            seen.add(child.name)
            dirs.append((child.name, child))

    user_dir = _get_user_plugins_dir()
    if user_dir:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if child.name in seen:
                continue
            if not _is_rag_provider_dir(child):
                continue
            dirs.append((child.name, child))

    return dirs


def find_provider_dir(name: str) -> Optional[Path]:
    bundled = _RAG_PLUGINS_DIR / name
    if bundled.is_dir() and (bundled / "__init__.py").exists():
        return bundled
    user_rag = _get_user_rag_plugins_dir()
    if user_rag:
        user = user_rag / name
        if user.is_dir() and (user / "__init__.py").exists():
            return user
    user_dir = _get_user_plugins_dir()
    if user_dir:
        user = user_dir / name
        if user.is_dir() and _is_rag_provider_dir(user):
            return user
    return None


def discover_rag_providers() -> List[Tuple[str, str, bool]]:
    results = []
    for name, child in _iter_provider_dirs():
        desc = ""
        yaml_file = child / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file, encoding="utf-8-sig") as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
            except Exception:
                pass
        available = True
        try:
            provider = _load_provider_from_dir(child)
            available = bool(provider and provider.is_available())
        except Exception:
            available = False
        results.append((name, desc, available))
    return results


def load_rag_provider(name: str) -> Optional["RAGProvider"]:
    provider_dir = find_provider_dir(name)
    if not provider_dir:
        logger.debug("RAG provider '%s' not found", name)
        return None
    try:
        return _load_provider_from_dir(provider_dir)
    except Exception as e:
        logger.warning("Failed to load RAG provider '%s': %s", name, e)
        return None


def _load_provider_from_dir(provider_dir: Path) -> Optional["RAGProvider"]:
    name = provider_dir.name
    _is_bundled = (
        _RAG_PLUGINS_DIR in provider_dir.parents
        or provider_dir.parent == _RAG_PLUGINS_DIR
    )
    module_name = f"plugins.rag.{name}" if _is_bundled else f"_intellect_user_rag.{name}"
    init_file = provider_dir / "__init__.py"
    if not init_file.exists():
        return None

    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        for parent in ("plugins", "plugins.rag"):
            if parent not in sys.modules:
                parent_path = Path(__file__).parent
                if parent == "plugins":
                    parent_path = parent_path.parent
                parent_init = parent_path / "__init__.py"
                if parent_init.exists():
                    spec = importlib.util.spec_from_file_location(
                        parent,
                        str(parent_init),
                        submodule_search_locations=[str(parent_path)],
                    )
                    if spec:
                        parent_mod = importlib.util.module_from_spec(spec)
                        sys.modules[parent] = parent_mod
                        try:
                            spec.loader.exec_module(parent_mod)
                        except Exception:
                            pass

        spec = importlib.util.spec_from_file_location(
            module_name,
            str(init_file),
            submodule_search_locations=[str(provider_dir)],
        )
        if not spec:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        for sub_file in provider_dir.glob("*.py"):
            if sub_file.name == "__init__.py":
                continue
            full_sub_name = f"{module_name}.{sub_file.stem}"
            if full_sub_name not in sys.modules:
                sub_spec = importlib.util.spec_from_file_location(
                    full_sub_name, str(sub_file)
                )
                if sub_spec:
                    sub_mod = importlib.util.module_from_spec(sub_spec)
                    sys.modules[full_sub_name] = sub_mod
                    try:
                        sub_spec.loader.exec_module(sub_mod)
                    except Exception as e:
                        logger.debug("Failed submodule %s: %s", full_sub_name, e)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.debug("Failed exec %s: %s", module_name, e)
            sys.modules.pop(module_name, None)
            return None

    if hasattr(mod, "register"):
        collector = _ProviderCollector()
        try:
            mod.register(collector)
            if collector.provider:
                return collector.provider
        except Exception as e:
            logger.debug("register() failed for %s: %s", name, e)

    from agent.rag_provider import RAGProvider
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if (
            isinstance(attr, type)
            and issubclass(attr, RAGProvider)
            and attr is not RAGProvider
        ):
            try:
                return attr()
            except Exception:
                pass
    return None


class _ProviderCollector:
    def __init__(self) -> None:
        self.provider = None

    def register_rag_provider(self, provider) -> None:
        self.provider = provider

    def register_tool(self, *args, **kwargs) -> None:
        pass

    def register_hook(self, *args, **kwargs) -> None:
        pass

    def register_cli_command(self, *args, **kwargs) -> None:
        pass


def _get_active_rag_provider() -> Optional[str]:
    try:
        from intellect_cli.config import load_config
        config = load_config()
        if not cfg_get(config, "rag", "enabled", default=True):
            return None
        return cfg_get(config, "rag", "provider") or None
    except Exception:
        return None


def discover_plugin_cli_commands() -> List[dict]:
    """CLI commands for the active RAG plugin only."""
    results: List[dict] = []
    active = _get_active_rag_provider()
    if not active:
        return results
    plugin_dir = find_provider_dir(active)
    if not plugin_dir:
        return results
    cli_file = plugin_dir / "cli.py"
    if not cli_file.exists():
        return results
    _is_bundled = (
        _RAG_PLUGINS_DIR in plugin_dir.parents
        or plugin_dir.parent == _RAG_PLUGINS_DIR
    )
    module_name = (
        f"plugins.rag.{active}.cli"
        if _is_bundled
        else f"_intellect_user_rag.{active}.cli"
    )
    try:
        if module_name in sys.modules:
            cli_mod = sys.modules[module_name]
        else:
            spec = importlib.util.spec_from_file_location(module_name, str(cli_file))
            if not spec or not spec.loader:
                return results
            cli_mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = cli_mod
            spec.loader.exec_module(cli_mod)

        register_cli = getattr(cli_mod, "register_cli", None)
        if not callable(register_cli):
            return results

        help_text = f"Manage {active} RAG plugin"
        description = ""
        yaml_file = plugin_dir / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file, encoding="utf-8-sig") as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
                if desc:
                    help_text = desc
                    description = desc
            except Exception:
                pass

        handler_fn = getattr(cli_mod, f"{active}_command", None)

        results.append({
            "name": active,
            "help": help_text,
            "description": description,
            "setup_fn": register_cli,
            "handler_fn": handler_fn,
            "plugin": active,
        })
    except Exception as e:
        logger.debug("RAG CLI discovery failed for %s: %s", active, e)
    return results
