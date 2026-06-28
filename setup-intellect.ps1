# ============================================================================
# Intellect Agent Setup Script for Windows
# ============================================================================
# Quick setup for developers who cloned the repo manually on Windows.
# Uses uv for fast Python provisioning and package management.
#
# Usage:
#   .\setup-intellect.ps1                  # Interactive (default)
#   .\setup-intellect.ps1 -NonInteractive   # CI / automation
#   .\setup-intellect.ps1 -SkipSetup        # Skip setup wizard interactive prompt
#
# This script:
# 1. Detects / installs uv
# 2. Locates or installs Python 3.12 via uv
# 3. Creates a Python 3.12 virtual environment
# 4. Installs dependencies (prefers uv.lock hash-verified, falls back to pip)
# 5. Creates .env from template (if not exists)
# 6. Symlinks the 'intellect' CLI into %USERPROFILE%\.local\bin\
# 7. Writes User PATH
# 8. Syncs bundled skills
# 9. Optionally runs the setup wizard
# ============================================================================

param(
    [switch]$SkipSetup,
    [switch]$NonInteractive
)

# Support env var for CI compatibility (same as bash: INTELLECT_SKIP_SETUP=1)
if ($env:INTELLECT_SKIP_SETUP -eq "1") {
    $SkipSetup = $true
}

$ErrorActionPreference = "Stop"

# Force the console to UTF-8 so non-ASCII output renders correctly.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # Some constrained PowerShell hosts disallow encoding mutation.
}

# ============================================================================
# Configuration
# ============================================================================
$PythonVersion = "3.12"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Prevent uv from discovering config files from the wrong user's home
# directory when running under a different user context.
$env:UV_NO_CONFIG = "1"

# ============================================================================
# Helper functions
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|           * Intellect Agent Setup                          |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|  Developer setup script — builds from source.            |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "-> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "[X] $Message" -ForegroundColor Red
}

# Read a yes/no prompt. Returns $true for yes, $false for no.
# In NonInteractive mode, defaults to $true.
function Read-YesNo {
    param([string]$Prompt)
    if ($NonInteractive) { return $true }
    $response = Read-Host "$Prompt [Y/n]"
    return ($response -eq "" -or $response -match '^[Yy]')
}

# ============================================================================
# Main setup
# ============================================================================

Set-Location $ScriptDir

Write-Banner

# ============================================================================
# 1. Install / locate uv
# ============================================================================

Write-Info "Checking for uv..."

$UvCmd = $null

# Check if uv is already on PATH
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $version = uv --version
    $UvCmd = "uv"
    Write-Success "uv found ($version)"
}

# Check common install locations
if (-not $UvCmd) {
    $uvPaths = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    foreach ($uvPath in $uvPaths) {
        if (Test-Path $uvPath) {
            $UvCmd = $uvPath
            $version = & $uvPath --version
            Write-Success "uv found at $uvPath ($version)"
            break
        }
    }
}

# Install uv if not found
if (-not $UvCmd) {
    Write-Info "Installing uv (fast Python package manager)..."
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        # Find the installed binary
        $uvExe = "$env:USERPROFILE\.local\bin\uv.exe"
        if (-not (Test-Path $uvExe)) {
            $uvExe = "$env:USERPROFILE\.cargo\bin\uv.exe"
        }
        if (-not (Test-Path $uvExe)) {
            # Refresh PATH from registry and try again
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                $uvExe = (Get-Command uv).Source
            }
        }

        if (Test-Path $uvExe) {
            $UvCmd = $uvExe
            $version = & $uvExe --version
            Write-Success "uv installed ($version)"
        } else {
            Write-Err "uv installed but not found on PATH."
            Write-Info "Try restarting your terminal and re-running this script."
            Write-Info "Or install manually: https://docs.astral.sh/uv/getting-started/installation/"
            exit 1
        }
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-Err "Failed to install uv: $_"
        Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    }
}

# ============================================================================
# 2. Python check (uv can provision it automatically)
# ============================================================================

Write-Info "Checking Python $PythonVersion..."

$PythonPath = $null
try {
    $pythonFind = & $UvCmd python find $PythonVersion 2>&1
    if ($LASTEXITCODE -eq 0) {
        $PythonPath = $pythonFind.Trim()
        $pyVersion = & $PythonPath --version 2>&1
        Write-Success "$pyVersion found"
    }
} catch {
    # uv python find failed — Python not available, install it
}

if (-not $PythonPath) {
    Write-Info "Python $PythonVersion not found, installing via uv..."
    & $UvCmd python install $PythonVersion
    $PythonPath = (& $UvCmd python find $PythonVersion).Trim()
    $pyVersion = & $PythonPath --version 2>&1
    Write-Success "$pyVersion installed"
}

# ============================================================================
# 3. Virtual environment
# ============================================================================

Write-Info "Setting up virtual environment..."

if (Test-Path "venv") {
    Write-Info "Removing old venv..."
    Remove-Item -Recurse -Force venv
}

& $UvCmd venv venv --python $PythonVersion
Write-Success "venv created (Python $PythonVersion)"

$env:VIRTUAL_ENV = Join-Path $ScriptDir "venv"
$SetupPython = Join-Path $ScriptDir "venv\Scripts\python.exe"

# ============================================================================
# 4. Dependencies
# ============================================================================

Write-Info "Installing dependencies..."

# Build the safe extras list (mirrors setup-intellect.sh logic)
$_BROKEN_EXTRAS = @()  # populate when an extra becomes unresolvable
$_ALL_EXTRAS = @(
    "modal", "daytona", "messaging", "matrix", "cron", "cli", "dev",
    "tts-premium", "slack", "pty", "honcho", "mcp", "homeassistant",
    "sms", "acp", "voice", "dingtalk", "feishu", "google",
    "bedrock", "web", "youtube"
)
$_SAFE_EXTRAS = @()
foreach ($_e in $_ALL_EXTRAS) {
    $_skip = $false
    foreach ($_b in $_BROKEN_EXTRAS) {
        if ($_e -eq $_b) { $_skip = $true; break }
    }
    if (-not $_skip) { $_SAFE_EXTRAS += $_e }
}
$_SAFE_SPEC = ".[$($_SAFE_EXTRAS -join ',')]"

function Invoke-PipInstall {
    # Multi-tier fallback: .[all] -> safe extras -> base
    & $UvCmd pip install -e ".[all]" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn ".[all] install failed, trying safe extras..."
        & $UvCmd pip install -e $_SAFE_SPEC 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Safe extras install failed, falling back to base..."
            & $UvCmd pip install -e "." 2>&1
        }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Dependency installation failed. See output above for details."
        exit 1
    }
}

if (Test-Path "uv.lock") {
    # Hash-verified install (preferred)
    Write-Info "Using uv.lock for hash-verified installation..."
    Write-Info "(first run on a fresh venv can take 1-5 minutes; uv prints progress below)"
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $ScriptDir "venv"
    & $UvCmd sync --extra all --locked 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Dependencies installed (hash-verified via uv.lock)"
    } else {
        Write-Warn "Lockfile sync failed (see uv output above)."
        Write-Warn "Falling back to PyPI resolve — transitives will NOT be hash-verified."
        Invoke-PipInstall
        Write-Success "Dependencies installed (transitives re-resolved, not hash-verified)"
    }
} else {
    Write-Warn "uv.lock not found — installing without hash verification of transitives."
    Invoke-PipInstall
    Write-Success "Dependencies installed (transitives re-resolved, not hash-verified)"
}

# ============================================================================
# 5. Optional: ripgrep
# ============================================================================

Write-Info "Checking ripgrep (optional, for faster search)..."

if (Get-Command rg -ErrorAction SilentlyContinue) {
    Write-Success "ripgrep found"
} else {
    Write-Warn "ripgrep not found (file search will use grep fallback)"
    if (Read-YesNo "Install ripgrep for faster search?") {
        $rgInstalled = $false
        # Try winget first
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            try {
                winget install BurntSushi.ripgrep.MSVC --accept-source-agreements --silent 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { $rgInstalled = $true }
            } catch { }
        }
        # Try cargo as fallback
        if (-not $rgInstalled -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
            Write-Info "Trying cargo install..."
            try {
                cargo install ripgrep 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { $rgInstalled = $true }
            } catch { }
        }
        if ($rgInstalled) {
            Write-Success "ripgrep installed"
        } else {
            Write-Warn "Auto-install failed. Install options:"
            Write-Host "    winget install BurntSushi.ripgrep.MSVC"
            Write-Host "    cargo install ripgrep"
            Write-Host "    https://github.com/BurntSushi/ripgrep#installation"
        }
    }
}

# ============================================================================
# 6. Environment file
# ============================================================================

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Success "Created .env from template"
    }
} else {
    Write-Success ".env exists"
}

# ============================================================================
# 7. PATH setup — symlink intellect into user-facing bin dir
# ============================================================================

Write-Info "Setting up intellect command..."

$LocalBin = "$env:USERPROFILE\.local\bin"
if (-not (Test-Path $LocalBin)) {
    New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null
}

$IntellectBin = Join-Path $ScriptDir "venv\Scripts\intellect.exe"
$IntellectLink = Join-Path $LocalBin "intellect.ps1"

# Create a launcher script that invokes the venv intellect
# (Windows doesn't have symlinks for non-admin without developer mode)
$launcherContent = @"
# Intellect Agent launcher — invokes the venv's intellect.exe
& "$IntellectBin" @args
"@
Set-Content -Path $IntellectLink -Value $launcherContent -Encoding UTF8
Write-Success "Launcher created: $IntellectLink"

# Ensure %USERPROFILE%\.local\bin is in User PATH
$currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User") ?? ""
if ($currentUserPath -notlike "*$LocalBin*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentUserPath;$LocalBin", "User")
    $env:Path = "$env:Path;$LocalBin"
    Write-Success "Added %USERPROFILE%\.local\bin to User PATH"
} else {
    Write-Success "%USERPROFILE%\.local\bin already in User PATH"
}

# ============================================================================
# 8. Seed bundled skills into ~/.intellect/skills/
# ============================================================================

$IntellectSkillsDir = if ($env:INTELLECT_HOME) {
    Join-Path $env:INTELLECT_HOME "skills"
} else {
    Join-Path $env:USERPROFILE "\.intellect\skills"
}

if (-not (Test-Path $IntellectSkillsDir)) {
    New-Item -ItemType Directory -Force -Path $IntellectSkillsDir | Out-Null
}

Write-Host ""
Write-Info "Syncing bundled skills to ~/.intellect/skills/ ..."

$SkillsSyncScript = Join-Path $ScriptDir "tools\skills_sync.py"
if (Test-Path $SkillsSyncScript) {
    try {
        & $SetupPython $SkillsSyncScript 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Skills synced"
        } else {
            throw "Sync script failed"
        }
    } catch {
        # Fallback: copy if sync script fails
        $SkillsSource = Join-Path $ScriptDir "skills"
        if (Test-Path $SkillsSource) {
            Copy-Item -Recurse -Force "$SkillsSource\*" $IntellectSkillsDir 2>$null
            Write-Success "Skills copied (fallback)"
        }
    }
} else {
    $SkillsSource = Join-Path $ScriptDir "skills"
    if (Test-Path $SkillsSource) {
        Copy-Item -Recurse -Force "$SkillsSource\*" $IntellectSkillsDir 2>$null
        Write-Success "Skills copied"
    }
}

# ============================================================================
# Done
# ============================================================================

Write-Host ""
Write-Success "Setup complete!"
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "  1. Restart your terminal (or open a new PowerShell window)"
Write-Host ""
Write-Host "  2. Run the setup wizard to configure API keys:"
Write-Host "     intellect setup"
Write-Host ""
Write-Host "  3. Start chatting:"
Write-Host "     intellect"
Write-Host ""
Write-Host "Other commands:"
Write-Host "  intellect status           # Check configuration"
Write-Host "  intellect gateway install  # Install gateway service (messaging + cron)"
Write-Host "  intellect cron list        # View scheduled jobs"
Write-Host "  intellect doctor           # Diagnose issues"
Write-Host ""

# Ask if they want to run setup wizard now
if (-not $SkipSetup) {
    if (Read-YesNo "Would you like to run the setup wizard now?") {
        Write-Host ""
        & $SetupPython -m intellect_cli.main setup
    }
}
