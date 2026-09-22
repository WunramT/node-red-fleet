#!/usr/bin/env bash
# Claude Code web — plugin marketplace + install provisioning. Shared by
# web-session-start.sh (must run every session — see PERSISTENCE below) and
# web-setup.sh (best-effort warm-up only, see the same caveat).
#
# WHY THIS EXISTS: .claude/settings.json declares the alex-plugins marketplace
# (https://gitlab.com/public-alex/agents/claude-plugins.git) and enables
# issue-workflow@alex-plugins, but as of Claude Code v2.1.195 a plugin sourced
# from a marketplace other than the official one only loads after an explicit
# `claude plugin install` — declaring it in settings.json does not install it.
# Nothing in the web bootstrap performs that install step on its own, so
# /issue-workflow:orchestrator silently never loads; `claude plugin list`
# reports no plugins installed despite settings.json enabling one.
#
# PERSISTENCE CAVEAT: ~/.claude/plugins is NOT among the snapshot-persisted
# paths (only /usr, /opt, /var/lib/docker, /root/.nuget, /root/.npm survive —
# see web-setup.sh's header), so running this only from web-setup.sh is not
# sufficient on its own; web-session-start.sh's invocation (every session) is
# the one that actually matters. Both call this script so the marketplace URL
# and plugin name live in exactly one place.
#
# ACTIVATION CAVEAT: the CLI documents "restart required to apply" for newly
# installed plugins, and /reload-plugins is disabled in this web sandbox — so
# a plugin installed here becomes available starting the NEXT session, not
# necessarily the one that installed it.
#
# Idempotent (marketplace add / plugin install no-op cleanly if already
# present) and non-fatal throughout: a plugin-provisioning hiccup should never
# block an otherwise-usable session.
set -uo pipefail

command -v claude >/dev/null 2>&1 || { echo "[provision-plugins] claude CLI not found — skipping."; exit 0; }

say() { echo "[provision-plugins] $*"; }

MARKETPLACE_URL="https://gitlab.com/public-alex/agents/claude-plugins.git"
PLUGIN="issue-workflow@alex-plugins"

say "adding alex-plugins marketplace ..."
claude plugin marketplace add "$MARKETPLACE_URL" >/tmp/plugin-marketplace-add.log 2>&1 \
  || say "WARNING: marketplace add failed (see /tmp/plugin-marketplace-add.log)"

say "installing $PLUGIN ..."
claude plugin install "$PLUGIN" --scope user >/tmp/plugin-install-issue-workflow.log 2>&1 \
  || say "WARNING: install of $PLUGIN failed (see /tmp/plugin-install-issue-workflow.log)"

say "done — a newly installed plugin activates starting next session (restart required; /reload-plugins is disabled in this sandbox)."
