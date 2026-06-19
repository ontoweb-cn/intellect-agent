# Review: `install.ps1` Windows PowerShell compatibility fixes

**Date:** 2026-06-19  
**Branch:** `fix/install-ps1-windows-eap-uv-maturin` (local)  
**Scope:** `scripts/install.ps1` only  
**Validated on:** Windows 10/11, PowerShell 5.1, uv 0.11.22, Python 3.12.10 (winget), Rust 1.96

---

## Summary

Windows installs via `scripts/install.ps1` fail under the script’s global
`$ErrorActionPreference = "Stop"` because **native commands** (`uv`, `git`,
`python`, `maturin`) write routine progress to **stderr**. PowerShell 5.1
surfaces that stderr as **terminating `NativeCommandError` records**, aborting
the installer even when `$LASTEXITCODE` is 0.

A second class of failures affects **Rust extension** installation on fresh
`uv venv` environments: the venv has **no pip**, maturin is not on `PATH`, and
a stale sibling **`.venv/`** directory causes maturin to pick the wrong Python.

This change set applies the **same EAP=Continue + `$LASTEXITCODE` pattern**
already used for `git fetch` and Playwright in this file, switches wheel/maturin
install to **`uv pip`**, and pins **`VIRTUAL_ENV` / `PYO3_PYTHON`** for local
maturin builds.

---

## Root cause

| Symptom | stderr example | Why EAP=Stop aborts |
|---------|----------------|---------------------|
| Venv creation “fails” | `Using CPython 3.12.10 interpreter at: …` | uv info line on stderr |
| Rust probe “throws” | `Traceback … ModuleNotFoundError` | expected when ext missing |
| `uv sync` never reaches fallback | `Resolved 214 packages in 32.97s` | uv progress on stderr |
| maturin “not found” after pip | N/A | venv `Scripts\` not on PATH |
| maturin picks dead Python | `No Python at '…uv\python\…'` | stale `.venv/` preferred over `venv/` |

**Note:** `2>$null` does **not** suppress NativeCommandError under EAP=Stop on
Windows PowerShell 5.1 — the fix must temporarily set
`$ErrorActionPreference = "Continue"`.

---

## Proposed changes (by function)

### 1. `Install-Venv`

**Problem:** `uv venv` succeeds but installer exits on stderr banner.

**Fix:**
- Wrap `& $UvCmd venv …` in EAP=Continue; throw only on non-zero exit code.
- Remove stale `.venv/` before creating `venv/` (aborted `uv sync` leaves a
  broken sibling env that maturin prefers).

### 2. `Test-RustExtensionInstalled`

**Problem:** `import intellect_community_core` traceback on stderr terminates
the probe instead of returning `$false`.

**Fix:** EAP=Continue around the python invocation; return `$LASTEXITCODE -eq 0`.

### 3. `Install-RustExtensionFromGitee`

**Problems:**
- Gitee URL lookup python script stderr can terminate under EAP=Stop.
- `python -m pip install` fails on uv-created venvs (**pip not bootstrapped**).

**Fix:**
- EAP=Continue around URL lookup script.
- Replace `python -m pip install` with `uv pip install --python $PythonExe`.
- Set `UV_PROJECT_ENVIRONMENT` / `VIRTUAL_ENV` when using a venv.

### 4. `Install-RustExtensionLocal`

**Problems:**
- `python -m pip install maturin` — same missing-pip issue.
- `Get-Command maturin` misses venv-local `maturin.exe`.
- `maturin develop` without `VIRTUAL_ENV` fails when only `venv/` exists.
- maturin stderr (🐍 Found CPython …) can terminate under EAP=Stop.

**Fix:**
- Resolve maturin as `venv\Scripts\maturin.exe` first, then PATH.
- Install via `uv pip install --python $PythonExe maturin` if missing.
- Set `PYO3_PYTHON`, `VIRTUAL_ENV`, `UV_PROJECT_ENVIRONMENT` before build.
- Invoke `& $maturinCmd develop --release` under EAP=Continue.

### 5. `Install-Dependencies` — `uv sync` / fallback tiers

**Problem:** `uv sync --locked` prints resolver progress to stderr; when the
lockfile is stale the script should **fall through to pip tiers**, but EAP=Stop
throws before `$LASTEXITCODE` is checked.

**Fix:** EAP=Continue around `uv sync` and each `uv pip install -e` tier;
branch on captured exit code (existing tiered fallback logic unchanged).

---

## Out of scope (follow-ups for separate PRs)

| Item | Rationale |
|------|-----------|
| **`Invoke-WithNativeCommand` helper** | DRY for ~8 EAP wraps; nice refactor, not required for correctness |
| **`Install-Repository` git checkout/pull EAP wrap** | stderr noise today but update path already uses EAP=Continue; checkout lines still emit red errors cosmetically |
| **`PLAYWRIGHT_DOWNLOAD_HOST` / npm mirror env vars** | npmmirror lacks Chromium v1228 (404); document `AGENT_BROWSER_EXECUTABLE_PATH` fallback instead |
| **Stage `node-deps` in isolation** | `$HasNode` unset when `-Stage node-deps` run without `-Stage node`; pre-existing stage-protocol gap |
| **`Resolve-UvCmd` in `Install-RustExtensionFromGitee`** | Defensive; currently safe because `Stage-Dependencies` always calls `Resolve-UvCmd` first |

---

## Related follow-up: WebUI Windows launch (separate PR)

**Not part of `install.ps1`**, but discovered during the same Windows install
session and should land as a core fix:

| Symptom | Root cause | Fix location |
|---------|------------|--------------|
| `[webui] Failed to stay running` / `No module named 'webui'` | `webui_start()` used base `pythonw.exe` with only venv `site-packages` on `PYTHONPATH`; missing agent project root for `-m webui.server` | `intellect_cli/webui.py` |
| Chat fails with Rust extension missing | `_discover_python()` preferred system `pythonw.exe` over venv | `webui/api/config.py` |
| Quota probes miss venv deps | Subprocess env used `sys.prefix` instead of `VIRTUAL_ENV` site-packages | `webui/api/providers.py` |

Pattern: mirror `gateway_windows._resolve_detached_python()` + project root on
`PYTHONPATH` + `cwd` (same approach as gateway daemon launch).

Test: `tests/intellect_cli/test_webui_windows.py`.

Post-install smoke (after install.ps1 completes):

```powershell
intellect webui start
# expect: [webui] Started (PID …)
curl http://127.0.0.1:9119/api/health/agent
```

---

## Operator note: manual package installs on uv venvs

uv-created venvs **do not ship `pip.exe`** (and often have no `pip` module until
`ensurepip` runs). Users following docs that say `venv\Scripts\pip.exe install …`
will hit "无法识别 cmdlet" on Windows.

**Preferred recovery command** (matches what the installer uses internally):

```powershell
$env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"
uv pip install --python "d:\workspace\intellect-agent\venv\Scripts\python.exe" "<package-spec>"
```

`Install-PlatformSdks` already bootstraps pip via `ensurepip` as a last resort
for messaging SDKs; general ad-hoc installs should document `uv pip` instead.

During partial installs (tier fallback when `uv sync --locked` fails),
transitive deps like `websockets` may be absent even though the WebUI starts.
A full `[all]` tier success avoids this; otherwise install manually with `uv pip`.

---

## Optional install.ps1 enhancements (future PR)

| Enhancement | Why |
|-------------|-----|
| Post-install import probe for `websockets`, `intellect_community_core` | Catches incomplete tier fallback before user hits WebUI/chat |
| User-facing error text: `uv pip install` not `pip.exe` | Reduces support noise on Windows uv venvs |
| `intellect webui start` smoke in `-Stage path` or finalize | Validates daemon launch path end-to-end |

These are **nice-to-have**; the EAP/uv/maturin fixes above remain the blocker
for install.ps1 itself.


## Test plan

Manual (Windows PowerShell 5.1):

```powershell
# Clean-ish repro
cd $env:USERPROFILE
Remove-Item -Recurse -Force "d:\workspace\intellect-agent\venv","d:\workspace\intellect-agent\.venv" -ErrorAction SilentlyContinue

# Full non-interactive install into existing checkout
$env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"
& "d:\workspace\intellect-agent\scripts\install.ps1" `
  -InstallDir "d:\workspace\intellect-agent" `
  -SkipSetup -NonInteractive

# Verify
d:\workspace\intellect-agent\venv\Scripts\intellect.exe --version
d:\workspace\intellect-agent\venv\Scripts\python.exe -c "import intellect_community_core"
```

Expected:
1. Passes `Creating virtual environment` (no abort on “Using CPython …”).
2. Builds or downloads Rust extension without “maturin not recognized”.
3. On stale lockfile: warns on `uv sync --locked`, succeeds via pip tier fallback.
4. `intellect --version` works from venv.
5. `python -c "import intellect_community_core, websockets"` succeeds (or Rust
   build + `uv pip install websockets` documented if tier fallback was partial).
6. `intellect webui start` stays running (requires separate WebUI launch fix PR).

Manual package install (if a dep is missing — **do not use `pip.exe`**):

```powershell
$env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"
uv pip install --python "d:\workspace\intellect-agent\venv\Scripts\python.exe" "<spec>"
```

Stage-driver smoke (optional):

```powershell
foreach ($s in @("venv","dependencies")) {
  & scripts\install.ps1 -InstallDir (Get-Location) -Stage $s -NonInteractive -Json
}
```

---

## Risk assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| Masking real uv/maturin failures | Low | Still check `$LASTEXITCODE`; throw on non-zero where appropriate |
| Behavior change on Unix | None | File is Windows-only (`install.ps1`) |
| `uv pip` vs `pip` divergence | Low | Matches existing `Install-Dependencies` tier paths; uv is already required |

---

## Suggested commit message

```
fix(install): harden install.ps1 for Windows PowerShell EAP=Stop

Wrap uv/maturin native stderr under ErrorActionPreference=Continue so
informational output does not abort the installer, and fix Rust extension
install to use uv pip plus venv-scoped maturin paths on fresh uv venvs.
```

---

## Reviewer checklist

- [ ] EAP wraps restore `$ErrorActionPreference` in `finally` blocks
- [ ] No `$UvCmd` use before `Resolve-UvCmd` on code paths you care about
- [ ] Stale `.venv/` removal is acceptable (documented in comment)
- [ ] Tiered dependency fallback still runs when `uv sync --locked` fails
- [ ] Manual test plan executed on Windows PowerShell 5.1
