# Build a Quartz vault from a wiki directory (Windows).
# Called by: webui/api/routes.py and intellect_cli/vault_build.py
#
# Usage: build.ps1 <wiki_path> <output_dir> <title> <base_path>
#   wiki_path   - source markdown directory (e.g. C:\Users\Simon\wiki)
#   output_dir  - where to write the static site
#   title       - site title
#   base_path   - base URL path (e.g. /vault or /vault/t/my-team)
param(
    [Parameter(Mandatory=$true)] [string]$WikiPath,
    [Parameter(Mandatory=$true)] [string]$OutputDir,
    [string]$Title = "LLM Wiki",
    [string]$BasePath = "/vault"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$QuartzRepo = Join-Path $ScriptDir "_quartz"
$QuartzContent = Join-Path $QuartzRepo "content"
$QuartzConfig = Join-Path $QuartzRepo "quartz.config.ts"
$QuartzLayout = Join-Path $QuartzRepo "quartz.layout.ts"

# ── Ensure Node.js is available ───────────────────────────────────────
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Error "[vault] Node.js is required but not found on PATH. Install Node.js 22+ from https://nodejs.org"
    exit 1
}

# ── Ensure Quartz is installed ────────────────────────────────────────
if (-not (Test-Path (Join-Path $QuartzRepo ".git"))) {
    Write-Host "[vault] Cloning Quartz (one-time, ~50MB)..."
    git clone --depth 1 --branch v4 https://github.com/jackyzha0/quartz.git $QuartzRepo 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[vault] ERROR: Failed to clone Quartz repository"
        exit 1
    }
    Write-Host "[vault] Installing Quartz npm dependencies..."
    Push-Location $QuartzRepo
    try {
        npm install --prefer-offline 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Error "[vault] ERROR: Failed to install Quartz dependencies"
            exit 1
        }
        Write-Host "[vault] Quartz ready."
    } finally {
        Pop-Location
    }
}

# ── Symlink wiki content into Quartz content dir ───────────────────────
if (Test-Path $QuartzContent) { Remove-Item -Recurse -Force $QuartzContent }
# PowerShell requires admin for symlinks; use junction as fallback
try {
    New-Item -ItemType SymbolicLink -Path $QuartzContent -Target $WikiPath -Force | Out-Null
} catch {
    # Fallback: create a directory junction (works without admin on NTFS)
    try {
        New-Item -ItemType Junction -Path $QuartzContent -Target $WikiPath -Force | Out-Null
    } catch {
        # Last resort: copy content
        Write-Host "[vault] Symlink/junction failed, copying wiki content..."
        Copy-Item -Recurse -Force "$WikiPath\*" $QuartzContent
    }
}

# ── Generate quartz.config.ts if missing ───────────────────────────────
if (-not (Test-Path $QuartzConfig)) {
    @"
import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

const config: QuartzConfig = {
    configuration: {
        pageTitle: "${Title}",
        baseUrl: "${BasePath}",
        enableSPA: true,
        enablePopovers: true,
        analytics: null,
        locale: "en-US",
        generateIndex: true,
        defaultDateType: "modified",
        ignorePatterns: ["_*\\.md", "private/**"],
        theme: {
            typography: {
                header: "Inter",
                body: "Inter",
                code: "JetBrains Mono",
            },
            colors: {
                lightMode: {
                    light: "#ffffff",
                    lightgray: "#e5e5e5",
                    gray: "#b8b8b8",
                    darkgray: "#4e4e4e",
                    dark: "#2b2b2b",
                    secondary: "#284b63",
                    tertiary: "#84a59d",
                    highlight: "rgba(143, 183, 169, 0.15)",
                },
                darkMode: {
                    light: "#161618",
                    lightgray: "#393639",
                    gray: "#646464",
                    darkgray: "#d4d4d4",
                    dark: "#ebebec",
                    secondary: "#7b97aa",
                    tertiary: "#84a59d",
                    highlight: "rgba(143, 183, 169, 0.15)",
                },
            },
        },
    },
    plugins: {
        transformers: [
            Plugin.FrontMatter(),
            Plugin.TableOfContents(),
            Plugin.CreatedModifiedDate({ priority: ["frontmatter", "git", "filesystem"] }),
            Plugin.SyntaxHighlighting(),
            Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
            Plugin.GitHubFlavoredMarkdown(),
            Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
            Plugin.Latex({ renderEngine: "katex" }),
            Plugin.Description(),
        ],
        filters: [Plugin.RemoveDrafts()],
        emitters: [
            Plugin.AliasRedirects(),
            Plugin.ComponentResources({ fontOrigin: "googleFonts" }),
            Plugin.ContentPage(),
            Plugin.FolderPage(),
            Plugin.TagPage(),
            Plugin.ContentIndex({ enableSiteMap: true, enableRSS: true }),
            Plugin.Assets(),
            Plugin.Static(),
            Plugin.NotFoundPage(),
        ],
    },
}

export default config
"@ | Out-File -Encoding UTF8 $QuartzConfig
}

# ── Generate quartz.layout.ts if missing ──────────────────────────────
if (-not (Test-Path $QuartzLayout)) {
    @"
import { defaultLayout } from "./quartz/cfg"
export { defaultLayout }
"@ | Out-File -Encoding UTF8 $QuartzLayout
}

# ── Build ─────────────────────────────────────────────────────────────
Write-Host "[vault] Building Quartz site from ${WikiPath} -> ${OutputDir}..."
Push-Location $QuartzRepo
try {
    npx quartz build --output $OutputDir --concurrency 4 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[vault] Quartz build failed (exit code $LASTEXITCODE)"
        exit 1
    }
} finally {
    Pop-Location
}

# Copy built output if Quartz puts it elsewhere
$publicDir = Join-Path $QuartzRepo "public"
if ((Test-Path $publicDir) -and ((Resolve-Path $publicDir).Path -ne (Resolve-Path $OutputDir).Path)) {
    Remove-Item -Recurse -Force $OutputDir -ErrorAction SilentlyContinue
    Copy-Item -Recurse -Force "$publicDir\*" $OutputDir
}

Write-Host "[vault] Build complete: $OutputDir"
