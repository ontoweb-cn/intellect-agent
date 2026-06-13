"""
Doctor command for intellect CLI.

Diagnoses issues with Intellect Agent setup.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

from intellect_cli.config import get_project_root, get_intellect_home, get_env_path
from intellect_cli.env_loader import load_intellect_dotenv
from intellect_constants import display_intellect_home

PROJECT_ROOT = get_project_root()
INTELLECT_HOME = get_intellect_home()
_DHH = display_intellect_home()  # user-facing display path (e.g. ~/.intellect or ~/.intellect/profiles/coder)

# Load environment variables from ~/.intellect/.env so API key checks work
_env_path = get_env_path()
load_intellect_dotenv(intellect_home=_env_path.parent, project_env=PROJECT_ROOT / ".env")

from intellect_cli.colors import Colors, color
from intellect_cli.models import _INTELLECT_USER_AGENT
from intellect_constants import OPENROUTER_MODELS_URL
from utils import base_url_host_matches


_PROVIDER_ENV_HINTS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_BASE_URL",
    "ONTOWEB_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CN_API_KEY",
    "GMI_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "HF_TOKEN",
    "OPENCODE_ZEN_API_KEY",
    "OPENCODE_GO_API_KEY",
    "XIAOMI_API_KEY",
    "VOLCENGINE_API_KEY",
    "VOLCENGINE_CODING_PLAN_API_KEY",
    "VOLCENGINE_AGENT_PLAN_API_KEY",
    "TOKENHUB_API_KEY",
)


from intellect_constants import is_termux as _is_termux


def _python_install_cmd() -> str:
    return "python -m pip install" if _is_termux() else "uv pip install"


def _system_package_install_cmd(pkg: str) -> str:
    if _is_termux():
        return f"pkg install {pkg}"
    if sys.platform == "darwin":
        return f"brew install {pkg}"
    return f"sudo apt install {pkg}"


def _safe_which(cmd: str) -> str | None:
    """shutil.which wrapper resilient to platform monkeypatching in tests."""
    try:
        return shutil.which(cmd)
    except Exception:
        return None


def _termux_browser_setup_steps(node_installed: bool) -> list[str]:
    steps: list[str] = []
    step = 1
    if not node_installed:
        steps.append(f"{step}) pkg install nodejs")
        step += 1
    steps.append(f"{step}) npm install -g agent-browser")
    steps.append(f"{step + 1}) agent-browser install")
    return steps


def _termux_install_all_fallback_notes() -> list[str]:
    return [
        "Termux install profile: use .[termux-all] for broad compatibility (installer default on Termux).",
        "Matrix E2EE extra is excluded on Termux (python-olm currently fails to build).",
        "Local faster-whisper extra is excluded on Termux (ctranslate2/av build path unavailable).",
        "STT fallback: use Groq Whisper (set GROQ_API_KEY) or OpenAI Whisper (set VOICE_TOOLS_OPENAI_KEY).",
    ]


def _has_provider_env_config(content: str) -> bool:
    """Return True when ~/.intellect/.env contains provider auth/base URL settings."""
    return any(key in content for key in _PROVIDER_ENV_HINTS)


def _honcho_is_configured_for_doctor() -> bool:
    """Return True when Honcho is configured, even if this process has no active session."""
    try:
        from plugins.memory.honcho.client import HonchoClientConfig

        cfg = HonchoClientConfig.from_global_config()
        return bool(cfg.enabled and (cfg.api_key or cfg.base_url))
    except Exception:
        return False


def _is_kanban_worker_env_gate(item: dict) -> bool:
    """Return True when Kanban is unavailable only because this is not a worker process."""
    if item.get("name") != "kanban":
        return False
    if os.environ.get("INTELLECT_KANBAN_TASK"):
        return False

    tools = item.get("tools") or []
    return bool(tools) and all(str(tool).startswith("kanban_") for tool in tools)


def _doctor_tool_availability_detail(toolset: str) -> str:
    """Optional explanatory suffix for toolsets whose doctor status needs context."""
    if toolset == "kanban" and not os.environ.get("INTELLECT_KANBAN_TASK"):
        return "(runtime-gated; loaded only for dispatcher-spawned workers)"
    return ""


def _apply_doctor_tool_availability_overrides(available: list[str], unavailable: list[dict]) -> tuple[list[str], list[dict]]:
    """Adjust runtime-gated tool availability for doctor diagnostics."""
    updated_available = list(available)
    updated_unavailable = []
    for item in unavailable:
        name = item.get("name")
        if _is_kanban_worker_env_gate(item):
            if "kanban" not in updated_available:
                updated_available.append("kanban")
            continue
        if name == "honcho" and _honcho_is_configured_for_doctor():
            if "honcho" not in updated_available:
                updated_available.append("honcho")
            continue
        updated_unavailable.append(item)
    return updated_available, updated_unavailable


def _has_healthy_oauth_fallback_for_apikey_provider(provider_label: str) -> bool:
    """Return True when a direct API-key probe failure is non-blocking.

    Some provider families support both a direct API-key path and a separate
    OAuth runtime path. When the OAuth path is already healthy, doctor should
    still show a failed API-key connectivity row, but it should not promote
    that direct-key problem into the final blocking summary.
    """
    normalized = (provider_label or "").strip().lower()
    if normalized in {"google / gemini", "gemini"}:
        try:
            from intellect_cli.auth import get_gemini_oauth_auth_status
            return bool((get_gemini_oauth_auth_status() or {}).get("logged_in"))
        except Exception:
            return False
    if normalized == "minimax":
        try:
            from intellect_cli.auth import get_minimax_oauth_auth_status
            return bool((get_minimax_oauth_auth_status() or {}).get("logged_in"))
        except Exception:
            return False
    if normalized == "xai":
        try:
            from intellect_cli.auth import get_xai_oauth_auth_status
            return bool((get_xai_oauth_auth_status() or {}).get("logged_in"))
        except Exception:
            return False
    return False


def check_ok(text: str, detail: str = ""):
    print(f"  {color('✓', Colors.GREEN)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_warn(text: str, detail: str = ""):
    print(f"  {color('⚠', Colors.YELLOW)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_fail(text: str, detail: str = ""):
    print(f"  {color('✗', Colors.RED)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_info(text: str):
    print(f"    {color('→', Colors.CYAN)} {text}")


def _section(title: str) -> None:
    """Print a doctor section banner: blank line + bold cyan ◆ title."""
    print()
    print(color(f"◆ {title}", Colors.CYAN, Colors.BOLD))


def _fail_and_issue(text: str, detail: str, fix: str, issues: list[str]) -> None:
    """Emit a check_fail and append the corresponding fix instruction."""
    check_fail(text, detail)
    issues.append(fix)


def _read_pyproject_version() -> str | None:
    """Read the ``version = "..."`` from ``pyproject.toml`` at the project root.

    Returns None when running from an installed wheel (no pyproject.toml ships
    with the package) or when the file can't be parsed. Reads only the
    ``[project]`` version, ignoring any version strings that appear in other
    tables.
    """
    pyproject = PROJECT_ROOT / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    in_project = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version") and "=" in line:
            value = line.split("=", 1)[1]
            value = value.split("#", 1)[0].strip().strip("\"'")
            return value or None
    return None


def _check_version_consistency(issues: list[str]) -> None:
    """Verify pyproject.toml version matches intellect_cli.__version__.

    A git conflict resolution (reset/merge) can revert one file without the
    other, leaving ``intellect --version`` reporting a stale version while
    ``pyproject.toml`` is current. Detect that drift so users can re-sync.
    Silent no-op for installed wheels where pyproject.toml isn't present.
    """
    try:
        from intellect_cli import __version__ as init_version
    except Exception:
        return
    pyproject_version = _read_pyproject_version()
    if pyproject_version is None:
        # Installed wheel or unreadable pyproject — nothing to cross-check.
        return
    if pyproject_version == init_version:
        check_ok("Version files consistent", f"({init_version})")
    else:
        _fail_and_issue(
            "Version mismatch between source files",
            f"(pyproject.toml {pyproject_version} != intellect_cli/__init__.py {init_version})",
            "Re-sync version files (e.g. run 'intellect update', or set "
            "intellect_cli/__init__.py __version__ to match pyproject.toml)",
            issues,
        )


def _check_s6_supervision(issues: list[str]) -> None:
    """Inside a container under our s6 /init, surface what s6 sees.

    Runs as a counterpart to :func:`_check_gateway_service_linger` for
    the systemd-on-host case. No-op everywhere except in the s6
    container so host runs aren't cluttered with irrelevant output.

    Reports:
      - Whether the main-intellect static service is up
      - How many per-profile gateway slots are registered (via
        ``S6ServiceManager.list_profile_gateways()``) and how many are
        currently supervised as ``up``
    """
    try:
        from intellect_cli.service_manager import (
            S6ServiceManager,
            detect_service_manager,
        )
    except Exception:
        return

    if detect_service_manager() != "s6":
        return

    _section("s6 Supervision")

    mgr = S6ServiceManager()

    # Static services. They live under /run/service/ via s6-rc symlinks,
    # so the same s6-svstat probe works.
    for static in ("main-intellect",):
        if mgr.is_running(static):
            check_ok(f"{static}: up")
        else:
            check_info(f"{static}: down (expected if not enabled via env)")

    profiles = mgr.list_profile_gateways()
    if not profiles:
        check_info("No per-profile gateways registered yet — create one with `intellect profile create <name>`")
        return

    up_count = sum(1 for p in profiles if mgr.is_running(f"gateway-{p}"))
    check_ok(
        f"Per-profile gateways: {up_count}/{len(profiles)} supervised up"
        + (f" ({', '.join(sorted(profiles))})" if len(profiles) <= 8 else "")
    )


def _check_gateway_service_linger(issues: list[str]) -> None:
    """Warn when a systemd user gateway service will stop after logout.

    Skipped inside a container running under s6 — the linger concept
    (user-systemd surviving SSH logout) doesn't apply there, and the
    s6 supervision state is surfaced separately by
    ``_check_s6_supervision``.
    """
    try:
        from intellect_cli.gateway import (
            get_systemd_linger_status,
            get_systemd_unit_path,
            is_linux,
        )
        from intellect_cli.service_manager import detect_service_manager
    except Exception as e:
        check_warn("Gateway service linger", f"(could not import gateway helpers: {e})")
        return

    if not is_linux():
        return

    # Inside a container under our s6 /init, _check_s6_supervision
    # reports the live supervision state; the linger warning would be
    # confusing here (no systemd, no logout, no "lingering" concept).
    if detect_service_manager() == "s6":
        return

    unit_path = get_systemd_unit_path()
    if not unit_path.exists():
        return

    _section("Gateway Service")
    linger_enabled, linger_detail = get_systemd_linger_status()
    if linger_enabled is True:
        check_ok("Systemd linger enabled", "(gateway service survives logout)")
    elif linger_enabled is False:
        check_warn("Systemd linger disabled", "(gateway may stop after logout)")
        check_info("Run: sudo loginctl enable-linger $USER")
        issues.append("Enable linger for the gateway user service: sudo loginctl enable-linger $USER")
    else:
        check_warn("Could not verify systemd linger", f"({linger_detail})")


def _check_project_health(issues: list[str]) -> None:
    """Check multi-project configuration health (spec §31)."""
    from intellect_cli.config import load_config

    cfg = load_config()
    members = cfg.get("members", {}) if isinstance(cfg, dict) else {}
    projects_cfg = members.get("projects", {}) if isinstance(members, dict) else {}

    # PROJECTS_ENABLED_NO_MEMBERS (check before early return)
    if projects_cfg.get("enabled") and not members.get("enabled"):
        _section("Projects")
        _fail_and_issue("projects.enabled=true but members.enabled=false",
                        "(projects require members to be enabled)",
                        "Set members.enabled: true in config.yaml",
                        issues)
        return

    if not members.get("enabled") or not projects_cfg.get("enabled"):
        return  # Projects not enabled — nothing to check

    _section("Projects")

    try:
        from agent.projects import ProjectDB
        db = ProjectDB(config=cfg)
    except Exception:
        check_warn("Could not open project database")
        return

    try:
        projects = db.list_projects(include_archived=False)
        all_projects = db.list_projects(include_archived=True)

        # PROJECT_DEFAULT_NOT_FOUND
        default_slug = projects_cfg.get("default_project")
        if default_slug:
            dp = db.get_project_by_slug(default_slug)
            if not dp:
                check_warn(f"default_project '{default_slug}' not found",
                           "Run: intellect members projects bootstrap")
            else:
                check_ok(f"Default project '{default_slug}' exists")

        # Check each active project
        for p in projects:
            slug = p.get("slug", p.get("id", "?"))
            pid = p["id"]
            members_list = db.get_project_members(pid)

            # PROJECT_NO_ADMIN
            admins = [m for m in members_list if m.get("role") == "admin"]
            if not admins:
                check_warn(f"Project '{slug}': no admin",
                           f"Assign with: intellect members projects admin add {slug} <member>")
            else:
                check_ok(f"Project '{slug}': {len(members_list)} member(s), "
                         f"{len(admins)} admin(s)")

            # PROJECT_ORPHANED
            if not members_list:
                check_info(f"Project '{slug}' has no members (orphaned)")

            # PROJECT_ENV_PERMISSIONS
            _check_project_env_perms(slug)

            # PROJECT_WORKSPACE_MISSING
            repo_url = p.get("repo_url")
            if repo_url:
                from agent.project_workspace import get_workspace_path, resolve_workspace
                ws = resolve_workspace(slug, config=cfg)
                if ws and (get_workspace_path(slug, config=cfg) / ".git").exists():
                    check_ok(f"Project '{slug}': workspace cloned")
                else:
                    check_info(f"Project '{slug}': workspace not cloned",
                               f"Run: intellect members projects clone {slug}")

                # PROJECT_GIT_AUTH_MISSING
                from agent.project_env import read_project_env
                env = read_project_env(slug, config=cfg)
                if not env.get("GIT_USERNAME") and not env.get("GIT_TOKEN") and not env.get("GIT_SSH_KEY"):
                    if repo_url.startswith("https://") or repo_url.startswith("git@"):
                        check_warn(f"Project '{slug}': repo_url set but no git credentials",
                                   f"Set GIT_USERNAME + GIT_TOKEN or GIT_SSH_KEY via: "
                                   f"intellect members projects env set {slug} GIT_TOKEN <token>")

        # Summary
        if projects:
            archived = len(all_projects) - len(projects)
            detail = f"{len(all_projects)} total"
            if archived:
                detail += f", {archived} archived"
            check_ok(f"{len(projects)} active project(s)", detail)
        else:
            check_info("No active projects",
                       "Run: intellect members projects bootstrap")

    finally:
        try:
            db.close()
        except Exception:
            pass


def _check_oauth_health(issues: list[str]) -> None:
    """Check OAuth configuration health."""
    from intellect_cli.config import load_config

    cfg = load_config()
    if not isinstance(cfg, dict):
        return
    members = cfg.get("members", {})
    if not isinstance(members, dict):
        return
    oauth_cfg = members.get("oauth", {})
    if not isinstance(oauth_cfg, dict) or not oauth_cfg.get("enabled"):
        return

    _section("OAuth")

    try:
        from agent.members_oauth import list_enabled_providers, is_oauth_enabled
    except ImportError:
        check_warn("OAuth module not available")
        return

    if not is_oauth_enabled(cfg):
        return

    yaml_providers = (cfg.get("members", {}) or {}).get("oauth", {}).get("providers", [])
    if yaml_providers:
        try:
            from agent.oauth.migrate_from_config import migration_marker_exists

            migrated = migration_marker_exists()
        except ImportError:
            migrated = False
        hint = "Run: intellect oauth migrate-from-config --write-config"
        if migrated:
            hint = (
                "YAML list still present; run migrate-from-config --write-config "
                "to clear it, or remove members.oauth.providers manually"
            )
        check_warn(
            "members.oauth.providers in config.yaml is deprecated",
            hint,
        )

    enabled = list_enabled_providers(cfg)
    if not enabled:
        _fail_and_issue(
            "OAuth enabled but no login providers enabled in state.db",
            "(enable built-ins via Settings or CLI)",
            "Run: intellect oauth list && intellect oauth enable github "
            "(or google / gitee / feishu)",
            issues,
        )
        return

    check_ok(f"{len(enabled)} login provider(s) enabled: {', '.join(p['id'] for p in enabled)}")

    from agent.oauth.provider_resolution import enterprise_provider_config_hint

    for p in enabled:
        pid = p["id"]
        merged = __import__("agent.members_oauth", fromlist=["resolve_provider"]).resolve_provider(cfg, pid)
        if merged:
            from agent.members_oauth import provider_oauth_login_ready, get_provider_secret

            if not provider_oauth_login_ready(merged):
                hint = enterprise_provider_config_hint(pid)
                check_warn(f"Provider '{pid}': incomplete OAuth login config", hint)
            elif not merged.get("client_id") and pid not in ("wecom", "dingtalk", "feishu", "lark"):
                check_warn(f"Provider '{pid}': missing client_id (set in Settings)")
            elif not get_provider_secret(merged):
                secret_env = merged.get("client_secret_env", f"{pid.upper()}_OAUTH_CLIENT_SECRET")
                check_warn(
                    f"Provider '{pid}': OAuth secret not configured",
                    f"Set credentials in Settings or {secret_env} in ~/.intellect/.env",
                )
            else:
                check_ok(f"Provider '{pid}': credentials configured")

            # Git integration note
            from agent.members_oauth import is_git_host_provider, get_git_host_for_provider
            if is_git_host_provider(pid):
                git_host = get_git_host_for_provider(pid)
                if oauth_cfg.get("store_git_token"):
                    check_ok(f"Provider '{pid}': git auth enabled for {git_host}")
                else:
                    check_info(f"Provider '{pid}': git auth disabled (store_git_token=false)")

    check_info("OAuth login: intellect members login --oauth <provider>")

    th_cfg = oauth_cfg.get("trusted_header") or {}
    if th_cfg.get("enabled"):
        header_name = th_cfg.get("header") or "X-Forwarded-User"
        map_mode = th_cfg.get("map") or "email"
        check_ok(f"Trusted header SSO enabled ({header_name}, map={map_mode})")
        if map_mode not in ("email", "username"):
            check_warn(
                f"trusted_header.map is '{map_mode}' (expected email or username)",
            )
        if th_cfg.get("require_localhost_upstream", True):
            check_info(
                "trusted_header.require_localhost_upstream: true — "
                "only trust headers from localhost upstream",
            )
    else:
        check_info("Trusted header SSO: disabled (members.oauth.trusted_header.enabled)")


def _check_session_isolation_health(issues: list[str]) -> None:
    """Warn when multi-user mode has unowned (NULL member_id) sessions."""
    from intellect_cli.config import load_config

    cfg = load_config()
    if not isinstance(cfg, dict):
        return
    members = cfg.get("members", {})
    if not isinstance(members, dict) or not members.get("enabled"):
        return

    _section("Session isolation")

    iso = members.get("session_isolation") or {}
    legacy = iso.get("legacy_null_visibility", "strict")
    check_ok(f"legacy_null_visibility: {legacy!r}")

    try:
        from intellect_cli.members_sessions import count_null_member_sessions

        counts = count_null_member_sessions()
    except Exception as exc:
        check_warn("Session ownership audit skipped", str(exc))
        return

    if counts["json_null"] or counts["db_null"]:
        _fail_and_issue(
            f"Unowned sessions: {counts['json_null']} JSON, {counts['db_null']} session-store row(s)",
            "(member_id NULL — hidden from members under strict mode)",
            "Run: intellect members sessions audit-null && "
            "intellect members sessions migrate-ownership --member-id <id>",
            issues,
        )
    else:
        check_ok("No NULL member_id sessions in JSON or session store")


def _check_project_env_perms(slug: str) -> None:
    """Check that a project's .env file has 0600 permissions."""
    import stat
    from agent.project_env import _env_path
    env_path = _env_path(slug)
    if not env_path.exists():
        return  # No .env file — nothing to check
    try:
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode != 0o600:
            check_warn(f"Project '{slug}': .env permissions are {oct(mode)} (should be 600)",
                       f"Fix: chmod 600 {env_path}")
    except OSError:
        pass


_APIKEY_PROVIDERS_CACHE: list | None = None


def _build_apikey_providers_list() -> list:
    """Build the API-key provider health-check list once and cache it.

    Tuple format: (name, env_vars, default_url, base_env, supports_models_endpoint)
    Base list augmented with any ProviderProfile with auth_type="api_key" not
    already present — adding plugins/model-providers/<name>/ is sufficient to get into doctor.
    """
    _static = [
        ("Z.AI / GLM",      ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), "https://api.z.ai/api/paas/v4/models", "GLM_BASE_URL", True),
        ("Kimi / Moonshot",  ("KIMI_API_KEY",),                              "https://api.moonshot.ai/v1/models",   "KIMI_BASE_URL", True),
        ("StepFun Step Plan", ("STEPFUN_API_KEY",),                          "https://api.stepfun.ai/step_plan/v1/models", "STEPFUN_BASE_URL", True),
        ("Kimi / Moonshot (China)", ("KIMI_CN_API_KEY",),                    "https://api.moonshot.cn/v1/models",   None, True),
        ("Arcee AI",         ("ARCEEAI_API_KEY",),                           "https://api.arcee.ai/api/v1/models",  "ARCEE_BASE_URL", True),
        ("GMI Cloud",        ("GMI_API_KEY",),                               "https://api.gmi-serving.com/v1/models", "GMI_BASE_URL", True),
        ("DeepSeek",         ("DEEPSEEK_API_KEY",),                          "https://api.deepseek.com/v1/models",  "DEEPSEEK_BASE_URL", True),
        ("Hugging Face",     ("HF_TOKEN",),                                  "https://router.huggingface.co/v1/models", "HF_BASE_URL", True),
        ("NVIDIA NIM",       ("NVIDIA_API_KEY",),                            "https://integrate.api.nvidia.com/v1/models", "NVIDIA_BASE_URL", True),
        ("Alibaba/DashScope", ("DASHSCOPE_API_KEY",),                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models", "DASHSCOPE_BASE_URL", True),
        # MiniMax global: /v1 endpoint supports /models.
        ("MiniMax",          ("MINIMAX_API_KEY",),                           "https://api.minimax.io/v1/models",    "MINIMAX_BASE_URL", True),
        # MiniMax CN: /v1 endpoint does NOT support /models (returns 404).
        ("MiniMax (China)",  ("MINIMAX_CN_API_KEY",),                        "https://api.minimaxi.com/v1/models",  "MINIMAX_CN_BASE_URL", False),
        ("Kilo Code",        ("KILOCODE_API_KEY",),                          "https://api.kilo.ai/api/gateway/models", "KILOCODE_BASE_URL", True),
        ("OpenCode Zen",     ("OPENCODE_ZEN_API_KEY",),                      "https://opencode.ai/zen/v1/models",  "OPENCODE_ZEN_BASE_URL", True),
        # OpenCode Go has no shared /models endpoint; skip the health check.
        ("OpenCode Go",      ("OPENCODE_GO_API_KEY",),                       None,                                  "OPENCODE_GO_BASE_URL", False),
    ]
    _known_names = {t[0] for t in _static}
    # Also index by profile canonical name so profiles without display_name
    # don't create duplicate entries for providers already in the static list.
    _known_canonical: set[str] = set()
    _name_to_canonical = {
        "Z.AI / GLM": "zai", "Kimi / Moonshot": "kimi-coding",
        "StepFun Step Plan": "stepfun", "Kimi / Moonshot (China)": "kimi-coding-cn",
        "Arcee AI": "arcee", "GMI Cloud": "gmi", "DeepSeek": "deepseek",
        "Hugging Face": "huggingface", "NVIDIA NIM": "nvidia",
        "Alibaba/DashScope": "alibaba", "MiniMax": "minimax",
        "MiniMax (China)": "minimax-cn",
        "Kilo Code": "kilocode", "OpenCode Zen": "opencode-zen",
        "OpenCode Go": "opencode-go",
    }
    for _label, _canonical in _name_to_canonical.items():
        _known_canonical.add(_canonical)
    # Providers that already have a dedicated health check above the generic
    # API-key loop (with custom headers/auth). Skip their pluggable profiles
    # here so the generic Bearer-auth loop doesn't run a duplicate, broken
    # check (e.g. Anthropic native API requires x-api-key, not Bearer).
    _dedicated_canonical = {"anthropic", "openrouter", "bedrock"}
    _known_canonical.update(_dedicated_canonical)
    try:
        from providers import list_providers
        from providers.base import ProviderProfile as _PP
        try:
            from intellect_cli.providers import normalize_provider as _normalize_provider
        except Exception:  # pragma: no cover - normalization is best-effort
            def _normalize_provider(_name: str) -> str:
                return (_name or "").strip().lower()
        for _pp in list_providers():
            if not isinstance(_pp, _PP) or _pp.auth_type != "api_key" or not _pp.env_vars:
                continue
            _label = _pp.display_name or _pp.name
            if _label in _known_names or _pp.name in _known_canonical:
                continue
            _candidates = {_normalize_provider(_pp.name)}
            for _alias in (_pp.aliases or ()):
                _candidates.add(_normalize_provider(_alias))
            if _candidates & _dedicated_canonical:
                continue
            # Separate API-key vars from base-URL override vars — the health-check
            # loop sends the first found value as Authorization: Bearer, so a URL
            # string must never be picked.
            _key_vars = tuple(
                v for v in _pp.env_vars
                if not v.endswith("_BASE_URL") and not v.endswith("_URL")
            )
            _base_var = next(
                (v for v in _pp.env_vars if v.endswith("_BASE_URL") or v.endswith("_URL")),
                None,
            )
            if not _key_vars:
                continue
            _models_url = (
                (_pp.models_url or (_pp.base_url.rstrip("/") + "/models"))
                if _pp.base_url else None
            )
            _hc = getattr(_pp, "supports_health_check", True)
            _static.append((_label, _key_vars, _models_url, _base_var, _hc))
    except Exception:
        pass
    return _static


def _check_session_store(
    intellect_home: Path,
    *,
    issues: list,
    should_fix: bool = False,
) -> int:
    """Probe the active session store (SQLite)."""
    fixed_count = 0

    state_db_path = intellect_home / "state.db"
    if state_db_path.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(state_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            count = cursor.fetchone()[0]
            conn.close()
            check_ok(f"{_DHH}/state.db exists ({count} sessions)")
        except Exception as e:
            check_warn(f"{_DHH}/state.db exists but has issues: {e}")
    else:
        check_info(f"{_DHH}/state.db not created yet (will be created on first session)")

    wal_path = intellect_home / "state.db-wal"
    if wal_path.exists():
        try:
            wal_size = wal_path.stat().st_size
            if wal_size > 50 * 1024 * 1024:
                check_warn(
                    f"WAL file is large ({wal_size // (1024 * 1024)} MB)",
                    "(may indicate missed checkpoints)",
                )
                if should_fix:
                    import sqlite3

                    conn = sqlite3.connect(str(state_db_path))
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn.close()
                    new_size = wal_path.stat().st_size if wal_path.exists() else 0
                    check_ok(
                        f"WAL checkpoint performed ({wal_size // 1024}K → {new_size // 1024}K)"
                    )
                    fixed_count += 1
                else:
                    issues.append(
                        "Large WAL file — run 'intellect doctor --fix' to checkpoint"
                    )
            elif wal_size > 10 * 1024 * 1024:
                check_info(
                    f"WAL file is {wal_size // (1024 * 1024)} MB (normal for active sessions)"
                )
        except Exception:
            pass

    return fixed_count


def run_doctor_storage() -> int:
    """Storage-focused diagnostics (``intellect doctor --storage``)."""
    from intellect_cli.config import load_config

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│              🩺 Intellect Doctor — Storage               │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))

    config = load_config()
    issues = 0

    _section("Storage backend")
    check_ok("storage backend: sqlite")

    home = get_intellect_home()
    sqlite_path = home / "state.db"
    if sqlite_path.is_file():
        size_kb = sqlite_path.stat().st_size // 1024
        check_ok(f"{_DHH}/state.db present ({size_kb} KB)")
    else:
        check_info(f"{_DHH}/state.db not created yet")

    _section("OAuth / members (database)")
    try:
        from intellect_state import SessionDB

        db = SessionDB()
        try:
            conn = db._conn
            prov = conn.execute(
                "SELECT COUNT(*) AS c FROM oauth_providers"
            ).fetchone()
            tok = conn.execute(
                "SELECT COUNT(*) AS c FROM oauth_tokens"
            ).fetchone()
            members = conn.execute("SELECT COUNT(*) AS c FROM members").fetchone()
            prov_n = int(prov["c"] if prov else 0)
            tok_n = int(tok["c"] if tok else 0)
            mem_n = int(members["c"] if members else 0)
            check_ok(
                f"oauth_providers={prov_n}, oauth_tokens={tok_n}, members={mem_n}"
            )
            if prov_n:
                sample = conn.execute(
                    "SELECT id, enabled FROM oauth_providers ORDER BY id LIMIT 1"
                ).fetchone()
                if sample:
                    check_ok(
                        f"provider smoke read: id={sample['id']!r} enabled={sample['enabled']}"
                    )
        finally:
            db.close()
    except Exception as exc:
        check_fail("Database OAuth/members probe", str(exc))
        issues += 1

    _section("Cache / events (W4b)")
    try:
        from agent.cache.factory import get_cache_backend_name
        from agent.events.factory import get_events_backend_name

        workers = int(os.environ.get("INTELLECT_WEBUI_WORKERS", 1))
        cache_backend = get_cache_backend_name(config)
        events_backend = get_events_backend_name(config)
        check_ok(f"INTELLECT_WEBUI_WORKERS={workers}")
        check_ok(f"cache.backend={cache_backend!r}, events.backend={events_backend!r}")
        if workers > 1:
            if events_backend == "redis":
                try:
                    from agent.events.redis_sync import _redis_client
                    from agent.events.redis_url import resolve_events_redis_url

                    events_cfg = config.get("events") if isinstance(config.get("events"), dict) else {}
                    redis_cfg = events_cfg.get("redis") if isinstance(events_cfg.get("redis"), dict) else {}
                    url = resolve_events_redis_url(config)
                    client = _redis_client(url)
                    try:
                        client.ping()
                        check_ok(f"Redis events ping OK (db={redis_cfg.get('db', 1)})")
                    finally:
                        client.close()
                except Exception as exc:
                    check_fail("Redis events connectivity", str(exc))
                    issues += 1
    except Exception as exc:
        check_warn("Cache/events probe skipped", str(exc))

    iso_issues: list[str] = []
    try:
        _check_session_isolation_health(iso_issues)
        issues += len(iso_issues)
    except Exception as exc:
        check_warn("Session isolation probe skipped", str(exc))

    print()
    if issues:
        print(color(f"  Storage check finished with {issues} issue(s).", Colors.YELLOW, Colors.BOLD))
        return 1
    print(color("  Storage check passed.", Colors.GREEN, Colors.BOLD))
    return 0


def run_doctor_perf() -> int:
    """Quick performance diagnostics (``intellect doctor --perf``)."""
    import subprocess
    import time

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│              ⚡ Intellect Doctor — Performance            │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))
    print()

    results = []

    # 1. Gateway import time
    t0 = time.perf_counter()
    try:
        subprocess.run(
            [sys.executable, "-c", "import gateway.run"],
            capture_output=True, timeout=15,
        )
        elapsed = time.perf_counter() - t0
        status = color("✓", Colors.GREEN) if elapsed < 2.0 else color("⚠", Colors.YELLOW)
        results.append(("Gateway import", f"{elapsed:.3f}s", status))
    except subprocess.TimeoutExpired:
        results.append(("Gateway import", "timeout (>15s)", color("✗", Colors.RED)))
    except Exception as exc:
        results.append(("Gateway import", str(exc), color("✗", Colors.RED)))

    # 2. Config load time
    t0 = time.perf_counter()
    try:
        from intellect_cli.config import load_config
        cfg = load_config()
        elapsed = time.perf_counter() - t0
        status = color("✓", Colors.GREEN) if elapsed < 0.5 else color("⚠", Colors.YELLOW)
        results.append(("Config load", f"{elapsed:.3f}s", status))
    except Exception as exc:
        results.append(("Config load", str(exc), color("✗", Colors.RED)))

    # 3. SessionDB init time
    t0 = time.perf_counter()
    try:
        from intellect_state import SessionDB
        db = SessionDB()
        elapsed = time.perf_counter() - t0
        status = color("✓", Colors.GREEN) if elapsed < 1.0 else color("⚠", Colors.YELLOW)
        results.append(("SessionDB init", f"{elapsed:.3f}s", status))
        db.close()
    except Exception as exc:
        results.append(("SessionDB init", str(exc), color("✗", Colors.RED)))

    # 4. FTS5 availability
    try:
        from intellect_state import _sqlite_supports_fts5
        fts5 = _sqlite_supports_fts5()
        results.append(("FTS5 support", "yes" if fts5 else "no",
                        color("✓", Colors.GREEN) if fts5 else color("⚠", Colors.YELLOW)))
    except Exception as exc:
        results.append(("FTS5 support", str(exc), color("✗", Colors.RED)))

    # 5. Python version + platform
    results.append(("Python", f"{sys.version.split()[0]} ({sys.platform})", color("ℹ", Colors.CYAN)))

    # Print results
    max_label = max(len(r[0]) for r in results)
    max_val = max(len(r[1]) for r in results)
    for label, val, status in results:
        print(f"  {status}  {label:<{max_label}}  {val}")

    print()
    return 0


def run_doctor(args):
    """Run diagnostic checks."""
    if getattr(args, "storage", False):
        sys.exit(run_doctor_storage())

    if getattr(args, "perf", False):
        sys.exit(run_doctor_perf())

    should_fix = getattr(args, 'fix', False)
    ack_target = getattr(args, 'ack', None)

    # Doctor runs from the interactive CLI, so CLI-gated tool availability
    # checks (like cronjob management) should see the same context as `intellect`.
    os.environ.setdefault("intellect_INTERACTIVE", "1")

    # Handle `intellect doctor --ack <id>` as a fast path. Persist the ack and
    # return without running the rest of the diagnostics — the user has
    # already seen the advisory and just wants to silence it.
    if ack_target:
        from intellect_cli.security_advisories import (
            ADVISORIES,
            ack_advisory,
        )
        valid_ids = {a.id for a in ADVISORIES}
        if ack_target not in valid_ids:
            print(color(
                f"Unknown advisory ID: {ack_target!r}. Known IDs: "
                f"{', '.join(sorted(valid_ids)) or '(none)'}",
                Colors.RED,
            ))
            sys.exit(2)
        if ack_advisory(ack_target):
            print(color(
                f"  ✓ Acknowledged advisory {ack_target}. "
                f"It will no longer trigger startup banners.",
                Colors.GREEN,
            ))
        else:
            print(color(
                f"  ✗ Failed to persist ack for {ack_target}. "
                f"Check ~/.intellect/config.yaml is writable.",
                Colors.RED,
            ))
            sys.exit(1)
        return

    issues = []
    manual_issues = []  # issues that can't be auto-fixed
    fixed_count = 0

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│                 🩺 Intellect Doctor                        │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))

    _section("Security Advisories")
    try:
        from intellect_cli.security_advisories import (
            detect_compromised,
            filter_unacked,
            full_remediation_text,
            get_acked_ids,
        )
        all_hits = detect_compromised()
        fresh_hits = filter_unacked(all_hits)
        if fresh_hits:
            for hit in fresh_hits:
                check_fail(
                    f"{hit.advisory.title}",
                    f"({hit.package}=={hit.installed_version})",
                )
                # Print the full remediation block, indented under the
                # check_fail header so it reads as a single section.
                for line in full_remediation_text(hit):
                    if line:
                        print(f"    {color(line, Colors.YELLOW)}")
                    else:
                        print()
                # Funnel into the action list so the summary block surfaces it
                # for users who scroll past the section.
                manual_issues.append(
                    f"Resolve security advisory {hit.advisory.id}: "
                    f"uninstall {hit.package}=={hit.installed_version} and "
                    f"rotate credentials, then run "
                    f"`intellect doctor --ack {hit.advisory.id}`."
                )
            # Acked-but-still-installed: show as informational so the user
            # knows the package is still on disk after the ack.
            acked_ids = get_acked_ids()
            for h in all_hits:
                if h.advisory.id in acked_ids:
                    check_warn(
                        f"{h.package}=={h.installed_version} still installed "
                        f"(advisory {h.advisory.id} acknowledged)",
                    )
        else:
            check_ok("No active security advisories")
    except Exception as e:
        # Never let a bug in the advisory check block the rest of doctor.
        check_warn(f"Security advisory check failed: {e}")
    
    _section("Python Environment")
    py_version = sys.version_info
    if py_version >= (3, 11):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    elif py_version >= (3, 10):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
        check_warn("Python 3.11+ recommended for RL Training tools (tinker requires >= 3.11)")
    elif py_version >= (3, 8):
        check_warn(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "(3.10+ recommended)")
    else:
        _fail_and_issue(
            f"Python {py_version.major}.{py_version.minor}.{py_version.micro}",
            "(3.10+ required)",
            "Upgrade Python to 3.10+",
            issues,
        )
    
    # Check if in virtual environment
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        check_ok("Virtual environment active")
    else:
        check_warn("Not in virtual environment", "(recommended)")

    # Detect drift between pyproject.toml and intellect_cli/__init__.py versions
    # (a git conflict resolution can silently revert one but not the other).
    _check_version_consistency(issues)
    
    _section("Required Packages")
    required_packages = [
        ("openai", "OpenAI SDK"),
        ("rich", "Rich (terminal UI)"),
        ("dotenv", "python-dotenv"),
        ("yaml", "PyYAML"),
        ("httpx", "HTTPX"),
    ]
    
    optional_packages = [
        ("croniter", "Croniter (cron expressions)"),
        ("telegram", "python-telegram-bot"),
        ("discord", "discord.py"),
    ]
    
    for module, name in required_packages:
        try:
            __import__(module)
            check_ok(name)
        except ImportError:
            _fail_and_issue(name, "(missing)", f"Install {name}: {_python_install_cmd()} {module}", issues)
    
    for module, name in optional_packages:
        try:
            __import__(module)
            check_ok(name, "(optional)")
        except ImportError:
            check_warn(name, "(optional, not installed)")
    
    _section("Configuration Files")
    # Check ~/.intellect/.env (primary location for user config)
    env_path = INTELLECT_HOME / '.env'
    if env_path.exists():
        check_ok(f"{_DHH}/.env file exists")
        
        # Check for common issues. Pin encoding to UTF-8 because .env files are
        # written as UTF-8 everywhere in the codebase, while Path.read_text()
        # defaults to the system locale — which crashes on non-UTF-8 Windows
        # locales (e.g. GBK) as soon as the file contains any non-ASCII byte.
        content = env_path.read_text(encoding="utf-8")
        if _has_provider_env_config(content):
            check_ok("API key or custom endpoint configured")
        else:
            check_warn(f"No API key found in {_DHH}/.env")
            issues.append("Run 'intellect setup' to configure API keys")
    else:
        # Also check project root as fallback
        fallback_env = PROJECT_ROOT / '.env'
        if fallback_env.exists():
            check_ok(".env file exists (in project directory)")
        else:
            check_fail(f"{_DHH}/.env file missing")
            if should_fix:
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.touch()
                # .env holds API keys — restrict to owner-only access from
                # creation. touch() obeys umask which is commonly 0o022,
                # leaving the file world-readable; tighten explicitly.
                try:
                    os.chmod(str(env_path), 0o600)
                except OSError:
                    pass
                check_ok(f"Created empty {_DHH}/.env")
                check_info("Run 'intellect setup' to configure API keys")
                fixed_count += 1
            else:
                check_info("Run 'intellect setup' to create one")
                issues.append("Run 'intellect setup' to create .env")
    
    # Check ~/.intellect/config.yaml (primary) or project cli-config.yaml (fallback)
    config_path = INTELLECT_HOME / 'config.yaml'
    if config_path.exists():
        check_ok(f"{_DHH}/config.yaml exists")

        # Validate model.provider and model.default values
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            model_section = cfg.get("model") or {}
            provider_raw = (model_section.get("provider") or "").strip()
            provider = provider_raw.lower()
            default_model = (model_section.get("default") or model_section.get("model") or "").strip()

            known_providers: set = set()
            try:
                from intellect_cli.auth import (
                    PROVIDER_REGISTRY,
                    resolve_provider as _resolve_auth_provider,
                )
                known_providers = set(PROVIDER_REGISTRY.keys()) | {"openrouter", "custom", "auto"}
            except Exception:
                _resolve_auth_provider = None
                pass
            try:
                from intellect_cli.config import get_compatible_custom_providers as _compatible_custom_providers
                from intellect_cli.providers import (
                    normalize_provider as _normalize_catalog_provider,
                    resolve_provider_full as _resolve_provider_full,
                )
            except Exception:
                _compatible_custom_providers = None
                _normalize_catalog_provider = None
                _resolve_provider_full = None

            custom_providers = []
            if _compatible_custom_providers is not None:
                try:
                    custom_providers = _compatible_custom_providers(cfg)
                except Exception:
                    custom_providers = []

            user_providers = cfg.get("providers")
            if isinstance(user_providers, dict):
                known_providers.update(str(name).strip().lower() for name in user_providers if str(name).strip())
            for entry in custom_providers:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if name:
                    known_providers.add("custom:" + name.lower().replace(" ", "-"))

            valid_provider_ids = set(known_providers)
            provider_ids_to_accept = {provider} if provider else set()
            if _normalize_catalog_provider is not None:
                for known_provider in known_providers:
                    try:
                        valid_provider_ids.add(_normalize_catalog_provider(known_provider))
                    except Exception:
                        continue

            runtime_provider = provider
            if (
                provider
                and _resolve_auth_provider is not None
                and provider not in {"auto", "custom"}
            ):
                try:
                    runtime_provider = _resolve_auth_provider(provider)
                    provider_ids_to_accept.add(runtime_provider)
                except Exception:
                    runtime_provider = provider

            catalog_provider = provider
            if (
                provider
                and _resolve_provider_full is not None
                and provider not in {"auto", "custom"}
            ):
                provider_def = _resolve_provider_full(provider, user_providers, custom_providers)
                catalog_provider = provider_def.id if provider_def is not None else None
                if catalog_provider is not None:
                    provider_ids_to_accept.add(catalog_provider)

            if provider and provider != "auto":
                if catalog_provider is None or (
                    known_providers
                    and not (provider_ids_to_accept & valid_provider_ids)
                ):
                    known_list = ", ".join(sorted(known_providers)) if known_providers else "(unavailable)"
                    _fail_and_issue(
                        f"model.provider '{provider_raw}' is not a recognised provider",
                        f"(known: {known_list})",
                        (
                            f"model.provider '{provider_raw}' is unknown. "
                            f"Valid providers: {known_list}. "
                            f"Fix: run 'intellect config set model.provider <valid_provider>'"
                        ),
                        issues,
                    )

            # Warn if model is set to a provider-prefixed name on a provider that doesn't use them
            provider_for_policy = runtime_provider or catalog_provider
            providers_accepting_vendor_slugs = {
                "openrouter",
                "custom",
                "auto",
                "kilocode",
                "opencode-zen",
                "huggingface",
                "lmstudio",
                "ontoweb",
            }
            if (
                default_model
                and "/" in default_model
                and provider_for_policy
                and provider_for_policy not in providers_accepting_vendor_slugs
            ):
                check_warn(
                    f"model.default '{default_model}' uses a vendor/model slug but provider is '{provider_raw}'",
                    "(vendor-prefixed slugs belong to aggregators like openrouter)",
                )
                issues.append(
                    f"model.default '{default_model}' is vendor-prefixed but model.provider is '{provider_raw}'. "
                    "Either set model.provider to 'openrouter', or drop the vendor prefix."
                )

            # Check credentials for the configured provider.
            # Limit to API-key providers in PROVIDER_REGISTRY — other provider
            # types (OAuth, SDK, anthropic/custom/auto) have their own env-var
            # checks elsewhere in doctor, and get_auth_status() returns a bare
            # {logged_in: False} for anything it doesn't explicitly dispatch,
            # which would produce false positives.
            if runtime_provider and runtime_provider not in ("auto", "custom"):
                try:
                    if runtime_provider == "openrouter":
                        from intellect_cli.config import get_env_value

                        configured = bool(
                            str(get_env_value("OPENROUTER_API_KEY") or "").strip()
                            or str(get_env_value("OPENAI_API_KEY") or "").strip()
                        )
                    else:
                        from intellect_cli.auth import PROVIDER_REGISTRY, get_auth_status

                        pconfig = PROVIDER_REGISTRY.get(runtime_provider)
                        configured = True
                        if pconfig and getattr(pconfig, "auth_type", "") == "api_key":
                            status = get_auth_status(runtime_provider) or {}
                            configured = bool(
                                status.get("configured")
                                or status.get("logged_in")
                                or status.get("api_key")
                            )
                    if not configured:
                        _fail_and_issue(
                            f"model.provider '{runtime_provider}' is set but no API key is configured",
                            "(check ~/.intellect/.env or run 'intellect setup')",
                            (
                                f"No credentials found for provider '{runtime_provider}'. "
                                f"Run 'intellect setup' or set the provider's API key in {_DHH}/.env, "
                                f"or switch providers with 'intellect config set model.provider <name>'"
                            ),
                            issues,
                        )
                except Exception:
                    pass

        except Exception as e:
            check_warn("Could not validate model/provider config", f"({e})")
    else:
        fallback_config = PROJECT_ROOT / 'cli-config.yaml'
        if fallback_config.exists():
            check_ok("cli-config.yaml exists (in project directory)")
        else:
            if should_fix:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                example_config = PROJECT_ROOT / 'cli-config.yaml.example'
                if example_config.exists():
                    shutil.copy2(str(example_config), str(config_path))
                    check_ok(f"Created {_DHH}/config.yaml from cli-config.yaml.example")
                else:
                    from intellect_cli.config import DEFAULT_CONFIG, save_config
                    save_config(DEFAULT_CONFIG)
                    check_ok(f"Created {_DHH}/config.yaml from defaults")
                fixed_count += 1
            else:
                check_warn("config.yaml not found", "(using defaults)")

    # Check config version and stale keys
    config_path = INTELLECT_HOME / 'config.yaml'
    if config_path.exists():
        try:
            from intellect_cli.config import check_config_version, migrate_config
            current_ver, latest_ver = check_config_version()
            if current_ver < latest_ver:
                check_warn(
                    f"Config version outdated (v{current_ver} → v{latest_ver})",
                    "(new settings available)"
                )
                if should_fix:
                    try:
                        migrate_config(interactive=False, quiet=False)
                        check_ok("Config migrated to latest version")
                        fixed_count += 1
                    except Exception as mig_err:
                        check_warn(f"Auto-migration failed: {mig_err}")
                        issues.append("Run 'intellect setup' to migrate config")
                else:
                    issues.append("Run 'intellect doctor --fix' or 'intellect setup' to migrate config")
            else:
                check_ok(f"Config version up to date (v{current_ver})")
        except Exception:
            pass

        # Detect stale root-level model keys (known bug source — PR #4329)
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                raw_config = yaml.safe_load(f) or {}
            stale_root_keys = [k for k in ("provider", "base_url") if k in raw_config and isinstance(raw_config[k], str)]
            if stale_root_keys:
                check_warn(
                    f"Stale root-level config keys: {', '.join(stale_root_keys)}",
                    "(should be under 'model:' section)"
                )
                if should_fix:
                    # Coerce scalar/None ``model:`` into a dict before mutation —
                    # ``setdefault("model", {})`` would return an existing scalar
                    # and then ``model_section[k] = ...`` would raise TypeError.
                    raw_model = raw_config.get("model")
                    if isinstance(raw_model, dict):
                        model_section = raw_model
                    elif isinstance(raw_model, str) and raw_model.strip():
                        model_section = {"default": raw_model.strip()}
                        raw_config["model"] = model_section
                    else:
                        model_section = {}
                        raw_config["model"] = model_section
                    for k in stale_root_keys:
                        if not model_section.get(k):
                            model_section[k] = raw_config.pop(k)
                        else:
                            raw_config.pop(k)
                    from utils import atomic_yaml_write
                    atomic_yaml_write(config_path, raw_config)
                    check_ok("Migrated stale root-level keys into model section")
                    fixed_count += 1
                else:
                    issues.append("Stale root-level provider/base_url in config.yaml — run 'intellect doctor --fix'")
        except Exception:
            pass

        # Validate config structure (catches malformed custom_providers, etc.)
        try:
            from intellect_cli.config import validate_config_structure
            config_issues = validate_config_structure()
            if config_issues:
                _section("Config Structure")
                for ci in config_issues:
                    if ci.severity == "error":
                        check_fail(ci.message)
                    else:
                        check_warn(ci.message)
                    # Show the hint indented
                    for hint_line in ci.hint.splitlines():
                        check_info(hint_line)
                    issues.append(ci.message)
        except Exception:
            pass

    _section("xAI Model Retirement (May 15, 2026)")

    try:
        from intellect_cli.config import load_config
        from intellect_cli.xai_retirement import (
            MIGRATION_GUIDE_URL,
            find_retired_xai_refs,
            format_issue,
        )

        _xai_cfg = load_config()
        retired_refs = find_retired_xai_refs(_xai_cfg)
        if not retired_refs:
            check_ok("No retired xAI models in config")
        else:
            for ref in retired_refs:
                check_warn(format_issue(ref))
            check_info(f"Migration guide: {MIGRATION_GUIDE_URL}")
            manual_issues.append(
                f"Update {len(retired_refs)} retired xAI model reference(s) "
                f"in config.yaml — see {MIGRATION_GUIDE_URL}"
            )
    except Exception as _xai_check_err:
        check_warn("xAI retirement check skipped", f"({_xai_check_err})")

    _section("Auth Providers")

    try:
        from intellect_cli.auth import (
            get_ontoweb_auth_status,
            get_codex_auth_status,
            get_gemini_oauth_auth_status,
            get_minimax_oauth_auth_status,
        )

        nous_status = get_ontoweb_auth_status()
        if nous_status.get("logged_in"):
            check_ok("ONTOWEB Portal auth", "(logged in)")
        else:
            check_warn("ONTOWEB Portal auth", "(not logged in)")

        codex_status = get_codex_auth_status()
        if codex_status.get("logged_in"):
            check_ok("OpenAI Codex auth", "(logged in)")
        else:
            check_warn("OpenAI Codex auth", "(not logged in)")
            if codex_status.get("error"):
                check_info(codex_status["error"])
            # Native OAuth uses Intellect' own device-code flow — the Codex CLI is
            # only needed to import existing tokens from ~/.codex/auth.json.
            # Attach the hint to the Codex auth row so it doesn't read as
            # remediation for whichever provider happens to print next (#27975).
            if not _safe_which("codex"):
                check_info(
                    "codex CLI not installed "
                    "(optional — only required to import tokens "
                    "from an existing Codex CLI login)"
                )

        gemini_status = get_gemini_oauth_auth_status()
        if gemini_status.get("logged_in"):
            email = gemini_status.get("email") or ""
            project = gemini_status.get("project_id") or ""
            pieces = []
            if email:
                pieces.append(email)
            if project:
                pieces.append(f"project={project}")
            suffix = f" ({', '.join(pieces)})" if pieces else ""
            check_ok("Google Gemini OAuth", f"(logged in{suffix})")
        else:
            check_warn("Google Gemini OAuth", "(not logged in)")

        minimax_status = get_minimax_oauth_auth_status()
        if minimax_status.get("logged_in"):
            region = minimax_status.get("region", "global")
            check_ok("MiniMax OAuth", f"(logged in, region={region})")
        else:
            check_warn("MiniMax OAuth", "(not logged in)")
    except Exception as e:
        check_warn("Auth provider status", f"(could not check: {e})")

    # xAI OAuth — separate try/except so an import failure here cannot
    # disrupt the already-printed OntoWeb/Codex/Gemini/MiniMax rows above.
    try:
        from intellect_cli.auth import get_xai_oauth_auth_status
        xai_oauth_status = get_xai_oauth_auth_status() or {}
        if xai_oauth_status.get("logged_in"):
            check_ok("xAI OAuth", "(logged in)")
        else:
            check_warn("xAI OAuth", "(not logged in)")
            if xai_oauth_status.get("error"):
                check_info(xai_oauth_status["error"])
    except Exception:
        pass

    try:
        from agent.oauth.auth_json_drift import check_auth_json_oauth_drift

        drift_issues: list[str] = []
        check_auth_json_oauth_drift(drift_issues)
        for msg in drift_issues:
            check_warn(msg)
    except Exception:
        pass

    _section("Directory Structure")
    intellect_home = INTELLECT_HOME
    if intellect_home.exists():
        check_ok(f"{_DHH} directory exists")
    elif should_fix:
        intellect_home.mkdir(parents=True, exist_ok=True)
        check_ok(f"Created {_DHH} directory")
        fixed_count += 1
    else:
        check_warn(f"{_DHH} not found", "(will be created on first use)")
    
    # Check expected subdirectories
    expected_subdirs = ["cron", "sessions", "logs", "skills", "memories"]
    for subdir_name in expected_subdirs:
        subdir_path = intellect_home / subdir_name
        if subdir_path.exists():
            check_ok(f"{_DHH}/{subdir_name}/ exists")
        elif should_fix:
            subdir_path.mkdir(parents=True, exist_ok=True)
            check_ok(f"Created {_DHH}/{subdir_name}/")
            fixed_count += 1
        else:
            check_warn(f"{_DHH}/{subdir_name}/ not found", "(will be created on first use)")
    
    # Check for SOUL.md persona file
    soul_path = intellect_home / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8").strip()
        # Check if it's just the template comments (no real content)
        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("<!--", "-->", "#"))]
        if lines:
            check_ok(f"{_DHH}/SOUL.md exists (persona configured)")
        else:
            check_info(f"{_DHH}/SOUL.md exists but is empty — edit it to customize personality")
    else:
        check_warn(f"{_DHH}/SOUL.md not found", "(create it to give Intellect a custom personality)")
        if should_fix:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(
                "# Intellect Agent Persona\n\n"
                "<!-- Edit this file to customize how Intellect communicates. -->\n\n"
                "You are Intellect, a helpful AI assistant.\n",
                encoding="utf-8",
            )
            check_ok(f"Created {_DHH}/SOUL.md with basic template")
            fixed_count += 1
    
    # Check memory directory
    memories_dir = intellect_home / "memories"
    if memories_dir.exists():
        check_ok(f"{_DHH}/memories/ directory exists")
        memory_file = memories_dir / "MEMORY.md"
        user_file = memories_dir / "USER.md"
        if memory_file.exists():
            size = len(memory_file.read_text(encoding="utf-8").strip())
            check_ok(f"MEMORY.md exists ({size} chars)")
        else:
            check_info("MEMORY.md not created yet (will be created when the agent first writes a memory)")
        if user_file.exists():
            size = len(user_file.read_text(encoding="utf-8").strip())
            check_ok(f"USER.md exists ({size} chars)")
        else:
            check_info("USER.md not created yet (will be created when the agent first writes a memory)")
    else:
        check_warn(f"{_DHH}/memories/ not found", "(will be created on first use)")
        if should_fix:
            memories_dir.mkdir(parents=True, exist_ok=True)
            check_ok(f"Created {_DHH}/memories/")
            fixed_count += 1
    
    fixed_count += _check_session_store(
        intellect_home, issues=issues, should_fix=should_fix
    )

    _check_gateway_service_linger(issues)
    _check_s6_supervision(issues)

    if sys.platform != "win32":
        _section("Command Installation")
        # Determine the venv entry point location
        _venv_bin = None
        for _venv_name in ("venv", ".venv"):
            _candidate = PROJECT_ROOT / _venv_name / "bin" / "intellect"
            if _candidate.exists():
                _venv_bin = _candidate
                break

        # Determine the expected command link directory (mirrors install.sh logic)
        _prefix = os.environ.get("PREFIX", "")
        _is_termux_env = bool(os.environ.get("TERMUX_VERSION")) or "com.termux/files/usr" in _prefix
        if _is_termux_env and _prefix:
            _cmd_link_dir = Path(_prefix) / "bin"
            _cmd_link_display = "$PREFIX/bin"
        else:
            _cmd_link_dir = Path.home() / ".local" / "bin"
            _cmd_link_display = "~/.local/bin"
        _cmd_link = _cmd_link_dir / "intellect"

        if _venv_bin is None:
            check_warn(
                "Venv entry point not found",
                "(intellect not in venv/bin/ or .venv/bin/ — reinstall with pip install -e '.[all]')"
            )
            manual_issues.append(
                f"Reinstall entry point: cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e '.[all]'"
            )
        else:
            check_ok(f"Venv entry point exists ({_venv_bin.relative_to(PROJECT_ROOT)})")

            # Check the symlink at the command link location
            if _cmd_link.is_symlink():
                _target = _cmd_link.resolve()
                _expected = _venv_bin.resolve()
                if _target == _expected:
                    check_ok(f"{_cmd_link_display}/intellect → correct target")
                else:
                    check_warn(
                        f"{_cmd_link_display}/intellect points to wrong target",
                        f"(→ {_target}, expected → {_expected})"
                    )
                    if should_fix:
                        _cmd_link.unlink()
                        _cmd_link.symlink_to(_venv_bin)
                        check_ok(f"Fixed symlink: {_cmd_link_display}/intellect → {_venv_bin}")
                        fixed_count += 1
                    else:
                        issues.append(f"Broken symlink at {_cmd_link_display}/intellect — run 'intellect doctor --fix'")
            elif _cmd_link.exists():
                # It's a regular file, not a symlink — possibly a wrapper script
                check_ok(f"{_cmd_link_display}/intellect exists (non-symlink)")
            else:
                check_fail(
                    f"{_cmd_link_display}/intellect not found",
                    "(intellect command may not work outside the venv)"
                )
                if should_fix:
                    _cmd_link_dir.mkdir(parents=True, exist_ok=True)
                    _cmd_link.symlink_to(_venv_bin)
                    check_ok(f"Created symlink: {_cmd_link_display}/intellect → {_venv_bin}")
                    fixed_count += 1

                    # Check if the link dir is on PATH
                    _path_dirs = os.environ.get("PATH", "").split(os.pathsep)
                    if str(_cmd_link_dir) not in _path_dirs:
                        check_warn(
                            f"{_cmd_link_display} is not on your PATH",
                            "(add it to your shell config: export PATH=\"$HOME/.local/bin:$PATH\")"
                        )
                        manual_issues.append(f"Add {_cmd_link_display} to your PATH")
                else:
                    issues.append(f"Missing {_cmd_link_display}/intellect symlink — run 'intellect doctor --fix'")

    _section("External Tools")
    # Git
    if _safe_which("git"):
        check_ok("git")
    else:
        check_warn("git not found", "(optional)")
    
    # ripgrep (optional, for faster file search)
    if _safe_which("rg"):
        check_ok("ripgrep (rg)", "(faster file search)")
    else:
        check_warn("ripgrep (rg) not found", "(file search uses grep fallback)")
        check_info(f"Install for faster search: {_system_package_install_cmd('ripgrep')}")
    
    # Docker (optional)
    terminal_env = os.getenv("TERMINAL_ENV", "local")
    try:
        from intellect_constants import is_container as _is_container
        running_in_container = _is_container()
    except Exception:
        running_in_container = False

    if running_in_container:
        # Inside our container the Docker terminal backend is not
        # configured by default (Docker-in-Docker isn't set up); the
        # local backend is the intended one. Skip the noisy "docker
        # not found" warning. If the user has explicitly chosen
        # TERMINAL_ENV=docker inside the container they likely mounted
        # /var/run/docker.sock, so fall through to the normal check.
        if terminal_env != "docker":
            check_info(
                "Running inside a container — using local terminal backend "
                "(docker-in-docker is not configured by default)"
            )
            # Skip to next section; Docker isn't relevant here.
            terminal_env = "local"
    if terminal_env == "docker":
        if _safe_which("docker"):
            # Check if docker daemon is running
            try:
                result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok("docker", "(daemon running)")
            else:
                _fail_and_issue("docker daemon not running", "", "Start Docker daemon", issues)
        else:
            _fail_and_issue(
                "docker not found",
                "(required for TERMINAL_ENV=docker)",
                "Install Docker or change TERMINAL_ENV",
                issues,
            )
    elif _safe_which("docker"):
        check_ok("docker", "(optional)")
    elif _is_termux():
        check_info("Docker backend is not available inside Termux (expected on Android)")
    elif running_in_container:
        pass  # already explained above
    else:
        check_warn("docker not found", "(optional)")
    
    # SSH (if using ssh backend)
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST")
        if ssh_host:
            ssh_user = os.getenv("TERMINAL_SSH_USER")
            ssh_port = os.getenv("TERMINAL_SSH_PORT")
            ssh_key = os.getenv("TERMINAL_SSH_KEY")
            target = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
            cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
            if ssh_port:
                cmd += ["-p", ssh_port]
            if ssh_key:
                cmd += ["-i", os.path.expanduser(ssh_key)]
            cmd += [target, "echo ok"]
            # Try to connect
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok(f"SSH connection to {ssh_host}")
            else:
                _fail_and_issue(f"SSH connection to {ssh_host}", "", f"Check SSH configuration for {ssh_host}", issues)
        else:
            _fail_and_issue(
                "TERMINAL_SSH_HOST not set",
                "(required for TERMINAL_ENV=ssh)",
                "Set TERMINAL_SSH_HOST in .env",
                issues,
            )
    
    # Daytona (if using daytona backend)
    if terminal_env == "daytona":
        daytona_key = os.getenv("DAYTONA_API_KEY")
        if daytona_key:
            check_ok("Daytona API key", "(configured)")
        else:
            _fail_and_issue(
                "DAYTONA_API_KEY not set",
                "(required for TERMINAL_ENV=daytona)",
                "Set DAYTONA_API_KEY environment variable",
                issues,
            )
        try:
            from daytona import Daytona  # noqa: F401 — SDK presence check
            check_ok("daytona SDK", "(installed)")
        except ImportError:
            _fail_and_issue(
                "daytona SDK not installed",
                "(pip install daytona)",
                "Install daytona SDK: pip install daytona",
                issues,
            )

    # Node.js + agent-browser (for browser automation tools)
    if _safe_which("node"):
        check_ok("Node.js")
        # Check if agent-browser is installed
        agent_browser_path = PROJECT_ROOT / "node_modules" / "agent-browser"
        agent_browser_ok = False
        if agent_browser_path.exists():
            check_ok("agent-browser (Node.js)", "(browser automation)")
            agent_browser_ok = True
        elif shutil.which("agent-browser"):
            check_ok("agent-browser", "(browser automation)")
            agent_browser_ok = True
        elif _is_termux():
            check_info("agent-browser is not installed (expected in the tested Termux path)")
            check_info("Install it manually later with: npm install -g agent-browser && agent-browser install")
            check_info("Termux browser setup:")
            for step in _termux_browser_setup_steps(node_installed=True):
                check_info(step)
        else:
            check_warn("agent-browser not installed", "(run: npm install)")

        # Chromium presence — the browser tools silently fail to register when
        # agent-browser is found but no Playwright-managed Chromium is on disk
        # (tools/browser_tool.py::check_browser_requirements filters them out
        # before the agent ever sees them).  Reuse the exact predicate it uses
        # so the two checks cannot diverge.  Skip on Termux (not a tested
        # path).
        if agent_browser_ok and not _is_termux():
            try:
                # Lazy import: browser_tool is a ~150KB module we don't want
                # to eagerly load in every `intellect doctor` invocation.
                from tools.browser_tool import (
                    _chromium_installed,
                    _is_camofox_mode,
                    _get_cloud_provider,
                    _get_cdp_override,
                    _using_lightpanda_engine,
                )
            except Exception:
                # If browser_tool can't even import, that's a separate bug
                # surfaced elsewhere; don't crash doctor.
                pass
            else:
                # Only warn about Chromium if the installed engine actually
                # requires it: Camofox, CDP override, a cloud provider, or
                # Lightpanda all bypass the local Chromium requirement.
                skip_chromium_check = (
                    _is_camofox_mode()
                    or bool(_get_cdp_override())
                    or _get_cloud_provider() is not None
                    or _using_lightpanda_engine()
                )
                if not skip_chromium_check:
                    if _chromium_installed():
                        check_ok("Playwright Chromium", "(browser engine)")
                    else:
                        check_warn(
                            "Playwright Chromium not installed",
                            "(browser_* tools will be hidden from the agent)",
                        )
                        if sys.platform == "win32":
                            check_info(
                                f"Install with: cd {PROJECT_ROOT} && "
                                "npx playwright install chromium"
                            )
                        else:
                            check_info(
                                f"Install with: cd {PROJECT_ROOT} && "
                                "npx playwright install --with-deps chromium"
                            )
    elif _is_termux():
        check_info("Node.js not found (browser tools are optional in the tested Termux path)")
        check_info("Install Node.js on Termux with: pkg install nodejs")
        check_info("Termux browser setup:")
        for step in _termux_browser_setup_steps(node_installed=False):
            check_info(step)
    else:
        check_warn("Node.js not found", "(optional, needed for browser tools)")
    
    # npm audit for all Node.js packages
    _npm_bin = _safe_which("npm")
    if _npm_bin:
        npm_dirs = [
            (PROJECT_ROOT, "Browser tools (agent-browser)"),
            (PROJECT_ROOT / "scripts" / "whatsapp-bridge", "WhatsApp bridge"),
        ]
        for npm_dir, label in npm_dirs:
            if not (npm_dir / "node_modules").exists():
                continue
            try:
                # Use resolved absolute path so Windows can execute
                # npm.cmd (CreateProcessW can't run bare .cmd names).
                audit_result = subprocess.run(
                    [_npm_bin, "audit", "--json"],
                    cwd=str(npm_dir),
                    capture_output=True, text=True, timeout=30,
                )
                import json as _json
                audit_data = _json.loads(audit_result.stdout) if audit_result.stdout.strip() else {}
                vuln_count = audit_data.get("metadata", {}).get("vulnerabilities", {})
                critical = vuln_count.get("critical", 0)
                high = vuln_count.get("high", 0)
                moderate = vuln_count.get("moderate", 0)
                total = critical + high + moderate
                if total == 0:
                    check_ok(f"{label} deps", "(no known vulnerabilities)")
                elif critical > 0 or high > 0:
                    check_warn(
                        f"{label} deps",
                        f"({critical} critical, {high} high, {moderate} moderate — run: cd {npm_dir} && npm audit fix)"
                    )
                    issues.append(
                        f"{label} has {total} npm "
                        f"{'vulnerability' if total == 1 else 'vulnerabilities'}"
                    )
                else:
                    check_ok(
                        f"{label} deps",
                        f"({moderate} moderate "
                        f"{'vulnerability' if moderate == 1 else 'vulnerabilities'})",
                    )
            except Exception:
                pass

    if _is_termux():
        check_info("Termux compatibility fallbacks:")
        for note in _termux_install_all_fallback_notes():
            check_info(note)

    _section("API Connectivity")
    # Refactor: every connectivity probe below is HTTP-bound and fully
    # independent. Running them in series spent ~5s wall on a typical
    # workstation (2s of that was boto3's IMDS lookup for AWS credentials,
    # which times out unless you're actually on EC2). Threading them with
    # a small executor pool collapses the section to roughly the slowest
    # single probe — about 2s — without changing the output format.
    #
    # Each ``_probe_*`` helper is a pure function: takes its inputs,
    # makes one HTTP/SDK call, returns a ``_ConnectivityResult`` carrying
    # the line(s) to print and any issue strings to append. No globals,
    # no shared mutable state, no printing inside the workers.
    import concurrent.futures as _futures
    from collections import namedtuple as _namedtuple

    _ConnectivityResult = _namedtuple(
        "_ConnectivityResult", ["label", "lines", "issues"]
    )
    _probes: list = []  # list of (label, callable) submitted in display order

    def _probe_openrouter() -> _ConnectivityResult:
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            return _ConnectivityResult(
                "OpenRouter API",
                [(color("⚠", Colors.YELLOW), "OpenRouter API",
                  color("(not configured)", Colors.DIM))],
                [],
            )
        try:
            import httpx
            r = httpx.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if r.status_code == 200:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✓", Colors.GREEN), "OpenRouter API", "")],
                    [],
                )
            if r.status_code == 401:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✗", Colors.RED), "OpenRouter API",
                      color("(invalid API key)", Colors.DIM))],
                    ["Check OPENROUTER_API_KEY in .env"],
                )
            if r.status_code == 402:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✗", Colors.RED), "OpenRouter API",
                      color("(out of credits — payment required)", Colors.DIM))],
                    ["OpenRouter account has insufficient credits. "
                     "Fix: run 'intellect config set model.provider <provider>' "
                     "to switch providers, or fund your OpenRouter account "
                     "at https://openrouter.ai/settings/credits"],
                )
            if r.status_code == 429:
                return _ConnectivityResult(
                    "OpenRouter API",
                    [(color("✗", Colors.RED), "OpenRouter API",
                      color("(rate limited)", Colors.DIM))],
                    ["OpenRouter rate limit hit — consider switching to "
                     "a different provider or waiting"],
                )
            return _ConnectivityResult(
                "OpenRouter API",
                [(color("✗", Colors.RED), "OpenRouter API",
                  color(f"(HTTP {r.status_code})", Colors.DIM))],
                [],
            )
        except Exception as e:
            return _ConnectivityResult(
                "OpenRouter API",
                [(color("✗", Colors.RED), "OpenRouter API",
                  color(f"({e})", Colors.DIM))],
                ["Check network connectivity"],
            )

    def _probe_anthropic() -> _ConnectivityResult:
        from intellect_cli.auth import get_anthropic_key
        key = get_anthropic_key()
        if not key:
            return _ConnectivityResult("Anthropic API", [], [])
        try:
            import httpx
            from agent.anthropic_adapter import (
                _is_oauth_token,
                _COMMON_BETAS,
                _OAUTH_ONLY_BETAS,
                _CONTEXT_1M_BETA,
            )
            headers = {"anthropic-version": "2023-06-01"}
            is_oauth = _is_oauth_token(key)
            if is_oauth:
                headers["Authorization"] = f"Bearer {key}"
                headers["anthropic-beta"] = ",".join(_COMMON_BETAS + _OAUTH_ONLY_BETAS)
            else:
                headers["x-api-key"] = key
            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers=headers, timeout=10,
            )
            # Reactive recovery: OAuth subscriptions without 1M context reject the
            # request with 400 "long context beta is not yet available for this
            # subscription". Retry once with that beta stripped so the doctor
            # check doesn't falsely report Anthropic as unreachable.
            if (
                is_oauth
                and r.status_code == 400
                and "long context beta" in r.text.lower()
                and "not yet available" in r.text.lower()
            ):
                headers["anthropic-beta"] = ",".join(
                    [b for b in _COMMON_BETAS if b != _CONTEXT_1M_BETA]
                    + list(_OAUTH_ONLY_BETAS)
                )
                r = httpx.get(
                    "https://api.anthropic.com/v1/models",
                    headers=headers, timeout=10,
                )
            if r.status_code == 200:
                return _ConnectivityResult(
                    "Anthropic API",
                    [(color("✓", Colors.GREEN), "Anthropic API", "")],
                    [],
                )
            if r.status_code == 401:
                return _ConnectivityResult(
                    "Anthropic API",
                    [(color("✗", Colors.RED), "Anthropic API",
                      color("(invalid API key)", Colors.DIM))],
                    [],
                )
            return _ConnectivityResult(
                "Anthropic API",
                [(color("⚠", Colors.YELLOW), "Anthropic API",
                  color("(couldn't verify)", Colors.DIM))],
                [],
            )
        except Exception as e:
            return _ConnectivityResult(
                "Anthropic API",
                [(color("⚠", Colors.YELLOW), "Anthropic API",
                  color(f"({e})", Colors.DIM))],
                [],
            )

    def _probe_apikey_provider(pname, env_vars, default_url, base_env,
                               supports_health_check) -> _ConnectivityResult:
        key = ""
        for ev in env_vars:
            key = os.getenv(ev, "")
            if key:
                break
        if not key:
            return _ConnectivityResult(pname, [], [])
        label = pname.ljust(20)
        if not supports_health_check:
            return _ConnectivityResult(
                pname,
                [(color("✓", Colors.GREEN), label,
                  color("(key configured)", Colors.DIM))],
                [],
            )
        try:
            import httpx
            base = os.getenv(base_env, "") if base_env else ""
            # Auto-detect Kimi Code keys (sk-kimi-) → api.kimi.com/coding/v1
            # (OpenAI-compat surface, which exposes /models for health check).
            if not base and key.startswith("sk-kimi-"):
                base = "https://api.kimi.com/coding/v1"
            # Anthropic-compat endpoints (/anthropic, api.kimi.com/coding
            # with no /v1) don't support /models. Rewrite to OpenAI-compat
            # /v1 surface for health checks.
            if base and base.rstrip("/").endswith("/anthropic"):
                from agent.auxiliary_client import _to_openai_base_url
                base = _to_openai_base_url(base)
            if base_url_host_matches(base, "api.kimi.com") and base.rstrip("/").endswith("/coding"):
                base = base.rstrip("/") + "/v1"
            url = (base.rstrip("/") + "/models") if base else default_url
            headers = {
                "Authorization": f"Bearer {key}",
                "User-Agent": _INTELLECT_USER_AGENT,
            }
            if base_url_host_matches(base, "api.kimi.com"):
                headers["User-Agent"] = "claude-code/0.1.0"
            # Google's Generative Language API (generativelanguage.googleapis.com)
            # rejects ``Authorization: Bearer <api-key>`` with 401
            # ``ACCESS_TOKEN_TYPE_UNSUPPORTED`` — that header is reserved for
            # OAuth 2 access tokens, not plain API keys. Plain keys use
            # ``x-goog-api-key`` (or ``?key=``). Without this, a perfectly valid
            # GOOGLE_API_KEY/GEMINI_API_KEY always shows red in ``intellect doctor``.
            if url and base_url_host_matches(url, "generativelanguage.googleapis.com"):
                headers.pop("Authorization", None)
                headers["x-goog-api-key"] = key
            r = httpx.get(url, headers=headers, timeout=10)
            if (
                pname == "Alibaba/DashScope"
                and not base
                and r.status_code == 401
            ):
                r = httpx.get(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
                    headers=headers, timeout=10,
                )
            if r.status_code == 200:
                return _ConnectivityResult(
                    pname,
                    [(color("✓", Colors.GREEN), label, "")],
                    [],
                )
            if r.status_code == 401:
                return _ConnectivityResult(
                    pname,
                    [(color("✗", Colors.RED), label,
                      color("(invalid API key)", Colors.DIM))],
                    [f"Check {env_vars[0]} in .env"],
                )
            return _ConnectivityResult(
                pname,
                [(color("⚠", Colors.YELLOW), label,
                  color(f"(HTTP {r.status_code})", Colors.DIM))],
                [],
            )
        except Exception as e:
            return _ConnectivityResult(
                pname,
                [(color("⚠", Colors.YELLOW), label,
                  color(f"({e})", Colors.DIM))],
                [],
            )

    def _probe_bedrock() -> _ConnectivityResult:
        try:
            from agent.bedrock_adapter import (
                has_aws_credentials,
                resolve_aws_auth_env_var,
                resolve_bedrock_region,
            )
        except ImportError:
            return _ConnectivityResult("AWS Bedrock", [], [])
        if not has_aws_credentials():
            return _ConnectivityResult("AWS Bedrock", [], [])
        auth_var = resolve_aws_auth_env_var()
        region = resolve_bedrock_region()
        label = "AWS Bedrock".ljust(20)
        try:
            import boto3
            from botocore.config import Config as _BotoConfig
            # Trim retries on the actual Bedrock API call so a transient
            # failure doesn't pad the doctor run by 30+ seconds.
            cfg = _BotoConfig(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 1},
            )
            client = boto3.client("bedrock", region_name=region, config=cfg)
            resp = client.list_foundation_models()
            n = len(resp.get("modelSummaries", []))
            return _ConnectivityResult(
                "AWS Bedrock",
                [(color("✓", Colors.GREEN), label,
                  color(f"({auth_var}, {region}, {n} models)", Colors.DIM))],
                [],
            )
        except ImportError:
            return _ConnectivityResult(
                "AWS Bedrock",
                [(color("⚠", Colors.YELLOW), label,
                  color(f"(boto3 not installed — {sys.executable} -m pip install boto3)",
                        Colors.DIM))],
                [f"Install boto3 for Bedrock: {sys.executable} -m pip install boto3"],
            )
        except Exception as e:
            err_name = type(e).__name__
            return _ConnectivityResult(
                "AWS Bedrock",
                [(color("⚠", Colors.YELLOW), label,
                  color(f"({err_name}: {e})", Colors.DIM))],
                [f"AWS Bedrock: {err_name} — check IAM permissions for "
                 f"bedrock:ListFoundationModels"],
            )

    def _probe_azure_entra() -> _ConnectivityResult:
        """Probe Azure Foundry Entra ID auth, parallel to ``_probe_bedrock``.

        Skipped unless the active config has ``model.provider:
        azure-foundry`` AND ``model.auth_mode: entra_id`` — we don't probe
        the token-service / CLI chain for users on plain API-key Azure.

        Bounded by a 10s timeout (via
        :func:`agent.azure_identity_adapter.describe_active_credential`)
        so a slow token service can't pad the doctor run.
        """
        label = "Azure Foundry (Entra ID)".ljust(28)
        try:
            from intellect_cli.config import load_config
            cfg = load_config()
            model_cfg = cfg.get("model") if isinstance(cfg, dict) else {}
            if not isinstance(model_cfg, dict):
                return _ConnectivityResult("Azure Foundry (Entra ID)", [], [])
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
            auth_mode = str(model_cfg.get("auth_mode") or "").strip().lower()
            if cfg_provider != "azure-foundry" or auth_mode != "entra_id":
                return _ConnectivityResult("Azure Foundry (Entra ID)", [], [])
        except Exception:
            return _ConnectivityResult("Azure Foundry (Entra ID)", [], [])

        try:
            from agent.azure_identity_adapter import (
                EntraIdentityConfig,
                SCOPE_AI_AZURE_DEFAULT,
                describe_active_credential,
                has_azure_identity_installed,
            )
        except Exception as exc:
            return _ConnectivityResult(
                "Azure Foundry (Entra ID)",
                [(color("⚠", Colors.YELLOW), label,
                  color(f"(adapter import failed: {exc})", Colors.DIM))],
                [f"Azure Foundry adapter import failed: {exc}"],
            )

        if not has_azure_identity_installed():
            return _ConnectivityResult(
                "Azure Foundry (Entra ID)",
                [(color("⚠", Colors.YELLOW), label,
                  color("(azure-identity not installed)", Colors.DIM))],
                [f"Install azure-identity: {sys.executable} -m pip install azure-identity"],
            )

        base_url = str(model_cfg.get("base_url") or "").strip()
        entra_cfg = model_cfg.get("entra") or {}
        if not isinstance(entra_cfg, dict):
            entra_cfg = {}
        scope = (
            str(entra_cfg.get("scope") or "").strip()
            or SCOPE_AI_AZURE_DEFAULT
        )
        config = EntraIdentityConfig(
            scope=scope,
        )
        info = describe_active_credential(config=config, timeout_seconds=10.0)
        if info.get("ok"):
            env_sources = info.get("env_sources") or []
            tag = ", ".join(env_sources) if env_sources else "default credential chain"
            return _ConnectivityResult(
                "Azure Foundry (Entra ID)",
                [(color("✓", Colors.GREEN), label,
                  color(f"({tag}, scope={scope})", Colors.DIM))],
                [],
            )
        err = info.get("error") or "credential chain exhausted"
        hint = info.get("hint") or (
            "Run `az login`, set AZURE_TENANT_ID/AZURE_CLIENT_ID/"
            "AZURE_CLIENT_SECRET, or attach a managed identity to this VM."
        )
        return _ConnectivityResult(
            "Azure Foundry (Entra ID)",
            [(color("⚠", Colors.YELLOW), label,
              color(f"({err})", Colors.DIM))],
            [f"Azure Foundry Entra: {err}. {hint}"],
        )

    # Build the probe submission list in display order
    _probes.append(("OpenRouter API", _probe_openrouter))
    _probes.append(("Anthropic API", _probe_anthropic))

    global _APIKEY_PROVIDERS_CACHE
    if _APIKEY_PROVIDERS_CACHE is None:
        _APIKEY_PROVIDERS_CACHE = _build_apikey_providers_list()
    for _entry in _APIKEY_PROVIDERS_CACHE:
        _pname, _env_vars, _default_url, _base_env, _supports = _entry
        # Capture loop vars by binding default args — without this, all closures
        # would share the final iteration's values and every probe would hit
        # the last provider's URL.
        _probes.append((_pname, lambda p=_pname, e=_env_vars, u=_default_url,
                                       b=_base_env, s=_supports:
                                _probe_apikey_provider(p, e, u, b, s)))

    _probes.append(("AWS Bedrock", _probe_bedrock))
    _probes.append(("Azure Foundry (Entra ID)", _probe_azure_entra))

    # Print a single status line so users see something happening, then
    # fan out. ``\r`` clears it once the first real result line lands.
    print(f"  {color(f'Running {len(_probes)} connectivity checks in parallel…', Colors.DIM)}",
          end="", flush=True)

    # Disable boto3's EC2 instance-metadata-service probe for the duration
    # of the parallel block. boto's default credential chain tries
    # 169.254.169.254 with a multi-second timeout when we're not on EC2,
    # which dominated the section's wall time before this fix
    # (~2s on a developer laptop, even with the rest parallelized).
    # Set on the parent thread before submitting work so the env-var
    # mutation never races with another worker. has_aws_credentials() in
    # the bedrock probe already gates on real env-var creds, so IMDS is
    # never the legitimate source for `intellect doctor`.
    _imds_prev = os.environ.get("AWS_EC2_METADATA_DISABLED")
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    try:
        # 8 workers is plenty — each probe is a single HTTP call plus a TLS
        # handshake. More than that wastes thread-startup cost and risks
        # noisy output if anything ever printed from inside a worker.
        with _futures.ThreadPoolExecutor(max_workers=8,
                                         thread_name_prefix="doctor-probe") as _ex:
            _futures_in_order = [_ex.submit(_fn) for _, _fn in _probes]
            _results = [_f.result() for _f in _futures_in_order]
    finally:
        if _imds_prev is None:
            os.environ.pop("AWS_EC2_METADATA_DISABLED", None)
        else:
            os.environ["AWS_EC2_METADATA_DISABLED"] = _imds_prev

    # Clear the "Running …" line and print all results in submission order.
    print("\r" + " " * 70 + "\r", end="")
    for _r in _results:
        for _glyph, _label, _detail in _r.lines:
            if _detail:
                print(f"  {_glyph} {_label} {_detail}")
            else:
                print(f"  {_glyph} {_label}")
        _issues_to_add = list(_r.issues)
        if _issues_to_add and _has_healthy_oauth_fallback_for_apikey_provider(_r.label):
            _issues_to_add = []
        for _issue in _issues_to_add:
            issues.append(_issue)

    _section("Tool Availability")
    try:
        # Add project root to path for imports
        sys.path.insert(0, str(PROJECT_ROOT))
        from model_tools import check_tool_availability, TOOLSET_REQUIREMENTS
        
        available, unavailable = check_tool_availability()
        available, unavailable = _apply_doctor_tool_availability_overrides(available, unavailable)
        
        for tid in available:
            info = TOOLSET_REQUIREMENTS.get(tid, {})
            check_ok(info.get("name", tid), _doctor_tool_availability_detail(tid))
        
        for item in unavailable:
            env_vars = item.get("missing_vars") or item.get("env_vars") or []
            if env_vars:
                vars_str = ", ".join(env_vars)
                check_warn(item["name"], f"(missing {vars_str})")
            else:
                check_warn(item["name"], "(system dependency not met)")

        # Count disabled tools with API key requirements
        api_disabled = [u for u in unavailable if (u.get("missing_vars") or u.get("env_vars"))]
        if api_disabled:
            issues.append("Run 'intellect setup' to configure missing API keys for full tool access")
    except Exception as e:
        check_warn("Could not check tool availability", f"({e})")
    
    _section("Skills Hub")
    hub_dir = INTELLECT_HOME / "skills" / ".hub"
    if hub_dir.exists():
        check_ok("Skills Hub directory exists")
        lock_file = hub_dir / "lock.json"
        if lock_file.exists():
            try:
                import json
                lock_data = json.loads(lock_file.read_text())
                count = len(lock_data.get("installed", {}))
                check_ok(f"Lock file OK ({count} hub-installed skill(s))")
            except Exception:
                check_warn("Lock file", "(corrupted or unreadable)")
        quarantine = hub_dir / "quarantine"
        q_count = sum(1 for d in quarantine.iterdir() if d.is_dir()) if quarantine.exists() else 0
        if q_count > 0:
            check_warn(f"{q_count} skill(s) in quarantine", "(pending review)")
    else:
        check_warn("Skills Hub directory not initialized", "(run: intellect skills list)")

    from intellect_cli.config import get_env_value

    def _gh_authenticated() -> bool:
        """Check if gh CLI is authenticated via token file or device flow."""
        try:
            result = subprocess.run(
                ["gh", "auth", "status", "--json", "authenticated"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    github_token = get_env_value("GITHUB_TOKEN") or get_env_value("GH_TOKEN")
    if github_token:
        check_ok("GitHub token configured (authenticated API access)")
    elif _gh_authenticated():
        check_ok("GitHub authenticated via gh CLI", "(full API access — no GITHUB_TOKEN needed)")
    else:
        check_warn("No GITHUB_TOKEN", f"(60 req/hr rate limit — set in {_DHH}/.env for better rates)")

    _section("Memory Provider")
    _active_memory_provider = ""
    try:
        import yaml as _yaml
        _mem_cfg_path = INTELLECT_HOME / "config.yaml"
        if _mem_cfg_path.exists():
            with open(_mem_cfg_path, encoding="utf-8") as _f:
                _raw_cfg = _yaml.safe_load(_f) or {}
            _active_memory_provider = (_raw_cfg.get("memory") or {}).get("provider", "")
    except Exception:
        pass

    if not _active_memory_provider:
        check_ok("Built-in memory active", "(no external provider configured — this is fine)")
    elif _active_memory_provider == "honcho":
        try:
            from plugins.memory.honcho.client import HonchoClientConfig, resolve_config_path
            hcfg = HonchoClientConfig.from_global_config()
            _honcho_cfg_path = resolve_config_path()

            if not _honcho_cfg_path.exists():
                check_warn("Honcho config not found", "run: intellect memory setup")
            elif not hcfg.enabled:
                check_info(f"Honcho disabled (set enabled: true in {_honcho_cfg_path} to activate)")
            elif not (hcfg.api_key or hcfg.base_url):
                _fail_and_issue(
                    "Honcho API key or base URL not set",
                    "run: intellect memory setup",
                    "No Honcho API key — run 'intellect memory setup'",
                    issues,
                )
            else:
                from plugins.memory.honcho.client import get_honcho_client, reset_honcho_client
                reset_honcho_client()
                try:
                    get_honcho_client(hcfg)
                    check_ok(
                        "Honcho connected",
                        f"workspace={hcfg.workspace_id} mode={hcfg.recall_mode} freq={hcfg.write_frequency}",
                    )
                except Exception as _e:
                    _fail_and_issue("Honcho connection failed", str(_e), f"Honcho unreachable: {_e}", issues)
        except ImportError:
            _fail_and_issue(
                "honcho-ai not installed",
                "pip install honcho-ai",
                "Honcho is set as memory provider but honcho-ai is not installed",
                issues,
            )
        except Exception as _e:
            check_warn("Honcho check failed", str(_e))
    elif _active_memory_provider == "mem0":
        try:
            from plugins.memory.mem0 import _load_config as _load_mem0_config
            mem0_cfg = _load_mem0_config()
            mem0_key = mem0_cfg.get("api_key", "")
            if mem0_key:
                check_ok("Mem0 API key configured")
                check_info(f"user_id={mem0_cfg.get('user_id', '?')}  agent_id={mem0_cfg.get('agent_id', '?')}")
            else:
                _fail_and_issue(
                    "Mem0 API key not set",
                    "(set MEM0_API_KEY in .env or run intellect memory setup)",
                    "Mem0 is set as memory provider but API key is missing",
                    issues,
                )
        except ImportError:
            _fail_and_issue(
                "Mem0 plugin not loadable",
                "pip install mem0ai",
                "Mem0 is set as memory provider but mem0ai is not installed",
                issues,
            )
        except Exception as _e:
            check_warn("Mem0 check failed", str(_e))
    elif _active_memory_provider == "graphiti":
        # Graphiti = temporal knowledge graph backed by FalkorDB.  Three
        # things can go wrong:
        #   1. graphiti-core / falkordb not installed
        #   2. config exists but FalkorDB unreachable
        #   3. all good — report per-scope graph status from the live manager
        try:
            from plugins.memory.graphiti import GraphitiMemoryProvider
            from plugins.memory.graphiti.config import load_config as _g_load
        except ImportError as _e:
            _fail_and_issue(
                "Graphiti plugin not loadable",
                str(_e),
                "Graphiti is set as memory provider but the plugin module "
                "failed to import",
                issues,
            )
        else:
            _gprov = GraphitiMemoryProvider()
            if not _gprov.is_available():
                _fail_and_issue(
                    "graphiti-core / falkordb not installed",
                    "pip install 'intellect-agent[graphiti]'",
                    "Graphiti is set as memory provider but its optional "
                    "dependencies are missing",
                    issues,
                )
            else:
                _gcfg = _g_load(str(INTELLECT_HOME))
                _gtarget = (
                    f"{_gcfg.get('falkordb_host', 'localhost')}:"
                    f"{_gcfg.get('falkordb_port', 6380)}"
                )
                # Try a live ping via the manager.  Anonymous CLI scope
                # = global graph — sufficient to prove the FalkorDB
                # connection works.
                try:
                    from plugins.memory.graphiti.client import (
                        GraphitiClientManager,
                    )
                    _gmgr = GraphitiClientManager(_gcfg)
                    _gmgr.bind_scope(
                        member_id=None, team_id=None, project_id=None
                    )
                    try:
                        _gpings = _gmgr.ping()
                    finally:
                        try:
                            _gmgr.shutdown()
                        except Exception:
                            pass
                    if _gpings and all(_gpings.values()):
                        check_ok(
                            "Graphiti connected",
                            f"backend={_gtarget} graphs="
                            + ",".join(_gpings.keys()),
                        )
                    elif _gpings:
                        _down = [
                            g for g, ok in _gpings.items() if not ok
                        ]
                        _fail_and_issue(
                            "Graphiti backend unreachable",
                            f"backend={_gtarget} down="
                            + ",".join(_down),
                            f"FalkorDB at {_gtarget} unreachable for: "
                            + ",".join(_down),
                            issues,
                        )
                    else:
                        check_warn(
                            "Graphiti configured but no graphs to ping",
                            "log in as a member or set up a global graph",
                        )
                except Exception as _gex:
                    _fail_and_issue(
                        "Graphiti connection failed",
                        str(_gex),
                        f"FalkorDB at {_gtarget} error: {_gex}",
                        issues,
                    )
    else:
        # Generic check for other memory providers (openviking, hindsight, etc.)
        try:
            from plugins.memory import load_memory_provider
            _provider = load_memory_provider(_active_memory_provider)
            if _provider and _provider.is_available():
                check_ok(f"{_active_memory_provider} provider active")
            elif _provider:
                check_warn(f"{_active_memory_provider} configured but not available", "run: intellect memory status")
            else:
                check_warn(f"{_active_memory_provider} plugin not found", "run: intellect memory setup")
        except Exception as _e:
            check_warn(f"{_active_memory_provider} check failed", str(_e))

    # RAG provider (parallel to memory — rag.provider in config.yaml)
    _active_rag_provider = ""
    _rag_enabled = True
    try:
        import yaml as _yaml_rag
        _rag_cfg_path = INTELLECT_HOME / "config.yaml"
        if _rag_cfg_path.exists():
            with open(_rag_cfg_path, encoding="utf-8") as _rf:
                _rag_raw = _yaml_rag.safe_load(_rf) or {}
            _rag_cfg = _rag_raw.get("rag") or {}
            _rag_enabled = _rag_cfg.get("enabled", True)
            _active_rag_provider = (_rag_cfg.get("provider") or "").strip()
    except Exception:
        pass

    if _rag_enabled and _active_rag_provider:
        _section("RAG Provider")
        if _active_rag_provider == "lightrag":
            try:
                from plugins.rag.lightrag.doctor import diagnose_lightrag_rag

                diagnose_lightrag_rag(
                    str(INTELLECT_HOME),
                    check_ok=check_ok,
                    check_warn=check_warn,
                    check_info=check_info,
                    fail_fn=_fail_and_issue,
                    issues=issues,
                )
            except ImportError as _lr_ex:
                _fail_and_issue(
                    "LightRAG doctor module not loadable",
                    str(_lr_ex),
                    "rag.provider is lightrag but doctor checks failed to import",
                    issues,
                )
            except Exception as _lr_ex:
                check_warn("LightRAG check failed", str(_lr_ex))
        else:
            try:
                from plugins.rag import load_rag_provider

                _rp = load_rag_provider(_active_rag_provider)
                if _rp and _rp.is_available():
                    check_ok(f"{_active_rag_provider} RAG provider active")
                elif _rp:
                    check_warn(
                        f"{_active_rag_provider} configured but not available",
                        "check plugin config",
                    )
                else:
                    check_warn(
                        f"{_active_rag_provider} RAG plugin not found",
                        "verify rag.provider in config.yaml",
                    )
            except Exception as _e:
                check_warn(f"{_active_rag_provider} RAG check failed", str(_e))

    try:
        from intellect_cli.profiles import list_profiles, _get_wrapper_dir, profile_exists
        import re as _re

        named_profiles = [p for p in list_profiles() if not p.is_default]
        if named_profiles:
            _section("Profiles")
            check_ok(f"{len(named_profiles)} profile(s) found")
            wrapper_dir = _get_wrapper_dir()
            for p in named_profiles:
                parts = []
                if p.gateway_running:
                    parts.append("gateway running")
                if p.model:
                    parts.append(p.model[:30])
                if not (p.path / "config.yaml").exists():
                    parts.append("⚠ missing config")
                if not (p.path / ".env").exists():
                    parts.append("no .env")
                wrapper = wrapper_dir / p.name
                if not wrapper.exists():
                    parts.append("no alias")
                status = ", ".join(parts) if parts else "configured"
                check_ok(f"  {p.name}: {status}")

            # Check for orphan wrappers
            if wrapper_dir.is_dir():
                for wrapper in wrapper_dir.iterdir():
                    if not wrapper.is_file():
                        continue
                    try:
                        content = wrapper.read_text()
                        if "intellect -p" in content:
                            _m = _re.search(r"intellect -p (\S+)", content)
                            if _m and not profile_exists(_m.group(1)):
                                check_warn(f"Orphan alias: {wrapper.name} → profile '{_m.group(1)}' no longer exists")
                    except Exception:
                        pass
    except ImportError:
        pass
    except Exception:
        pass

    # ── Multi-user / multi-project health ─────────────────────────────────
    try:
        _check_project_health(issues)
    except Exception:
        pass

    # ── OAuth health ──────────────────────────────────────────────────────
    try:
        _check_oauth_health(issues)
    except Exception:
        pass

    try:
        _check_session_isolation_health(issues)
    except Exception:
        pass

    print()
    remaining_issues = issues + manual_issues
    if should_fix and fixed_count > 0:
        print(color("─" * 60, Colors.GREEN))
        print(color(f"  Fixed {fixed_count} issue(s).", Colors.GREEN, Colors.BOLD), end="")
        if remaining_issues:
            print(color(f" {len(remaining_issues)} issue(s) require manual intervention.", Colors.YELLOW, Colors.BOLD))
        else:
            print()
        print()
        if remaining_issues:
            for i, issue in enumerate(remaining_issues, 1):
                print(f"  {i}. {issue}")
            print()
    elif remaining_issues:
        print(color("─" * 60, Colors.YELLOW))
        print(color(f"  Found {len(remaining_issues)} issue(s) to address:", Colors.YELLOW, Colors.BOLD))
        print()
        for i, issue in enumerate(remaining_issues, 1):
            print(f"  {i}. {issue}")
        print()
        if not should_fix:
            print(color("  Tip: run 'intellect doctor --fix' to auto-fix what's possible.", Colors.DIM))
    else:
        print(color("─" * 60, Colors.GREEN))
        print(color("  All checks passed! 🎉", Colors.GREEN, Colors.BOLD))
    
    print()
