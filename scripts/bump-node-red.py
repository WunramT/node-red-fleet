#!/usr/bin/env python3
"""Move one or more instances to another Node-RED version.

    python3 scripts/bump-node-red.py --to 5.0.1 --instance cho-test
    python3 scripts/bump-node-red.py --to 5.0.1 --all --dry-run

The version of an instance lives in two places that must agree: the
Dockerfile's FROM, which is what CI builds, and registry.yml's image_tag,
which is what the deploy pins and what `nr.py edit` runs locally. This edits
both or neither. The palette build resets to 1, because it counts builds of
that palette on that version.

Reaching an instance still means the palette transport, which recreates the
container, so roll the deploys one at a time, workbench first.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nodered  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def dockerfile_version(app: str) -> str | None:
    path = ROOT / "apps" / app / "Dockerfile"
    if not path.exists():
        return None
    found = re.search(r"^FROM\s+\S+/node-red:(\S+)\s*$", path.read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else None


def bump(inst: dict, to: str, write: bool) -> tuple[str, str] | None:
    """Rewrite one instance's Dockerfile and image_tag. Returns (old, new) tags."""
    tag = inst["image_tag"]
    if nodered.tag_version(tag) == to:
        return None
    new_tag = f"{tag.partition(':')[0]}:{to}-1"
    if not write:
        return tag, new_tag

    docker = ROOT / "apps" / inst["app"] / "Dockerfile"
    text, n = re.subn(r"^(FROM\s+\S+/node-red:)\S+\s*$", rf"\g<1>{to}",
                      docker.read_text(encoding="utf-8"), count=1, flags=re.M)
    if n != 1:
        sys.exit(f"{inst['app']}: no FROM ...node-red:<version> line in its Dockerfile")
    docker.write_text(text, encoding="utf-8")
    nodered.set_image_tag(inst["name"], new_tag)
    return tag, new_tag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, metavar="VERSION",
                    help="the Node-RED version to move to, e.g. 5.0.1")
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--instance", action="append", default=[],
                        help="one instance; repeat for several")
    target.add_argument("--all", action="store_true", help="every instance that has an app")
    ap.add_argument("--dry-run", action="store_true", help="print the changes, write nothing")
    args = ap.parse_args()

    if not VERSION.match(args.to):
        sys.exit(f"--to takes a version like 5.0.1, not {args.to!r}. A floating tag "
                 f"would defeat the pin (decision 5).")

    known = {i["name"]: i for i in nodered.instances() if i.get("app")}
    if args.all:
        targets = list(known.values())
    else:
        unknown = [n for n in args.instance if n not in known]
        if unknown:
            sys.exit(f"no instance with an app named: {', '.join(unknown)}.\n"
                     f"  Known: {', '.join(sorted(known))}")
        targets = [known[n] for n in args.instance]

    moved, already = [], []
    for inst in targets:
        # The Dockerfile is what CI builds from, so a disagreement there is the
        # one that produces a wrongly named image.
        on_disk = dockerfile_version(inst["app"])
        in_tag = nodered.tag_version(inst["image_tag"])
        if on_disk and on_disk != in_tag:
            sys.exit(f"{inst['name']}: its Dockerfile says {on_disk} and its image_tag "
                     f"says {in_tag}. Reconcile that first — this script would carry "
                     f"the disagreement forward.")

        result = bump(inst, args.to, write=not args.dry_run)
        (already if result is None else moved).append(inst["name"])
        if result:
            print(f"  {inst['name']:<11} {nodered.tag_short(result[0])} -> "
                  f"{nodered.tag_short(result[1])}")

    if already:
        print(f"\nalready on {args.to}: {', '.join(already)}")
    if not moved:
        print("nothing to do")
        return 0
    if args.dry_run:
        print(f"\ndry run — nothing written. Drop --dry-run to move "
              f"{len(moved)} instance(s).")
        return 0

    # The generated pipeline embeds the tags, and CI fails if it is stale.
    subprocess.run([sys.executable, "scripts/gen-image-pipeline.py"], cwd=ROOT, check=True)

    print(f"\n{len(moved)} instance(s) moved to {args.to}. Next:\n"
          f"  python3 scripts/validate-registry.py\n"
          f"  git diff\n"
          f"  commit to the default branch — CI builds each changed app\n"
          f"  then per instance: Jenkins DEPLOY_PALETTE=true, DRY_RUN=false\n"
          f"\nThat last step recreates the container, so it is an interruption per\n"
          f"instance. Workbench first, then one prod, then the rest — and read the\n"
          f"release notes between the two versions before the first one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
