# Intellect Agent — Build & Development Makefile
#
# Usage:
#   make rust-build      Build the Rust native extension (release)
#   make rust-dev        Build in development mode (faster, debug symbols)
#   make rust-check      Check Rust code compiles without building
#   make rust-test       Run Rust unit tests
#   make rust-clean      Clean Rust build artifacts
#   make install         Install intellect with Rust extension
#   make install-pure    Install without Rust extension (pure Python)

.PHONY: rust-build rust-dev rust-check rust-test rust-clean install install-pure help

RUST_CORE_DIR := rust-core

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Rust targets ──────────────────────────────────────────────────────────────

rust-build: ## Build Rust native extension (release)
	cd $(RUST_CORE_DIR) && maturin develop --release

rust-dev: ## Build Rust extension in dev mode (faster compile, debug)
	cd $(RUST_CORE_DIR) && maturin develop

rust-check: ## Check Rust code compiles
	cd $(RUST_CORE_DIR) && cargo check

rust-test: ## Run Rust unit tests
	cd $(RUST_CORE_DIR) && cargo test

rust-clean: ## Clean Rust build artifacts
	cd $(RUST_CORE_DIR) && cargo clean
	rm -f $(RUST_CORE_DIR)/target/wheels/*.whl

rust-wheel: ## Build a distributable wheel
	cd $(RUST_CORE_DIR) && maturin build --release

# ── Python targets ────────────────────────────────────────────────────────────

install: rust-build ## Install with Rust extension
	pip install -e .

install-pure: ## Install without Rust extension (pure Python)
	pip install -e .

# ── Development helpers ───────────────────────────────────────────────────────

check: rust-check ## Run all checks (Rust + Python syntax)
	@python3 -c "import ast; [ast.parse(open(f).read()) for f in \
		['cli.py', 'intellect_cli/main.py', 'intellect_state.py', \
		 'gateway/run.py', 'agent/storage/sqlite_backend.py']]" && \
	echo "Python syntax: OK"

fmt: ## Format Rust code
	cd $(RUST_CORE_DIR) && cargo fmt

clippy: ## Run Rust linter
	cd $(RUST_CORE_DIR) && cargo clippy -- -D warnings
