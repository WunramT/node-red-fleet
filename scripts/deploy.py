#!/usr/bin/env python3
"""Deploy a flow to a Node-RED instance through the Admin API.

    python3 scripts/deploy.py --instance wag-prod --dry-run              # diff and rev
    python3 scripts/deploy.py --instance wag-prod --expect-rev <rev>     # write

Runs **on the target host**: Jenkins ships it over SSH and executes it there,
because port publishing is inconsistent across the estate and the sites sit in
separate subnets (decision 10).

A `409` from POST aborts, always. It means the running flow diverged from Git —
someone edited in the browser — and overwriting that destroys work. There is no
--force, and adding one would undo decision 3. Recovery: docs/runbook.md.

`--expect-rev` is what makes that abort reachable. Without it the run reads the
current rev and posts against it moments later, so an edit made before the run
is already inside that rev and the POST flattens it. The rev has to be the one
a human reviewed in the dry run, not the one this run just read.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodered import (  # noqa: E402
    ROOT, base_url, env_credentials, find, get_token, instances, request,
)
from normalize import normalize, render  # noqa: E402


def deploy(inst: dict, dry_run: bool, expect_rev: str | None = None) -> int:
    name = inst["name"]
    app = inst.get("app")
    if not app:
        print(f"{name}: no app — nothing to deploy")
        return 0

    flow_file = ROOT / "apps" / app / "flows.json"
    desired = json.loads(flow_file.read_text(encoding="utf-8"))

    admin_root = inst.get("admin_root") or ""
    base = base_url(inst)
    print(f"{name}: {base}{admin_root}/flows")

    token = None
    creds = env_credentials(inst["auth_credential_id"])
    if creds:
        token = get_token(base, admin_root, *creds)
    else:
        # wfm-prod has adminAuth switched off, so there is nothing to authenticate
        # against until that is fixed (decision 13). Say so rather than failing
        # silently into an unauthenticated deploy.
        print(f"{name}: no credentials in the environment for "
              f"{inst['auth_credential_id']} — continuing unauthenticated")

    status, current = request(f"{base}{admin_root}/flows", token=token)
    rev = (current or {}).get("rev")
    running = (current or {}).get("flows", [])
    if rev is None:
        raise SystemExit(f"{name}: GET /flows returned no rev (status {status}) — "
                         "the instance may predate the v2 Admin API")

    if expect_rev and rev != expect_rev:
        print(f"\n{name}: CONFLICT. The instance is at rev {rev}, and the deploy was "
              f"approved for {expect_rev}.\n"
              f"Something changed it in between — a browser deploy, or another run.\n"
              f"Whatever it was is not in Git, and this would flatten it.\n"
              f"Capture it first: docs/runbook.md, 'On 409'.", file=sys.stderr)
        return 2

    before = render(normalize(running))
    after = render(normalize(desired))

    if before == after:
        print(f"{name}: already up to date ({len(desired)} nodes)")
        return 0

    diff = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"running/{name}", tofile=f"git/apps/{app}", lineterm="", n=3,
    ))
    changed = sum(1 for line in diff if line.startswith(("+", "-"))
                  and not line.startswith(("+++", "---")))
    print(f"{name}: {changed} changed lines, rev {rev}")

    if dry_run:
        print("\n".join(diff))
        return 0

    try:
        status, _ = request(
            f"{base}{admin_root}/flows", method="POST", token=token,
            body={"rev": rev, "flows": desired},
            headers={"Node-RED-Deployment-Type": "flows"},
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            print(f"\n{name}: CONFLICT. The running flow has changed since rev {rev} — "
                  f"someone edited it in the browser.\n"
                  f"That edit is not in Git and deploying would destroy it.\n"
                  f"Recover it first: docs/runbook.md, 'On 409'.", file=sys.stderr)
            return 2
        raise

    print(f"{name}: deployed ({status})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--instance", help="registry.yml name, e.g. wag-prod")
    target.add_argument("--all", action="store_true", help="every instance with an app")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the diff and exit 0, changing nothing")
    ap.add_argument("--expect-rev",
                    help="the rev a dry run reported. Refuse if the instance has moved "
                         "since — without this a deploy overwrites whatever it finds")
    args = ap.parse_args()

    if args.expect_rev and args.all:
        raise SystemExit("--expect-rev is one instance's rev, so it cannot go with --all")

    chosen = [find(args.instance)] if args.instance else [
        i for i in instances() if i.get("app")]

    if not (args.dry_run or args.expect_rev):
        print("note: no --expect-rev, so this deploy overwrites whatever the instance "
              "holds, including a browser edit made before it started.", file=sys.stderr)

    worst = 0
    for inst in chosen:
        worst = max(worst, deploy(inst, args.dry_run, args.expect_rev))
    return worst


if __name__ == "__main__":
    sys.exit(main())
