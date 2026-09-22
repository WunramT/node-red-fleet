#!/usr/bin/env bash
# Claude Code web — per-session bring-up. Wired via the .claude/settings.json
# SessionStart hook (startup|resume).
#
# Responsible ONLY for runtime state that does NOT survive into the cached
# snapshot and must be re-established every session:
#   1. the docker daemon (the web VM has docker but no systemd to start it)
#   2. the dev (30xxx) + test (31xxx) compose stacks (Postgres, NATS, telemetry)
#   3. a sanity check that the Playwright browser is baked in (self-heals if not)
#   4. plugin marketplace + install provisioning (see provision-plugins.sh —
#      ~/.claude/plugins is not a persisted snapshot path, so this must run
#      every session, not just once at setup time)
#
# Everything expensive and cacheable (toolchain, NuGet warm-up, browser binary,
# image pulls) lives in web-setup.sh so sessions start fast with no late-stage
# installs. This script no-ops outside the web sandbox, so the local
# podman/devcontainer flow is untouched.
set -euo pipefail

# Only run in the Claude Code web sandbox.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# Resolve the repo root from THIS script's location, not from $CLAUDE_PROJECT_DIR:
# that var is only guaranteed inside the SessionStart hook environment, and the
# script must also be correct when run directly (validation) or from any cwd.
SCRIPTS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="$(cd -- "$SCRIPTS/../.." && pwd)"
cd "$REPO"

say() { echo "[web-session-start] $*"; }

# --- 1. Docker daemon --------------------------------------------------------
# The web VM ships docker but is not booted with systemd, so nothing starts the
# daemon. Bring it up ourselves and wait for the socket before any compose call
# (the previous script skipped this, so `compose up` failed and infra never came
# up — while the banner still claimed "ready").
ensure_dockerd() {
  if docker info >/dev/null 2>&1; then
    say "docker daemon already running."
    return 0
  fi
  say "starting docker daemon (no systemd on this VM) ..."
  nohup dockerd >/tmp/dockerd.log 2>&1 &
  disown
  local i
  for i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      say "docker daemon ready."
      return 0
    fi
    sleep 1
  done
  say "ERROR: docker daemon did not become ready in 30s (see /tmp/dockerd.log)"
  return 1
}

# --- 3. Playwright browser sanity check (defined here, run last) -------------
# The browser binary is baked into the snapshot by web-setup.sh under
# PLAYWRIGHT_BROWSERS_PATH (a persistent system path). Here we only VERIFY the
# build the repo pins (read cheaply from the NuGet-cached browsers.json — no
# project build). Self-heal only if it is genuinely missing, e.g. the setup
# script was never configured or the cache expired.
verify_playwright() {
  local bpath="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
  local bj rev hs_dir cr_dir
  bj="$(find /root/.nuget/packages/microsoft.playwright -name browsers.json 2>/dev/null | head -1 || true)"
  if [ -z "$bj" ]; then
    say "Playwright package not in NuGet cache yet — skipping browser check (build will restore it)."
    return 0
  fi
  # First "chromium" revision in browsers.json is the pinned Chromium build.
  rev="$(grep -A1 '"name": "chromium"' "$bj" | grep -oE '"revision": "[0-9]+"' | grep -oE '[0-9]+' | head -1)"
  cr_dir="$bpath/chromium-$rev"
  hs_dir="$bpath/chromium_headless_shell-$rev"
  if [ -f "$cr_dir/INSTALLATION_COMPLETE" ] && [ -f "$hs_dir/INSTALLATION_COMPLETE" ]; then
    say "Playwright chromium-$rev present (baked in snapshot)."
    return 0
  fi
  say "Playwright chromium-$rev missing — self-healing (this should normally be baked by web-setup.sh) ..."
  # Non-fatal: a browser hiccup should not block an otherwise-usable session
  # (infra is already up). E2E will surface the problem clearly if it persists.
  bash "$SCRIPTS/ensure-playwright-browsers.sh" \
    || say "WARNING: Playwright provisioning failed — E2E tests will not run until resolved."
}

# --- Run ----------------------------------------------------------------------
say "bringing up session infrastructure ..."
ensure_dockerd

# 2. Compose stacks. The start scripts auto-detect the runtime (podman locally,
#    docker here) and block until Postgres + NATS report healthy; they exit
#    non-zero on failure, and set -e propagates that instead of a false "ready".
#      dev  (30xxx) backs manual Playwright validation (Development/http profile)
#      test (31xxx) backs `dotnet test` and `--launch-profile test`
say "starting dev stack (30xxx) ..."
"$REPO/environment/scripts/start.sh"
say "starting test stack (31xxx) ..."
"$REPO/environment/scripts/start-test.sh"

verify_playwright

# 4. Plugin marketplace + install provisioning (see provision-plugins.sh for
#    why this must run every session, not just once at setup time).
bash "$SCRIPTS/provision-plugins.sh" \
  || say "WARNING: plugin provisioning failed (see /tmp/plugin-*.log)"

# --- Local tool manifest (csharpier). The tool binaries are cached in the
#     persistent /root/.nuget, but the manifest restore state lives in the
#     freshly-cloned repo tree, so `dotnet csharpier` fails until restored once
#     per session. Fast and offline from the warm cache. Non-fatal. -----------
say "restoring local dotnet tools (csharpier) ..."
dotnet tool restore >/tmp/dotnet-tool-restore.log 2>&1 \
  || say "WARNING: dotnet tool restore failed (see /tmp/dotnet-tool-restore.log)"

say "session bring-up complete — dev + test stacks healthy (see per-step lines above for Playwright/tool status)."
