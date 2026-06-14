# Security Architecture

Intellect Agent executes user-approved shell commands and Python code. This
document describes the multi-layer defense system.

## Overview

```
User / LLM command
  → NFKC normalization + ANSI strip + lowercase
  → Layer 1: Rust RegexSet (69 patterns, O(n) single-pass)
  → Layer 2: Python AST analysis (structural code check)
  → Approval state machine (yolo / smart / ask / cron)
  → Execute or block
```

## Layer 1 — Rust Regex (always on)

**File:** `rust-core/src/sandbox.rs` — 69 patterns in two tiers.

### Hardline (14 patterns) — unconditional blocks

Cannot be bypassed by any approval mode:
`rm -rf /`, `shutdown`, `reboot`, `mkfs`, `dd of=/dev/sd*`, fork bombs, `kill -9 -1`.

### Dangerous (55 patterns) — requires user approval

Covers: recursive delete, chmod 777, SQL DROP/DELETE/TRUNCATE, curl|sh,
file overwrite (tee/redirect), git force push, docker lifecycle, sudo -S,
SSH authorized_keys overwrite, block device write, script execution (31 tokens).

### Script Execution Token Registry

31 Python/Perl/Ruby/Node APIs in `SCRIPT_EXEC_TOKEN_ENTRIES`, each with
individual description:

```
Code execution      — exec(), eval(), __import__()
Subprocess spawn    — os.system(), os.popen(), os.exec*(), os.spawn*(),
                      os.posix_spawn*(), subprocess
File destruction    — os.remove(), os.unlink(), os.rmdir(), shutil.rmtree(),
                      Path.rmdir(), Path.unlink()
Destructive writes  — Path.write_bytes(), Path.write_text(),
                      open(..., 'w'/'a'/'wb'/'w+')
Native lib loading  — ctypes
Deserialization     — pickle, marshal
Dynamic compilation — compile()
Network exfil       — urllib, requests, socket
Dynamic access      — getattr(), __dict__[]
```

Adding a token: append one line to `SCRIPT_EXEC_TOKEN_ENTRIES` in `sandbox.rs`.

## Layer 2 — AST Analysis

**File:** `tools/approval.py` → `_check_python_ast()`

For `execute_code` invocations, parses Python code and walks the AST:

- Direct calls: `exec()`, `eval()`, `compile()`
- Attribute calls: `os.system()`, `subprocess.call()`, `pickle.loads()`
- getattr obfuscation: `getattr(__builtins__, "exec")("id()")`
- `__dict__[]` access: `os.__dict__["system"]("id")`
- importlib chains: `importlib.import_module("os").system("id")`
- Dangerous imports: `import os`, `from subprocess import call`

AST detection triggers **auto-deny** in all contexts.

## Layer 3 — Path & Network Guards

| Module | Protection |
|--------|-----------|
| `path_security.py` | Blocks system files, SSH keys, credentials |
| `url_safety.py` | SSRF: private IPs, loopback, cloud metadata |
| `file_tools.py` | Write guard: `/etc/`, `/boot/`, docker socket |

## Coverage

30 attack vectors: 23 caught by both layers, 4 regex-only, 1 AST-only (+6 import detections).
18 adversarial payloads tested: **0 bypasses**.

## Performance

| Operation | Latency |
|-----------|---------|
| RegexSet (69 patterns) | ~10 µs |
| AST parse + walk | ~27 µs |
| Full pipeline | ~8 µs (AST skipped when regex passes) |

## Reporting Issues

Open an issue at https://gitee.com/ontoweb/intellect-agent/issues.
Do NOT disclose bypasses publicly before a fix is available.
