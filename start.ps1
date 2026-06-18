# Intellect Agent 开发环境启动脚本
# 用法: .\dev_start.ps1 [webui|agent|all]

param(
    [string]$Command = "webui"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$VenvPython = "$ProjectRoot\.venv\Scripts\python.exe"
$VenvIntellect = "$ProjectRoot\.venv\Scripts\intellect.exe"

# 检查 venv 是否存在
if (-not (Test-Path $VenvPython)) {
    Write-Error "venv 不存在，请先运行: uv sync"
    exit 1
}

# 加载 .env 中的环境变量
$EnvFile = "$env:USERPROFILE\.intellect\.env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)') {
            $key = $Matches[1].Trim()
            $val = $Matches[2].Trim()
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
    Write-Host "[env] Loaded $EnvFile" -ForegroundColor Gray
}

# 检查 Rust 扩展
$HasRust = & $VenvPython -c "import intellect_community_core; print('OK')" 2>$null
if (-not $HasRust) {
    Write-Warning "Rust 扩展未安装，性能受限。如需编译: cd rust-core; maturin develop --release"
}

function Start-WebUI {
    Write-Host "=== Starting WebUI ===" -ForegroundColor Cyan
    & $VenvIntellect webui stop 2>$null
    Start-Sleep -Seconds 1
    & $VenvIntellect webui start
    Start-Sleep -Seconds 3

    $Health = try { Invoke-RestMethod -Uri "http://127.0.0.1:9119/health" -TimeoutSec 3 } catch { $null }
    if ($Health -and $Health.status -eq "ok") {
        Write-Host "WebUI OK: http://127.0.0.1:9119" -ForegroundColor Green
    } else {
        Write-Warning "WebUI 可能尚未就绪，检查日志: $env:USERPROFILE\.intellect\webui.log"
    }
}

function Start-Agent {
    Write-Host "=== Starting Agent (interactive) ===" -ForegroundColor Cyan
    $Model = if ($env:DEEPSEEK_API_KEY) { "deepseek-v4-flash" } else { "" }
    & $VenvPython -m run_agent --model $Model --verbose
}

switch ($Command) {
    "webui" { Start-WebUI }
    "agent" { Start-Agent }
    "all"   { Start-WebUI; Start-Agent }
    default { Write-Host "用法: .\dev_start.ps1 [webui|agent|all]" }
}
