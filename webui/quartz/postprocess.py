"""Post-process Quartz build output: add .html to internal links, force light color-scheme."""
import re
import sys
from pathlib import Path


def main(output_dir: str) -> None:
    output = Path(output_dir)
    if not output.is_dir():
        print(f"ERROR: output directory not found: {output_dir}", file=sys.stderr)
        sys.exit(1)

    counts = {'links': 0, 'theme': 0}
    ext_pattern = re.compile(r'\.[a-zA-Z0-9]+$')
    link_pattern = re.compile(r'href="(\.\.?/[^"]+)"')

    # Process .html files: fix internal links
    for html_file in output.rglob('*.html'):
        content = html_file.read_text(encoding='utf-8')

        def fix_link(m: re.Match) -> str:
            path = m.group(1)
            if '#' in path or '://' in path:
                return m.group(0)
            if ext_pattern.search(path.split('/')[-1]):
                return m.group(0)
            counts['links'] += 1
            return 'href="' + path + '.html"'

        content = link_pattern.sub(fix_link, content)
        html_file.write_text(content, encoding='utf-8')

    # Process .css files: force color-scheme to light, override dark theme vars
    for css_file in output.rglob('*.css'):
        content = css_file.read_text(encoding='utf-8')
        changed = False

        if 'color-scheme:dark' in content:
            content = content.replace('color-scheme:dark', 'color-scheme:light')
            changed = True

        # Override dark mode CSS variables to match light mode
        # The [saved-theme=dark] block may still have dark values from older builds
        dark_block_pattern = re.compile(r'\[saved-theme=dark\]\{[^}]*\}')
        def fix_dark_block(m: re.Match) -> str:
            block = m.group(0)
            # Replace dark color values with light ones
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
            changed = True
            content = new_content

        if changed:
            counts['theme'] += 1
            css_file.write_text(content, encoding='utf-8')

    # Override the JS theme detection to default to light mode
    for js_file in output.rglob('*.js'):
        content = js_file.read_text(encoding='utf-8')
        changed = False

        # In prescript.js: change the default theme detection to force light
        # Pattern: var d=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"
        old_js = 'window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"'
        new_js = 'window.matchMedia("(prefers-color-scheme: light)").matches?"light":"light"'
        if old_js in content:
            content = content.replace(old_js, new_js)
            changed = True

        if changed:
            js_file.write_text(content, encoding='utf-8')
            counts['theme'] += 1

    print(f"[vault] Post-process: fixed {counts['links']} links, {counts['theme']} theme overrides")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python postprocess.py <output_dir>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
