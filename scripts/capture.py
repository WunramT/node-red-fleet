#!/usr/bin/env python3
"""Pull the running flow of an instance into its app directory.

    python3 scripts/capture.py --instance wag-prod
    python3 scripts/capture.py --instance wag-prod --dry-run

This is the return path. Somebody edits in the browser, and this brings that
edit into Git where it can be reviewed and deployed like anything else. It is
also the recovery from a 409: the conflicting edit is captured, not discarded.

It writes to the repository. It never writes to an instance.

Same reach and credentials as deploy.py — see that script's docstring.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodered import (  # noqa: E402
    ROOT, base_url, env_credentials, find, get_token, request,
)
from normalize import normalize, render  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the diff and write nothing")
    args = ap.parse_args()

    inst = find(args.instance)
    app = inst.get("app")
    if not app:
        raise SystemExit(f"{inst['name']} has no app — nothing to capture into. "
                         f"Give it one in registry.yml first.")

    admin_root = inst.get("admin_root") or ""
    base = base_url(inst)
    creds = env_credentials(inst["auth_credential_id"])
    token = get_token(base, admin_root, *creds) if creds else None
    _, payload = request(f"{base}{admin_root}/flows", token=token)

    running = (payload or {}).get("flows", [])
    if not running:
        raise SystemExit(f"{inst['name']}: the instance returned no flow. "
                         f"Capturing that would empty apps/{app}/flows.json.")

    target = ROOT / "apps" / app / "flows.json"
    after = render(normalize(running))
    before = target.read_text(encoding="utf-8") if target.exists() else ""

    if before == after:
        print(f"{inst['name']}: apps/{app}/flows.json already matches "
              f"({len(running)} nodes) — nothing to capture")
        return 0

    diff = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/apps/{app}/flows.json", tofile=f"b/apps/{app}/flows.json",
        lineterm="", n=3))
    print("\n".join(diff))
    changed = sum(1 for line in diff if line.startswith(("+", "-"))
                  and not line.startswith(("+++", "---")))

    if args.dry_run:
        print(f"\n{inst['name']}: {changed} lines would change. Nothing written.")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8")
    print(f"\n{inst['name']}: wrote apps/{app}/flows.json — {changed} changed lines, "
          f"{len(running)} nodes.\nReview it, then commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
