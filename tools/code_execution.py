"""Sandboxed code execution via Docker containers (P3-6).

Provides a secure code-cell execution engine using Docker containers with
strict resource limits.  Falls back gracefully to read-only mode when
Docker is unavailable.

Security model:
  - ``--network=none`` — no network access
  - ``--memory=256m --cpus=0.5`` — resource limits
  - ``--read-only`` — immutable root filesystem
  - ``--tmpfs /tmp:size=64m`` — minimal writable scratch space
  - ``--user=1000:1000`` — non-root execution
  - 30-second hard timeout
  - 100 KB output truncation

Container pooling: maintains a small pool of pre-created containers so
subsequent executions skip the ~2s cold-start penalty.  Containers are
reset between uses (``docker start`` + ``docker exec`` is ~10x faster
than ``docker run``).

Usage::

    from tools.code_execution import CodeCellExecutor

    executor = CodeCellExecutor()
    result = executor.execute("print('hello')", language="python")
    # CodeCellResult(stdout="hello\\n", stderr="", exit_code=0, ...)
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

_MAX_CODE_LENGTH = 5000          # characters
_MAX_OUTPUT_LENGTH = 100_000     # characters (~100 KB)
_EXECUTION_TIMEOUT = 30          # seconds
_CONTAINER_MEMORY = "256m"
_CONTAINER_CPUS = "0.5"
_TMPFS_SIZE = "64m"
_POOL_SIZE = 3                   # pre-warmed containers
_POOL_MAX_IDLE_AGE = 300         # seconds before recycling an idle container

_SUPPORTED_LANGUAGES = {
    "python": {
        "image": "python:3.12-slim",
        "cmd": ["python"],
        "extension": ".py",
    },
    "bash": {
        "image": "bash:5.2",
        "cmd": ["bash"],
        "extension": ".sh",
    },
    "javascript": {
        "image": "node:22-slim",
        "cmd": ["node"],
        "extension": ".js",
    },
}


# ── Result ───────────────────────────────────────────────────────────────


@dataclass
class CodeCellResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    execution_time_ms: float = 0.0
    truncated: bool = False
    error: str = ""
    language: str = "python"


# ── Executor ─────────────────────────────────────────────────────────────


class CodeCellExecutor:
    """Execute code cells in sandboxed Docker containers."""

    def __init__(self) -> None:
        self._available: bool | None = None  # None = not yet probed
        self._lock = threading.Lock()
        self._pool: deque[str] = deque()        # container names
        self._pool_timestamps: dict[str, float] = {}

    # ── Public API ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if Docker is available and a suitable image exists."""
        if self._available is not None:
            return self._available
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, timeout=5,
            )
            self._available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._available = False
        return self._available

    def list_languages(self) -> list[str]:
        """Return the list of supported languages (runtime-dependent)."""
        available = []
        for lang, cfg in _SUPPORTED_LANGUAGES.items():
            if self._image_available(cfg["image"]):
                available.append(lang)
        return available or list(_SUPPORTED_LANGUAGES.keys())

    def execute(
        self,
        code: str,
        *,
        language: str = "python",
        timeout: int = _EXECUTION_TIMEOUT,
    ) -> CodeCellResult:
        """Execute *code* in a sandboxed container.  Blocks until complete."""
        t0 = time.time()

        # Pre-flight validation
        code = str(code or "").strip()
        if not code:
            return CodeCellResult(
                error="No code provided", language=language,
            )
        if len(code) > _MAX_CODE_LENGTH:
            return CodeCellResult(
                error=f"Code exceeds {_MAX_CODE_LENGTH} character limit ({len(code)} chars)",
                language=language,
            )

        lang_cfg = _SUPPORTED_LANGUAGES.get(language)
        if lang_cfg is None:
            return CodeCellResult(
                error=f"Unsupported language: {language}. Supported: {', '.join(_SUPPORTED_LANGUAGES)}",
                language=language,
            )

        if not self.is_available():
            return CodeCellResult(
                error="Docker is not available. Install Docker to enable code execution.",
                language=language,
            )

        # Write code to a temp file and copy into the container (F2 fix)
        import tempfile
        container_path = f"/tmp/code{lang_cfg['extension']}"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=lang_cfg["extension"], delete=False,
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        try:
            container = self._acquire_container(lang_cfg["image"])
            # F2: copy temp file into the container's tmpfs
            subprocess.run(
                ["docker", "cp", tmp_path, f"{container}:{container_path}"],
                capture_output=True, timeout=10, check=True,
            )
            cmd = lang_cfg["cmd"] + [container_path]

            result = subprocess.run(
                [
                    "docker", "exec",
                    "--user", "1000:1000",
                    container,
                    *cmd,
                ],
                capture_output=True, text=True,
                timeout=timeout,
                env={**os.environ, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            truncated = False

            if len(stdout) > _MAX_OUTPUT_LENGTH:
                stdout = stdout[:_MAX_OUTPUT_LENGTH] + "\n... [output truncated]"
                truncated = True
            if len(stderr) > _MAX_OUTPUT_LENGTH:
                stderr = stderr[:_MAX_OUTPUT_LENGTH] + "\n... [output truncated]"
                truncated = True

            return CodeCellResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=result.returncode,
                execution_time_ms=(time.time() - t0) * 1000,
                truncated=truncated,
                language=language,
            )
        except subprocess.TimeoutExpired:
            self._kill_container(lang_cfg["image"])
            return CodeCellResult(
                error=f"Execution timed out after {timeout}s",
                execution_time_ms=timeout * 1000,
                language=language,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── Container Pooling ─────────────────────────────────────────────

    def _image_available(self, image: str) -> bool:
        """Check if a Docker image is available locally."""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _acquire_container(self, image: str) -> str:
        """Get a container from the pool, or create a new one."""
        with self._lock:
            # Evict stale containers
            now = time.time()
            while self._pool:
                name = self._pool[0]
                if now - self._pool_timestamps.get(name, 0) > _POOL_MAX_IDLE_AGE:
                    self._destroy_container(self._pool.popleft())
                else:
                    break

            if self._pool:
                name = self._pool.popleft()
                # Reset container: restart it (check=True catches dead containers)
                subprocess.run(
                    ["docker", "start", name],
                    capture_output=True, timeout=5, check=True,
                )
                self._pool_timestamps[name] = now
                return name

        # No pooled container — create from scratch
        name = f"intellect-codecell-{os.getpid()}-{threading.get_ident()}"
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,  # ignore if doesn't exist
        )
        # F4: ensure the image is available before running
        if not self._image_available(image):
            subprocess.run(
                ["docker", "pull", image],
                capture_output=True, timeout=120, check=True,
            )
        subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--name", name,
                "--network=none",
                f"--memory={_CONTAINER_MEMORY}",
                f"--cpus={_CONTAINER_CPUS}",
                "--read-only",
                f"--tmpfs=/tmp:size={_TMPFS_SIZE},mode=1777",
                "--user=1000:1000",
                image,
                "sleep", "3600",  # keep alive
            ],
            capture_output=True, timeout=15, check=True,
        )
        return name

    def _kill_container(self, image: str) -> None:
        """Force-kill a container (called on timeout)."""
        name = f"intellect-codecell-{os.getpid()}-{threading.get_ident()}"
        subprocess.run(
            ["docker", "kill", name],
            capture_output=True, timeout=5,
        )

    def _destroy_container(self, name: str) -> None:
        """Remove a container from the pool."""
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, timeout=5,
        )
        self._pool_timestamps.pop(name, None)

    def shutdown(self) -> None:
        """Clean up all pooled containers (call on process exit)."""
        with self._lock:
            while self._pool:
                self._destroy_container(self._pool.popleft())

    def prefill_pool(self) -> None:
        """Pre-warm the container pool (call at startup for lower latency)."""
        if not self.is_available():
            return
        for lang, cfg in _SUPPORTED_LANGUAGES.items():
            if self._image_available(cfg["image"]):
                for _ in range(min(_POOL_SIZE, 2)):
                    name = self._acquire_container(cfg["image"])
                    with self._lock:
                        self._pool.append(name)
                        self._pool_timestamps[name] = time.time()
                logger.debug("Pre-warmed %d %s containers", min(_POOL_SIZE, 2), lang)


# ── Module-level singleton ───────────────────────────────────────────────

_executor: CodeCellExecutor | None = None


def get_executor() -> CodeCellExecutor:
    """Return the module-level executor singleton."""
    global _executor
    if _executor is None:
        _executor = CodeCellExecutor()
    return _executor


def execute_code_cell(code: str, language: str = "python") -> dict:
    """Convenience function: execute code and return a dict.

    Suitable for use as a tool handler in model_tools.py.
    """
    executor = get_executor()
    result = executor.execute(code, language=language)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "execution_time_ms": int(result.execution_time_ms),
        "truncated": result.truncated,
        "error": result.error,
        "language": result.language,
    }


# ── Tool registration ────────────────────────────────────────────────────

def _register():
    """Register the code execution tool with the Intellect tool registry."""
    try:
        from tools.registry import register

        register(
            name="execute_code_cell",
            description=(
                "Execute code in a sandboxed Docker container and return "
                "the output.  Supports Python (python:3.12-slim), Bash "
                "(bash:5.2), and JavaScript/Node.js (node:22-slim).  "
                "The container has no network access, limited memory "
                "(256 MB), and a 30-second timeout.  Code is capped at "
                "5000 characters and output at 100 KB.  Use this tool "
                "when the user asks you to write and run code."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The source code to execute.",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python", "bash", "javascript"],
                        "description": "Programming language. Default: python.",
                        "default": "python",
                    },
                },
                "required": ["code"],
            },
            handler=execute_code_cell,
            category="development",
            requires_approval=False,
            check_fn=lambda: get_executor().is_available(),
        )
    except Exception as exc:
        logger.debug("Code execution tool registration skipped: %s", exc)


_register()
