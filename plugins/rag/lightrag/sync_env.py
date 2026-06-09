"""Generate LightRAG server ``.env`` from Intellect ``config.yaml`` + ``.env``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from utils import atomic_text_write, base_url_hostname

_OPENAI_COMPAT_DEFAULT_HOSTS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
}
_OPENAI_COMPAT_KEY_VARS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "xai": "XAI_API_KEY",
}


@dataclass
class SyncResult:
    """Outcome of :func:`build_server_env`."""

    lines: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    llm_binding: str = ""
    llm_model: str = ""
    embedding_binding: str = ""
    embedding_model: str = ""
    output_path: Optional[Path] = None
    output_reason: str = ""


def _find_repo_deploy_env() -> Optional[Path]:
    """Locate ``deploy/lightrag/.env`` by walking up to the repo root."""
    cur = Path(__file__).resolve().parent
    for parent in cur.parents:
        if (parent / "pyproject.toml").is_file():
            deploy_dir = parent / "deploy" / "lightrag"
            if deploy_dir.is_dir():
                return deploy_dir / ".env"
            return None
    return None


def default_output_path() -> Tuple[Path, str]:
    """Return (path, reason) for the default server env file."""
    deploy_env = _find_repo_deploy_env()
    if deploy_env is not None:
        return deploy_env, "repo deploy/lightrag/.env"
    try:
        from intellect_constants import get_intellect_home

        p = get_intellect_home() / "lightrag" / "server.env"
        return p, "profile-local (pip install or no repo deploy/)"
    except Exception:
        p = Path.home() / ".intellect" / "lightrag" / "server.env"
        return p, "profile-local fallback"


def _loopback_host(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    return h in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _rewrite_host_for_docker(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if _loopback_host(parsed.hostname or ""):
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.rstrip("/")
        return f"http://host.docker.internal{port}{path}"
    return url.rstrip("/")


def _normalize_openai_base(url: str) -> str:
    """Append ``/v1`` only for bare host URLs; leave custom paths untouched."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    parsed = urlparse(u if "://" in u else f"http://{u}")
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/v1") or path.endswith("/chat/completions"):
        return u
    if path and path != "/":
        return u
    return f"{u}/v1"


def _is_openrouter(provider: str, base_url: str) -> bool:
    if (provider or "").strip().lower() == "openrouter":
        return True
    host = base_url_hostname(base_url)
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def _is_ollama_endpoint(base_url: str, provider: str) -> bool:
    p = (provider or "").strip().lower()
    if p in {"ollama", "ollama-cloud"}:
        return True
    host = (base_url_hostname(base_url) or "").lower()
    if "ollama" in host:
        return True
    parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    if parsed.port == 11434 and "ollama" in host:
        return True
    return False


def _resolve_intellect_runtime() -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    warnings: List[str] = []
    try:
        from intellect_cli.config import load_config

        config = load_config()
    except Exception as exc:
        return {}, {}, [f"could not load config.yaml: {exc}"]

    model_cfg = config.get("model") or {}
    if not isinstance(model_cfg, dict):
        model_cfg = {}

    model_name = str(model_cfg.get("default") or "").strip()
    if not model_name:
        warnings.append("model.default is empty — set via `intellect model` first")

    try:
        from intellect_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider()
    except Exception as exc:
        warnings.append(f"could not resolve runtime provider: {exc}")
        runtime = {}

    return model_cfg, runtime, warnings


def _map_llm_binding(
    *,
    provider: str,
    base_url: str,
    api_mode: str,
    for_docker: bool,
) -> Tuple[str, str, str]:
    """Return (llm_binding, llm_binding_host, api_key_placeholder)."""
    prov = (provider or "").strip().lower()
    base = (base_url or "").strip().rstrip("/")
    mode = (api_mode or "").strip().lower()

    if prov in {"openai-codex", "xai-oauth", "google-gemini-cli"}:
        return "openai", "", ""

    if _is_ollama_endpoint(base, prov):
        host = base or "http://127.0.0.1:11434"
        if for_docker:
            host = _rewrite_host_for_docker(host)
        return "ollama", host, "ollama"

    if _is_openrouter(prov, base):
        host = _normalize_openai_base(base or "https://openrouter.ai/api/v1")
        if for_docker and _loopback_host(urlparse(host).hostname or ""):
            host = _rewrite_host_for_docker(host)
        return "openai", host, "${OPENROUTER_API_KEY}"

    if mode == "anthropic_messages" and prov == "anthropic":
        return "openai", "", ""

    if base:
        host = _normalize_openai_base(base)
        if for_docker and _loopback_host(urlparse(host).hostname or ""):
            host = _rewrite_host_for_docker(host)
        key_var = _OPENAI_COMPAT_KEY_VARS.get(prov, "OPENAI_API_KEY")
        return "openai", host, f"${{{key_var}}}"

    if prov in _OPENAI_COMPAT_DEFAULT_HOSTS:
        host = _OPENAI_COMPAT_DEFAULT_HOSTS[prov]
        key_var = _OPENAI_COMPAT_KEY_VARS[prov]
        return "openai", host, f"${{{key_var}}}"

    return "openai", "", "${OPENAI_API_KEY}"


def _pick_api_key_env(runtime: Dict[str, Any], key_placeholder: str) -> str:
    if key_placeholder.startswith("${") and key_placeholder.endswith("}"):
        return key_placeholder
    api_key = str(runtime.get("api_key") or "").strip()
    if api_key and api_key not in {"aws-sdk", "no-key-required"}:
        return api_key
    return "${OPENAI_API_KEY}"


def build_server_env(
    *,
    embedding_model: str = "",
    for_docker: bool = False,
) -> SyncResult:
    """Build LightRAG server ``.env`` lines from Intellect model settings."""
    model_cfg, runtime, warnings = _resolve_intellect_runtime()
    result = SyncResult(warnings=list(warnings))

    model_name = str(model_cfg.get("default") or "").strip()
    provider = str(runtime.get("provider") or model_cfg.get("provider") or "").strip()
    base_url = str(runtime.get("base_url") or model_cfg.get("base_url") or "").strip()
    api_mode = str(runtime.get("api_mode") or model_cfg.get("api_mode") or "").strip()

    if provider in {"openai-codex", "xai-oauth"}:
        warnings.append(
            f"Intellect provider {provider!r} is OAuth-only; "
            "LightRAG server needs an API-key binding — set OPENAI_API_KEY or "
            "OLLAMA in the generated file manually"
        )
    if api_mode == "anthropic_messages" and provider == "anthropic":
        warnings.append(
            "Native Anthropic Messages API is not mapped automatically; "
            "use an OpenAI-compatible proxy or set LLM_BINDING manually"
        )
    if provider == "xai" and api_mode == "codex_responses":
        warnings.append(
            "Intellect xAI uses codex_responses; LightRAG server expects an "
            "OpenAI-chat-compatible endpoint — verify LLM_BINDING_HOST works"
        )

    llm_binding, llm_host, key_ph = _map_llm_binding(
        provider=provider,
        base_url=base_url,
        api_mode=api_mode,
        for_docker=for_docker,
    )

    if llm_binding == "ollama":
        emb_binding = "ollama"
        emb_host = llm_host
        emb_model = embedding_model or "bge-m3:latest"
        llm_model = model_name or "mistral-nemo:latest"
        openai_key_line = ""
    else:
        emb_binding = "openai"
        emb_host = llm_host or "https://api.openai.com/v1"
        emb_model = embedding_model or "text-embedding-3-small"
        llm_model = model_name or "gpt-4o-mini"
        openai_key_line = _pick_api_key_env(runtime, key_ph)

    result.llm_binding = llm_binding
    result.llm_model = llm_model
    result.embedding_binding = emb_binding
    result.embedding_model = emb_model

    header = [
        "# Generated by: intellect lightrag sync-server-env",
        "# Source: Intellect config.yaml model.* + runtime credentials",
        "# Re-run after `intellect model` changes. Pin embedding before first upload.",
        "",
    ]
    body: List[str] = [
        "# --- LLM (EXTRACT / QUERY / KEYWORDS) ---",
        f"LLM_BINDING={llm_binding}",
        f"LLM_MODEL={llm_model}",
    ]
    if llm_host:
        body.append(f"LLM_BINDING_HOST={llm_host}")
    if openai_key_line:
        if openai_key_line.startswith("${"):
            var = openai_key_line[2:-1]
            body.append(f"{var}=")
            body.append(f"OPENAI_API_KEY=${{{var}}}")
        else:
            body.append(f"OPENAI_API_KEY={openai_key_line}")

    body.extend([
        "",
        "# --- Embedding ---",
        f"EMBEDDING_BINDING={emb_binding}",
        f"EMBEDDING_MODEL={emb_model}",
    ])
    if emb_host and emb_binding == "ollama":
        body.append(f"EMBEDDING_BINDING_HOST={emb_host}")
    elif emb_host and emb_binding == "openai":
        body.append(f"EMBEDDING_BINDING_HOST={emb_host}")

    body.extend([
        "",
        "# --- Server ---",
        "WORKSPACE=",
        "PORT=9621",
        "",
        "# --- PostgreSQL (docker-compose.webui.yml only) ---",
        "POSTGRES_HOST=postgres-lightrag",
        "POSTGRES_PORT=5432",
        "POSTGRES_USER=lightrag",
        "POSTGRES_PASSWORD=change-me",
        "POSTGRES_DB=lightrag",
        "LIGHTRAG_PG_PASSWORD=change-me",
        "",
    ])

    if for_docker:
        body.insert(4, "# Docker: localhost hosts rewritten to host.docker.internal")

    result.lines = header + body
    result.warnings = warnings
    return result


def write_server_env(
    output: Path,
    *,
    embedding_model: str = "",
    for_docker: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    result = build_server_env(
        embedding_model=embedding_model,
        for_docker=for_docker,
    )
    text = "\n".join(result.lines)
    if not text.endswith("\n"):
        text += "\n"
    result.output_path = output
    if dry_run:
        return result
    atomic_text_write(output, text, mode=0o600)
    return result
