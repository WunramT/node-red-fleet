#!/usr/bin/env bash
# Claude Code web — reliable Playwright browser provisioning.
#
# Sourced/called by both web-setup.sh (bakes browsers into the cached snapshot)
# and web-session-start.sh (verifies, and self-heals if the snapshot is missing
# them). Idempotent — a fully provisioned browser is a fast no-op.
#
# WHY THIS EXISTS (not just `playwright.ps1 install chromium`):
#   The repo pins Microsoft.Playwright 1.57 (Chromium build v1200) but the web
#   base image only pre-installs an older build. Playwright's own downloader is
#   unreliable through the egress proxy: cdn.playwright.dev truncates the
#   ~165 MB stream mid-download ("server closed connection"), and the
#   playwright.download.prss.microsoft.com fallback returns 400. A plain
#   `curl -L` follows the CDN's 307 redirect to storage.googleapis.com
#   (chrome-for-testing) and downloads the exact same artifact reliably, with
#   resume. We extract it into the registry layout Playwright expects and drop
#   the INSTALLATION_COMPLETE marker so Playwright treats it as installed.
#
# Browsers live under PLAYWRIGHT_BROWSERS_PATH (/opt/pw-browsers on the web VM),
# which is a system path that persists in the snapshot — unlike the repo tree,
# which is re-cloned fresh every session.
set -euo pipefail

# Resolve the repo root from THIS script's location (works from any cwd, and
# whether or not $CLAUDE_PROJECT_DIR is set — it is not guaranteed in the setup
# phase that calls this helper).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
SYSTEST_DIR="$REPO/src/Polipol.PA.SystemTests"

log() { echo "[playwright] $*"; }

# Trim leading/trailing whitespace (dry-run output is space-padded).
trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# Locate the generated playwright.ps1 driver. It lives in the SystemTests build
# output (ephemeral repo tree), so build the project if it isn't there yet.
find_playwright_ps1() {
  local ps1
  ps1="$(ls "$SYSTEST_DIR"/bin/*/net*/playwright.ps1 2>/dev/null | head -1 || true)"
  if [ -z "$ps1" ]; then
    log "playwright.ps1 not found — building $SYSTEST_DIR ..."
    dotnet build "$SYSTEST_DIR" >/tmp/playwright-systests-build.log 2>&1 \
      || { log "SystemTests build failed (see /tmp/playwright-systests-build.log)"; return 1; }
    ps1="$(ls "$SYSTEST_DIR"/bin/*/net*/playwright.ps1 2>/dev/null | head -1 || true)"
  fi
  [ -n "$ps1" ] && printf '%s\n' "$ps1"
}

# A browser install dir counts as present if it has the INSTALLATION_COMPLETE
# marker, or is a non-empty pre-baked dir (e.g. the base image's ffmpeg).
is_installed() {
  local dir="$1"
  [ -f "$dir/INSTALLATION_COMPLETE" ] && return 0
  [ -d "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ] && return 0
  return 1
}

# Download (with redirect-follow + resume) and extract one browser into its
# registry dir, then mark it installed. Extract to a temp dir and move into
# place so an interrupted run never leaves a half-populated install dir.
provision_one() {
  local url="$1" dir="$2" name; name="$(basename "$dir")"
  if is_installed "$dir"; then
    log "$name already present — skipping."
    return 0
  fi
  log "provisioning $name from $url"
  local zip tmp; zip="$(mktemp /tmp/pw-"$name"-XXXXXX.zip)"
  local ok=0 i
  for i in 1 2 3 4 5; do
    if curl -fsSL -C - -o "$zip" --max-time 180 "$url"; then ok=1; break; fi
    log "download attempt $i incomplete ($(stat -c%s "$zip" 2>/dev/null || echo 0) bytes) — resuming"
    sleep 2
  done
  [ "$ok" = 1 ] || { log "FAILED to download $name after retries"; rm -f "$zip"; return 1; }

  tmp="$(mktemp -d /tmp/pw-"$name"-XXXXXX.d)"
  unzip -q -o "$zip" -d "$tmp"
  rm -f "$zip"
  rm -rf "$dir"; mkdir -p "$(dirname "$dir")"; mv "$tmp" "$dir"
  # Playwright's own zips are extracted as-is (native chrome-for-testing layout,
  # e.g. chrome-linux64/chrome) — this matches the 1.57 registry for linux-x64.
  touch "$dir/INSTALLATION_COMPLETE" "$dir/DEPENDENCIES_VALIDATED"
  log "$name provisioned."
}

ensure_playwright_browsers() {
  mkdir -p "$BROWSERS_PATH"
  local ps1; ps1="$(find_playwright_ps1)" || return 1

  # Ask Playwright itself which builds it needs and where — authoritative and
  # version-agnostic, so a Playwright bump needs no edits here. Parse the
  # (Install location, first Download url) pairs from `install --dry-run`.
  local dryrun; dryrun="$(pwsh "$ps1" install --dry-run chromium chromium-headless-shell 2>/dev/null)"
  local loc="" url="" rc=0
  while IFS= read -r line; do
    case "$line" in
      *"Install location:"*) loc="$(trim "${line#*:}")"; url="" ;;
      *"Download url:"*)
        if [ -n "$loc" ] && [ -z "$url" ]; then
          url="$(trim "${line#*:}")"
          provision_one "$url" "$loc" || rc=1
          loc=""
        fi ;;
    esac
  done <<< "$dryrun"
  return $rc
}

# Allow running standalone as well as being sourced.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  ensure_playwright_browsers
fi
