#!/usr/bin/env bash
# Smoke-test deploy/lightrag docker compose against a running LightRAG server.
#
# Usage:
#   scripts/smoke_lightrag_compose.sh              # health + plugin client
#   scripts/smoke_lightrag_compose.sh --up         # compose up -d then smoke
#   scripts/smoke_lightrag_compose.sh --full       # also insert/query round-trip
#   LIGHTRAG_COMPOSE_FILE=deploy/lightrag/docker-compose.webui.yml ...
#
# Full round-trip requires a working LLM+embedding backend in the server .env
# (OpenAI keys or Ollama reachable from the lightrag container).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_DIR="${LIGHTRAG_COMPOSE_DIR:-$ROOT/deploy/lightrag}"
COMPOSE_FILE="${LIGHTRAG_COMPOSE_FILE:-docker-compose.yml}"
BASE_URL="${LIGHTRAG_BASE_URL:-http://127.0.0.1:9621}"
DO_UP=0
DO_FULL=0
WORKSPACE="${LIGHTRAG_SMOKE_WORKSPACE:-smoke}"

for arg in "$@"; do
  case "$arg" in
    --up) DO_UP=1 ;;
    --full) DO_FULL=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '▶ %s\n' "$*"; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }
ok() { printf '✓ %s\n' "$*"; }

if [[ "$DO_UP" -eq 1 ]]; then
  log "Starting compose ($COMPOSE_FILE) in $COMPOSE_DIR"
  (cd "$COMPOSE_DIR" && docker compose -f "$COMPOSE_FILE" up -d lightrag 2>/dev/null) \
    || (cd "$COMPOSE_DIR" && docker compose -f "$COMPOSE_FILE" up -d)
fi

log "Waiting for $BASE_URL/health"
ready=0
for _ in $(seq 1 24); do
  if curl -sf "$BASE_URL/health" >/tmp/lightrag_health.json 2>/dev/null; then
    ready=1
    break
  fi
  sleep 5
done
[[ "$ready" -eq 1 ]] || fail "LightRAG /health not ready at $BASE_URL"

python3 - <<'PY' /tmp/lightrag_health.json
import json, sys
data = json.load(open(sys.argv[1]))
status = data.get("status", "")
assert status in ("healthy", "ok", "success"), f"unexpected status: {status!r}"
print(f"  server status: {status}")
print(f"  core_version: {data.get('core_version', '?')}")
print(f"  llm_binding: {(data.get('configuration') or {}).get('llm_binding', '?')}")
PY
ok "GET /health"

log "Plugin client health via LightRAGClientManager"
python3 - <<PY
import json, os, sys
sys.path.insert(0, "$ROOT")
os.environ.setdefault("INTELLECT_HOME", "/tmp/lightrag-smoke-home")
from pathlib import Path
home = Path(os.environ["INTELLECT_HOME"])
(home / "lightrag").mkdir(parents=True, exist_ok=True)
(home / "lightrag" / "config.json").write_text(
    json.dumps({"server": {"base_url": "$BASE_URL"}}),
    encoding="utf-8",
)
from plugins.rag.lightrag.client import LightRAGClientManager
mgr = LightRAGClientManager(json.loads((home / "lightrag" / "config.json").read_text()))
health = mgr.health()
mgr.shutdown()
print("  manager health:", health.get("status", health))
PY
ok "LightRAGClientManager.health()"

if [[ "$DO_FULL" -eq 1 ]]; then
  log "Full round-trip insert + query (workspace=$WORKSPACE)"
  python3 - <<PY
import json, os, sys, time
sys.path.insert(0, "$ROOT")
from plugins.rag.lightrag.client import LightRAGClient, LightRAGUnavailable

base_url = "$BASE_URL"
ws = "$WORKSPACE"
text = "Intellect compose smoke: the launch codename is Aurora-42."
client = LightRAGClient({"server": {"base_url": base_url}})
try:
    result = client.insert_text(text, workspace=ws, file_path="smoke.txt")
except LightRAGUnavailable as exc:
    print(f"insert failed: {exc}", file=sys.stderr)
    sys.exit(1)
print("  track_id:", result.get("track_id", ""))
deadline = time.time() + 120
while time.time() < deadline:
    docs = client.list_documents(workspace=ws)
    statuses = docs.get("statuses") or {}
    if statuses.get("processed"):
        break
    failed = statuses.get("failed") or []
    if failed:
        err = (failed[0] or {}).get("error_msg", "unknown")
        print(f"indexing failed: {err}", file=sys.stderr)
        sys.exit(1)
    time.sleep(3)
else:
    print("indexing timed out", file=sys.stderr)
    sys.exit(1)
data = client.query(
    "What is the launch codename?",
    workspace=ws,
    mode="naive",
    only_need_context=True,
)
client.close()
ctx = data.get("response") or ""
if "Aurora-42" not in ctx and "aurora" not in ctx.lower():
    print("query context missing codename:", ctx[:500], file=sys.stderr)
    sys.exit(1)
print("  query context contains codename")
PY
  ok "insert → index → search round-trip"
fi

ok "LightRAG compose smoke passed"
