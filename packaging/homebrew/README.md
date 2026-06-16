Homebrew packaging notes for Intellect Agent.

Use `packaging/homebrew/intellect-agent.rb` as a tap or `homebrew-core` starting point.

## Key choices

- Stable builds should target the semver-named sdist asset attached to each GitHub release, not the CalVer tag tarball.
- `faster-whisper` now lives in the `voice` extra, which keeps wheel-only transitive dependencies out of the base Homebrew formula.
- The wrapper exports `INTELLECT_BUNDLED_SKILLS`, `INTELLECT_OPTIONAL_SKILLS`, and `intellect_MANAGED=homebrew` so packaged installs keep runtime assets and defer upgrades to Homebrew.

## Rust extension (required since v0.6.2)

Intellect Agent requires the `intellect_community_core` PyO3 extension at runtime. The formula must either:

1. **Preferred (target state):** depend on a PyPI `intellect-community-core` bottle matching the pinned version in `pyproject.toml`, or
2. **Build from source:** add `depends_on "rust" => :build`, install `maturin` in the venv, and run `maturin develop --release` in `rust-core/` before `pip install` of the main package.

See `docs/packaging/macos.md` and `docs/packaging/design.md` §4.4 for the full design.

Without Rust, `intellect` will fail at startup when importing sandbox/storage/crypto modules.

## Typical update flow

1. Bump the formula `url`, `version`, and `sha256`.
2. Refresh Python resources with `brew update-python-resources --print-only intellect-agent`.
3. Keep `ignore_packages: %w[certifi cryptography pydantic]`.
4. Add Rust build step if not using PyPI Rust wheel.
5. Verify `brew audit --new --strict intellect-agent` and `brew test intellect-agent`.

## Cross-references

- Platform packaging design: `docs/packaging/design.md`
- Release checklist: `docs/packaging/maintainer-release.md`
- Artifact manifest: `packaging/manifests/artifacts.yaml`
