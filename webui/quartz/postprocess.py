"""Post-process Quartz build output: fix links, force light theme, disable SPA nav."""
import re
import sys
from pathlib import Path


def main(output_dir: str) -> None:
    output = Path(output_dir)
    if not output.is_dir():
        print(f"ERROR: output directory not found: {output_dir}", file=sys.stderr)
        sys.exit(1)

    counts = {'links': 0, 'theme': 0, 'spa': 0}
    ext_pattern = re.compile(r'\.[a-zA-Z0-9]+$')
    link_pattern = re.compile(r'href="(\.\.?/[^"]+)"')

    # ── Process .html files ──────────────────────────────────────────────
    for html_file in output.rglob('*.html'):
        content = html_file.read_text(encoding='utf-8')
        changed = False

        # 1. Add .html extension to internal links
        def fix_link(m: re.Match) -> str:
            path = m.group(1)
            if '#' in path or '://' in path:
                return m.group(0)
            if ext_pattern.search(path.split('/')[-1]):
                return m.group(0)
            counts['links'] += 1
            return 'href="' + path + '.html"'

        new_content = link_pattern.sub(fix_link, content)
        if new_content != content:
            content = new_content
            changed = True

        # 2. Add <meta name="color-scheme" content="light"> to force light mode
        if '<head>' in content and 'name="color-scheme"' not in content:
            content = content.replace(
                '<head>',
                '<head><meta name="color-scheme" content="light"/>',
            )
            counts['theme'] += 1
            changed = True

        if changed:
            html_file.write_text(content, encoding='utf-8')

    # ── Process .css files ───────────────────────────────────────────────
    for css_file in output.rglob('*.css'):
        content = css_file.read_text(encoding='utf-8')
        changed = False

        # 3. Fix color-scheme
        if 'color-scheme:dark' in content:
            content = content.replace('color-scheme:dark', 'color-scheme:light')
            changed = True
            counts['theme'] += 1

        # 4. Override dark mode CSS variables with light values
        dark_block_pattern = re.compile(r'\[saved-theme=dark\]\{[^}]*\}')

        def fix_dark_block(m: re.Match) -> str:
            block = m.group(0)
            block = re.sub(r'--light:#[0-9a-fA-F]{3,6}', '--light:#fff', block)
            block = re.sub(r'--lightgray:#[0-9a-fA-F]{3,6}', '--lightgray:#e5e5e5', block)
            block = re.sub(r'--dark:#[0-9a-fA-F]{3,6}', '--dark:#2b2b2b', block)
            block = re.sub(r'--darkgray:#[0-9a-fA-F]{3,6}', '--darkgray:#4e4e4e', block)
            block = re.sub(r'--gray:#[0-9a-fA-F]{3,6}', '--gray:#b8b8b8', block)
            block = re.sub(r'--secondary:#[0-9a-fA-F]{3,6}', '--secondary:#284b63', block)
            block = re.sub(r'--tertiary:#[0-9a-fA-F]{3,6}', '--tertiary:#84a59d', block)
            return block

        new_content = dark_block_pattern.sub(fix_dark_block, content)
        if new_content != content:
            content = new_content
            changed = True

        if changed:
            css_file.write_text(content, encoding='utf-8')

    # ── Process .js files ────────────────────────────────────────────────
    for js_file in output.rglob('*.js'):
        content = js_file.read_text(encoding='utf-8')
        changed = False

        # 5. Force JS default theme to light
        old_default = 'window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"'
        new_default = '"light"/*always-light*/'
        if old_default in content:
            content = content.replace(old_default, new_default)
            changed = True
            counts['theme'] += 1

        # 6. Neutralize SPA navigation in postscript.js
        # The SPA router uses addEventListener("nav", ...) and fetches pages.
        # Remove the router's popstate listener and navigation interception.
        if 'addEventListener("popstate"' in content:
            content = content.replace(
                'addEventListener("popstate"',
                'addEventListener("popstate.disabled"',
            )
            changed = True
            counts['spa'] += 1

        # Remove the click delegation that intercepts link clicks for SPA
        # Pattern: document.addEventListener("click",...router...)
        spa_click = 'document.addEventListener("click",'
        if spa_click in content and 'closest' in content:
            # Don't fully remove (it handles other things), just check
            pass

        if changed:
            js_file.write_text(content, encoding='utf-8')

    print(
        f"[vault] Post-process: fixed {counts['links']} links, "
        f"{counts['theme']} theme overrides, "
        f"{counts['spa']} spa disabled"
    )


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python postprocess.py <output_dir>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
