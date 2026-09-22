#!/usr/bin/env python3
"""Validate registry.yml against the schema, plus the rules a schema cannot express.

    python3 scripts/validate-registry.py            # fails on CHANGEME
    python3 scripts/validate-registry.py --draft     # allows CHANGEME, still checks the rest

Exit code 0 means the registry is deployable.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry.yml"
SCHEMA = ROOT / "schemas" / "registry.schema.json"
APPS = ROOT / "apps"


def check(registry: dict, allow_changeme: bool) -> list[str]:
    """The rules the schema cannot express: cross-field and cross-file ones."""
    errors = []
    instances = registry.get("instances", [])

    seen: dict[str, str] = {}
    for inst in instances:
        name = inst.get("name")
        if name in seen:
            errors.append(f"duplicate instance name '{name}'")
        seen[name] = inst.get("host", "?")

    for inst in instances:
        name = inst.get("name", "?")

        # A host may run several services, but not two with the same name.
        twins = [
            o for o in instances
            if o is not inst
            and o.get("host") == inst.get("host")
            and o.get("compose_service") == inst.get("compose_service")
        ]
        if twins:
            errors.append(
                f"{name}: compose_service '{inst.get('compose_service')}' is claimed twice "
                f"on {inst.get('host')} (also by {twins[0].get('name')})"
            )

        # An app must exist as a directory, or the deploy has nothing to push.
        app = inst.get("app")
        if app and not (APPS / app).is_dir():
            errors.append(f"{name}: app '{app}' has no directory at apps/{app}/")
        if app and not (APPS / app / "flows.json").is_file():
            errors.append(f"{name}: apps/{app}/flows.json is missing")

        if not allow_changeme:
            for field in ("image_tag", "auth_credential_id", "credential_secret_id"):
                if str(inst.get(field, "")).startswith("CHANGEME"):
                    errors.append(f"{name}: {field} is still a placeholder")

    return errors


def palette_tags_moved(ref: str) -> list[str]:
    """Apps whose palette changed since `ref` while their image_tag did not.

    CI builds an image when that app's package.json or Dockerfile changes, and
    it pushes the tag registry.yml names. So a palette change with an unchanged
    tag does not produce a new image — it replaces the one the instance already
    runs, under the same name. Nothing reports that afterwards: the tag still
    validates, the deploy still pins it, and the content is different.

    Empty on anything this cannot answer — a missing ref, no git — because a
    check that guesses is worse than one that admits it did not run. It says so
    on stderr: a skip in CI is a job that needs git or a deeper clone, not a
    pass.
    """
    def git(*args) -> str | None:
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
        except FileNotFoundError:
            return None
        return out.stdout if out.returncode == 0 else None

    if git("rev-parse", "--git-dir") is None:
        print("palette-tag check skipped: no git here, so nothing to diff. In CI "
              "the job image needs git.", file=sys.stderr)
        return []

    changed = git("diff", "--name-only", ref, "--")
    if changed is None:
        print(f"palette-tag check skipped: cannot diff against {ref}. A shallow "
              f"clone may not contain it — set GIT_DEPTH: 0.", file=sys.stderr)
        return []

    apps = {Path(line).parts[1] for line in changed.splitlines()
            if re.fullmatch(r"apps/[^/]+/(package\.json|Dockerfile)", line)}
    if not apps:
        return []

    before = git("show", f"{ref}:registry.yml")
    if before is None:
        print("palette-tag check skipped: registry.yml is not in that revision",
              file=sys.stderr)
        return []

    current = (yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}).get("instances", [])
    was = {i["name"]: i.get("image_tag") for i in
           (yaml.safe_load(before) or {}).get("instances", [])}
    now = {i["name"]: i.get("image_tag") for i in current}
    by_app = {i.get("app"): i["name"] for i in current}

    stale = []
    for app in sorted(apps):
        name = by_app.get(app)
        if name and was.get(name) == now.get(name):
            stale.append(f"{app}: its palette or Dockerfile changed, but "
                         f"{name}'s image_tag is still {now.get(name)!r} — raise the "
                         f"build suffix, or CI overwrites the tag the instance runs")
    return stale


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", action="store_true",
                    help="allow CHANGEME placeholders; use while the registry is being filled in")
    ap.add_argument("--emit-json", action="store_true",
                    help="also write registry.json, which deploy.py reads on hosts without PyYAML")
    ap.add_argument("--changed-since", metavar="REF",
                    help="also fail when an app's palette moved since REF without its image_tag")
    args = ap.parse_args()

    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(Draft202012Validator(schema).iter_errors(registry),
                        key=lambda e: list(e.absolute_path))
    ]
    errors += check(registry, allow_changeme=args.draft)
    if args.changed_since:
        errors += palette_tags_moved(args.changed_since)

    if errors:
        print(f"registry.yml: {len(errors)} problem(s)\n", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.emit_json:
        (ROOT / "registry.json").write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {ROOT / 'registry.json'}")

    n = len(registry.get("instances", []))
    print(f"registry.yml OK — {n} instances"
          + (" (draft: placeholders allowed)" if args.draft else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
