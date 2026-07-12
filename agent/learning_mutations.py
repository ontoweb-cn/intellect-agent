"""User-initiated edit/delete for journey nodes (learned skills + memories).

Node ids:
- **skills** → skill name (e.g. ``"debugging-desktop"``)
- **memories** → ``memory:<source>:<local>`` where ``local`` is the per-file
  index into ``MemoryStore._read_file`` chunks (``memory`` = MEMORY.md,
  ``profile`` = USER.md). Not a global index across both files.

Deleting an agent-created / profile skill archives it (``intellect curator restore``).
Deleting a hub-installed skill uninstalls it (``intellect skills uninstall``) — not curator.
Deleting a memory rewrites its file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

_MEMORY_FILES = {"memory": "MEMORY.md", "profile": "USER.md"}


def parse_node_kind(node_id: str) -> str:
    return "memory" if node_id.startswith("memory:") else "skill"


def _memories_dir() -> Path:
    from tools.memory_tool import get_memory_dir

    return get_memory_dir()


def _parse_memory_id(node_id: str) -> tuple[str, int]:
    parts = node_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "memory" or parts[1] not in _MEMORY_FILES:
        raise ValueError(f"bad memory node id: {node_id!r}")
    try:
        return parts[1], int(parts[2])
    except ValueError as exc:
        raise ValueError(f"bad memory node id: {node_id!r}") from exc


def _locate_memory(source: str, local: int) -> tuple[Path, list[str], int]:
    from tools.memory_tool import MemoryStore

    path = _memories_dir() / _MEMORY_FILES[source]
    if not path.exists():
        raise ValueError(f"{path.name} not found")
    chunks = MemoryStore._read_file(path)
    if not 0 <= local < len(chunks):
        raise ValueError("memory node id is stale — refresh the graph")
    return path, chunks, local


def _stale_error(message: str) -> dict[str, Any]:
    return {"ok": False, "code": "stale", "message": str(message)}


def _is_stale_message(message: str) -> bool:
    return "stale" in (message or "").lower()


def node_detail(node_id: str) -> dict[str, Any]:
    try:
        return _node_detail(node_id)
    except (ValueError, IndexError) as exc:
        msg = str(exc)
        if _is_stale_message(msg):
            return _stale_error(msg)
        return {"ok": False, "message": msg}


def _hub_skill_dir(name: str) -> Optional[Path]:
    from intellect_constants import get_intellect_home

    hub_root = get_intellect_home() / "skills" / ".hub"
    if not hub_root.is_dir():
        return None
    for skill_md in hub_root.rglob("SKILL.md"):
        if skill_md.parent.name == name:
            return skill_md.parent
    return None


def _hub_lock_has(name: str) -> bool:
    try:
        from intellect_constants import get_intellect_home
        from tools.skills_hub import HubLockFile

        lock = HubLockFile(get_intellect_home() / "skills" / ".hub" / "lock.json")
        return bool(lock.get_installed(name))
    except Exception:
        return False


def _resolve_journey_skill(name: str) -> Optional[dict[str, Any]]:
    """Single provenance for Journey detail / edit / delete.

    Returns ``{path, source, deleteMode}`` or ``None`` if missing.
    If the same name exists as both a profile skill and a hub install, returns
    ``{ambiguous: True, message: ...}`` so callers refuse instead of picking a side.
    """
    from tools.skill_manager_tool import _find_skill

    profile = _find_skill(name)
    hub_dir = _hub_skill_dir(name)
    lock_has = _hub_lock_has(name)
    has_profile = profile is not None
    has_hub = hub_dir is not None or lock_has

    if has_profile and has_hub:
        return {
            "ambiguous": True,
            "message": (
                f"skill '{name}' exists both as a profile skill and a hub install — "
                "remove one copy before editing or deleting from Journey"
            ),
        }
    if has_hub:
        return {
            "path": hub_dir,
            "source": "hub",
            "deleteMode": "uninstall",
        }
    if has_profile:
        return {
            "path": profile["path"],
            "source": "profile",
            "deleteMode": "archive",
        }
    return None


def _node_detail(node_id: str) -> dict[str, Any]:
    if parse_node_kind(node_id) == "memory":
        source, local_idx = _parse_memory_id(node_id)
        _, chunks, local = _locate_memory(source, local_idx)
        body = chunks[local].strip()
        return {
            "ok": True,
            "kind": "memory",
            "id": node_id,
            "label": body.splitlines()[0][:80],
            "content": body,
        }

    found = _resolve_journey_skill(node_id)
    if not found:
        return {"ok": False, "message": f"skill '{node_id}' not found"}
    if found.get("ambiguous"):
        return {"ok": False, "code": "ambiguous", "message": found["message"]}
    if found.get("path") is None:
        return {"ok": False, "message": f"skill '{node_id}' not found"}
    skill_md = Path(found["path"]) / "SKILL.md"
    if not skill_md.exists():
        return {"ok": False, "message": f"SKILL.md missing for '{node_id}'"}

    source = found.get("source") or "profile"
    return {
        "ok": True,
        "kind": "skill",
        "id": node_id,
        "label": node_id,
        "content": skill_md.read_text(encoding="utf-8"),
        "source": source,
        "deleteMode": found.get("deleteMode") or ("uninstall" if source == "hub" else "archive"),
    }


def delete_node(node_id: str) -> dict[str, Any]:
    try:
        return _delete_memory(node_id) if parse_node_kind(node_id) == "memory" else _delete_skill(node_id)
    except (ValueError, IndexError) as exc:
        msg = str(exc)
        if _is_stale_message(msg):
            return _stale_error(msg)
        return {"ok": False, "message": msg}


def _uninstall_hub_skill(name: str) -> tuple[bool, str]:
    """Uninstall a hub skill under the active INTELLECT_HOME (profile-safe)."""
    import shutil

    from intellect_constants import get_intellect_home
    from tools import skills_hub as hub

    home = get_intellect_home()
    skills_dir = home / "skills"
    hub_dir = skills_dir / ".hub"
    lock = hub.HubLockFile(hub_dir / "lock.json")
    entry = lock.get_installed(name)
    if not entry:
        return False, f"'{name}' is not a hub-installed skill (may be a builtin)"

    # Module-level hub paths are import-time snapshots of the default home.
    # Swap them for the active profile so path resolution + audit stay in-scope.
    old_skills_dir = hub.SKILLS_DIR
    old_hub_dir = hub.HUB_DIR
    old_audit_log = hub.AUDIT_LOG
    try:
        hub.SKILLS_DIR = skills_dir
        hub.HUB_DIR = hub_dir
        hub.AUDIT_LOG = hub_dir / "audit.log"
        try:
            install_path = hub._resolve_lock_install_path(entry.get("install_path", ""), name)
        except ValueError as exc:
            return False, f"Refusing to uninstall '{name}': {exc}"
        if install_path.exists():
            shutil.rmtree(install_path)
        lock.record_uninstall(name)
        try:
            hub.append_audit_log(
                "UNINSTALL",
                name,
                entry.get("source", "hub"),
                entry.get("trust_level", "n/a"),
                "n/a",
                "journey_delete",
            )
        except Exception:
            pass
        return True, f"Uninstalled '{name}' from {entry.get('install_path', '')}"
    finally:
        hub.SKILLS_DIR = old_skills_dir
        hub.HUB_DIR = old_hub_dir
        hub.AUDIT_LOG = old_audit_log


def _delete_skill(name: str) -> dict[str, Any]:
    from tools import skill_usage

    if skill_usage.get_record(name).get("pinned"):
        return {
            "ok": False,
            "message": f"'{name}' is pinned — unpin it first (intellect curator unpin {name})",
        }

    resolved = _resolve_journey_skill(name)
    if not resolved:
        return {"ok": False, "message": f"skill '{name}' not found"}
    if resolved.get("ambiguous"):
        return {"ok": False, "code": "ambiguous", "message": resolved["message"]}

    if resolved.get("source") == "hub":
        ok, message = _uninstall_hub_skill(name)
        if ok:
            _clear_skill_cache()
        return {
            "ok": ok,
            "message": (
                f"uninstalled hub skill '{name}' — reinstall via Skills panel / intellect skills"
                if ok
                else message
            ),
            "deleteMode": "uninstall",
        }

    ok, message = skill_usage.archive_skill(name)
    if ok:
        _clear_skill_cache()

    return {
        "ok": ok,
        "message": f"archived '{name}' — restore with: intellect curator restore {name}" if ok else message,
        "deleteMode": "archive",
    }


def _delete_memory(node_id: str) -> dict[str, Any]:
    source, local_idx = _parse_memory_id(node_id)
    path, chunks, local = _locate_memory(source, local_idx)

    del chunks[local]
    _write_memory(path, chunks)

    return {"ok": True, "message": f"deleted memory from {path.name}"}


def edit_node(node_id: str, content: str) -> dict[str, Any]:
    try:
        return _edit_memory(node_id, content) if parse_node_kind(node_id) == "memory" else _edit_skill(node_id, content)
    except (ValueError, IndexError) as exc:
        msg = str(exc)
        if _is_stale_message(msg):
            return _stale_error(msg)
        return {"ok": False, "message": msg}


def _edit_skill(name: str, content: str) -> dict[str, Any]:
    found = _resolve_journey_skill(name)
    if not found:
        return {"ok": False, "message": f"skill '{name}' not found"}
    if found.get("ambiguous"):
        return {"ok": False, "code": "ambiguous", "message": found["message"]}
    if found.get("path") is None:
        return {"ok": False, "message": f"skill '{name}' not found"}

    from tools.skill_manager_tool import (
        _atomic_write_text,
        _edit_skill as _do_edit,
        _security_scan_skill,
        _validate_content_size,
        _validate_frontmatter,
    )

    # Hub skills live under .hub/; skill_manager's finder skips them.
    if found.get("source") == "hub":
        from intellect_constants import get_intellect_home

        hub_root = (get_intellect_home() / "skills" / ".hub").resolve()
        skill_dir = Path(found["path"]).resolve()
        try:
            skill_dir.relative_to(hub_root)
        except ValueError:
            return {"ok": False, "message": f"refusing to edit '{name}': path escapes hub root"}
        err = _validate_frontmatter(content)
        if err:
            return {"ok": False, "message": err}
        err = _validate_content_size(content)
        if err:
            return {"ok": False, "message": err}
        skill_md = (skill_dir / "SKILL.md").resolve()
        try:
            skill_md.relative_to(hub_root)
        except ValueError:
            return {"ok": False, "message": f"refusing to edit '{name}': SKILL.md escapes hub root"}
        original = skill_md.read_text(encoding="utf-8") if skill_md.exists() else None
        _atomic_write_text(skill_md, content)
        scan_error = _security_scan_skill(skill_dir)
        if scan_error:
            if original is not None:
                _atomic_write_text(skill_md, original)
            return {"ok": False, "message": scan_error}
        _clear_skill_cache()
        return {"ok": True, "message": f"updated '{name}'"}

    result = _do_edit(name, content)
    if result.get("success"):
        _clear_skill_cache()
        return {"ok": True, "message": f"updated '{name}'"}

    return {"ok": False, "message": result.get("error", "edit failed")}


def _edit_memory(node_id: str, content: str) -> dict[str, Any]:
    source, local_idx = _parse_memory_id(node_id)
    body = content.strip()
    if not body:
        return {"ok": False, "message": "empty memory — use delete to remove it"}
    path, chunks, local = _locate_memory(source, local_idx)

    chunks[local] = body
    _write_memory(path, chunks)

    return {"ok": True, "message": f"updated memory in {path.name}"}


def _write_memory(path: Path, chunks: list[str]) -> None:
    from tools.memory_tool import MemoryStore

    MemoryStore._write_file(path, [c.strip() for c in chunks if c.strip()])


def _clear_skill_cache() -> None:
    try:
        from agent.prompt_builder import clear_skills_system_prompt_cache

        clear_skills_system_prompt_cache(clear_snapshot=True)
    except Exception:
        pass
