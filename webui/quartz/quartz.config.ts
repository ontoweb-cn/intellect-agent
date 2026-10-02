// ── Quartz Configuration for Intellect LLM Wiki Vaults ────────────────────
// Copied into ~/.quartz/project on every build by quartz/build.sh.
//
// Environment (set by build.sh / WebUI):
//   QUARTZ_SITE_TITLE  — page title and site name
//   QUARTZ_BASE_PATH   — URL path prefix (e.g. "/vault/m/alice")
//   QUARTZ_BASE_HOST   — host:port for absolute URLs (e.g. "127.0.0.1:9119")
// ──────────────────────────────────────────────────────────────────────────

import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

const stripSlashes = (value: string) => value.replace(/^\/+|\/+$/g, "")
const host = (process.env.QUARTZ_BASE_HOST || "127.0.0.1:9119").trim()
const pathPart = stripSlashes(process.env.QUARTZ_BASE_PATH || "vault")
const baseUrl = pathPart ? `${host}/${pathPart}` : host
const title = process.env.QUARTZ_SITE_TITLE || "LLM Wiki"

const config: QuartzConfig = {
  configuration: {
    pageTitle: title,
    pageTitleSuffix: "",
    enableSPA: true,
    enablePopovers: true,
    analytics: null,
    locale: "zh-CN",
    baseUrl,
    ignorePatterns: [
      "raw/**",
      "graphify-out/**",
      "log*.md",
      "SCHEMA.md",
      "templates/**",
      ".obsidian/**",
      ".trash/**",
      ".git/**",
      "node_modules/**",
    ],
    defaultDateType: "modified",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        header: "Schibsted Grotesk",
        body: "Source Sans Pro",
        code: "IBM Plex Mono",
      },
      colors: {
        lightMode: {
          light: "#faf8f8",
          lightgray: "#f0efef",
          gray: "#b8b8b8",
          darkgray: "#4e4e4e",
          dark: "#2b2b2b",
          secondary: "#284b63",
          tertiary: "#84a59d",
          highlight: "rgba(143, 159, 169, 0.15)",
          textHighlight: "#fff23688",
        },
        darkMode: {
          light: "#1a1a2e",
          lightgray: "#252537",
          gray: "#646464",
          darkgray: "#d4d4d4",
          dark: "#ebebec",
          secondary: "#7b97aa",
          tertiary: "#84a59d",
          highlight: "rgba(143, 159, 169, 0.15)",
          textHighlight: "#b3aa0288",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "git", "filesystem"],
      }),
      Plugin.SyntaxHighlighting({
        theme: {
          light: "github-light",
          dark: "github-dark",
        },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({
        enableSiteMap: true,
        enableRSS: true,
      }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.Favicon(),
      Plugin.NotFoundPage(),
    ],
  },
}

export default config
