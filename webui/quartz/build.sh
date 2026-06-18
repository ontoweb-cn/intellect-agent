#!/usr/bin/env bash
# Build a Quartz vault from a wiki directory.
# Called by: webui/api/routes.py and intellect_cli/vault_build.py
#
# Usage: build.sh <wiki_path> <output_dir> <title> <base_path>
#   wiki_path   - source markdown directory (e.g. ~/wiki)
#   output_dir  - where to write the static site
#   title       - site title
#   base_path   - base URL path (e.g. /vault or /vault/t/my-team)
set -euo pipefail

WIKI_PATH="${1:?wiki_path required}"
OUTPUT_DIR="${2:?output_dir required}"
TITLE="${3:-LLM Wiki}"
BASE_PATH="${4:-/vault}"

QUARTZ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUARTZ_REPO="$QUARTZ_DIR/_quartz"
QUARTZ_CONTENT="$QUARTZ_REPO/content"
QUARTZ_CONFIG="$QUARTZ_REPO/quartz.config.ts"
QUARTZ_LAYOUT="$QUARTZ_REPO/quartz.layout.ts"

# ── Ensure Quartz is installed ──────────────────────────────────────────
if [ ! -d "$QUARTZ_REPO/.git" ]; then
    echo "[vault] Cloning Quartz (one-time, ~50MB)..."
    git clone --depth 1 --branch v4 https://github.com/jackyzha0/quartz.git "$QUARTZ_REPO" 2>&1 || {
        echo "[vault] ERROR: Failed to clone Quartz repository"
        exit 1
    }
    echo "[vault] Installing Quartz npm dependencies..."
    cd "$QUARTZ_REPO"
    npm install --prefer-offline 2>&1 || {
        echo "[vault] ERROR: Failed to install Quartz dependencies"
        exit 1
    }
    echo "[vault] Quartz ready."
fi

# ── Symlink wiki content into Quartz content dir ────────────────────────
rm -rf "$QUARTZ_CONTENT"
ln -sf "$WIKI_PATH" "$QUARTZ_CONTENT"

# ── Generate quartz.config.ts if missing ─────────────────────────────────
if [ ! -f "$QUARTZ_CONFIG" ]; then
    cat > "$QUARTZ_CONFIG" << CFGEOF
import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

const config: QuartzConfig = {
    configuration: {
        pageTitle: "${TITLE}",
        baseUrl: "${BASE_PATH}",
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
                    light: "#faf8f8",
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
CFGEOF
fi

# ── Generate quartz.layout.ts if missing ────────────────────────────────
if [ ! -f "$QUARTZ_LAYOUT" ]; then
    cat > "$QUARTZ_LAYOUT" << LAYEOF
import { defaultLayout } from "./quartz/cfg"
export { defaultLayout }
LAYEOF
fi

# ── Build ────────────────────────────────────────────────────────────────
echo "[vault] Building Quartz site from ${WIKI_PATH} → ${OUTPUT_DIR}..."
cd "$QUARTZ_REPO"
npx quartz build --output "$OUTPUT_DIR" --concurrency 4 2>&1

# Copy built output if Quartz puts it elsewhere
if [ -d "$QUARTZ_REPO/public" ] && [ "$(realpath "$QUARTZ_REPO/public")" != "$(realpath "$OUTPUT_DIR")" ]; then
    rm -rf "$OUTPUT_DIR"
    cp -r "$QUARTZ_REPO/public" "$OUTPUT_DIR"
fi

echo "[vault] Build complete: $OUTPUT_DIR"
