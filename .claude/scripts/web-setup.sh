#!/usr/bin/env bash
# Claude Code web — Setup script for dap_node_red
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

# --- Node.js LTS (required for Node-RED) --------------------------------------
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
  apt-get install -y --no-install-recommends nodejs
fi

cd "$REPO"

# --- Install npm dependencies if package.json exists --------------------------
if [ -f "package.json" ]; then
  npm ci || npm install || echo "[web-setup] WARNING: npm install failed"
fi

# --- Podman for local container testing (per CLAUDE.md) -----------------------
if ! command -v podman >/dev/null 2>&1; then
  apt-get update -o Acquire::AllowReleaseInfoChange::Label=true
  apt-get install -y --no-install-recommends podman podman-compose
fi

echo "[web-setup] Setup complete for dap_node_red"