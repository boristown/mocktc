#!/usr/bin/env bash
#
# Deploy Mock Teamcenter on the ECC host (erphost).
# - installs a standalone Python 3.11 into /oracle (does NOT touch the global
#   python), creates a venv, installs dependencies
# - installs and enables mocktc.service + frpc-mocktc.service (auto-start on boot)
#
# Run from the repo root on the target host:
#   bash scripts/deploy.sh
#
set -euo pipefail

APP_ROOT=/oracle/mocktc
PY_ROOT=/oracle/python311
PY_BIN="$PY_ROOT/python/bin/python3.11"
FRP_DIR=/home/orae7p/frp_0.61.2_linux_amd64
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260807/cpython-3.11.15%2B20260807-x86_64-unknown-linux-gnu-install_only.tar.gz"
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

mkdir -p "$APP_ROOT/data"

# 1. standalone python (idempotent, keeps global python untouched)
if [[ ! -x "$PY_BIN" ]]; then
  echo "==> installing standalone python 3.11 into $PY_ROOT"
  tmp=$(mktemp /oracle/py.XXXXXX.tar.gz)
  curl -fL -m 300 -o "$tmp" "$PY_URL"
  mkdir -p "$PY_ROOT"
  tar -xzf "$tmp" -C "$PY_ROOT"
  rm -f "$tmp"
fi
"$PY_BIN" --version

# 2. venv + dependencies
if [[ ! -x "$APP_ROOT/venv/bin/python" ]]; then
  echo "==> creating venv"
  "$PY_BIN" -m venv "$APP_ROOT/venv"
fi
"$APP_ROOT/venv/bin/pip" install --upgrade pip >/dev/null
"$APP_ROOT/venv/bin/pip" install -q -r "$REPO_ROOT/requirements.txt"

# 3. app files (repo -> runtime dir)
install -d -m 755 "$APP_ROOT/templates" "$APP_ROOT/static" "$APP_ROOT/fixtures"
install -m 644 "$REPO_ROOT/mocktc_app/app.py" "$APP_ROOT/app.py"
install -m 644 "$REPO_ROOT/mocktc_app/templates/"*.html "$APP_ROOT/templates/"
install -m 644 "$REPO_ROOT/mocktc_app/static/"*.css "$REPO_ROOT/mocktc_app/static/"*.js "$APP_ROOT/static/" 2>/dev/null || true
install -m 644 "$REPO_ROOT/mocktc_app/fixtures/"*.json "$APP_ROOT/fixtures/" 2>/dev/null || true

# 4. systemd units + auto-start
install -m 644 "$REPO_ROOT/systemd/mocktc.service" /etc/systemd/system/mocktc.service
systemctl daemon-reload
systemctl enable mocktc.service >/dev/null 2>&1
systemctl restart mocktc.service

# 5. FRP client config + auto-start
if [[ -x "$FRP_DIR/frpc" ]]; then
  if [[ ! -f "$FRP_DIR/frpc-mocktc.toml" ]]; then
    token=$(sed -nE 's/^[[:space:]]*auth\.token[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' \
      "$FRP_DIR/frpc-sap-ai-query-agent.toml" | head -n1)
    sed "s/@@FRP_AUTH_TOKEN@@/$token/" "$REPO_ROOT/scripts/frpc-mocktc.toml" \
      > "$FRP_DIR/frpc-mocktc.toml"
  fi
  install -m 644 "$REPO_ROOT/systemd/frpc-mocktc.service" /etc/systemd/system/frpc-mocktc.service
  systemctl daemon-reload
  systemctl enable frpc-mocktc.service >/dev/null 2>&1
  systemctl restart frpc-mocktc.service
fi

# 6. health check
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18120/tc/v1/health >/dev/null 2>&1; then
    echo "==> mocktc healthy on 127.0.0.1:18120"
    systemctl is-enabled mocktc.service
    systemctl is-enabled frpc-mocktc.service 2>/dev/null || true
    exit 0
  fi
  sleep 1
done

systemctl status mocktc.service --no-pager || true
echo "!! mocktc failed to become healthy" >&2
exit 1
