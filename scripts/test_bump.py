#!/usr/bin/env python3
"""Tests for bump-node-red.py. Run: python3 scripts/test_bump.py

The version of an instance lives in its Dockerfile and in its image_tag, and
the whole point of the script is that those two never diverge. So that is what
this checks, plus that it refuses rather than guessing.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nodered  # noqa: E402
SCRIPT = ROOT / "scripts" / "bump-node-red.py"
FAILED = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT)


APP = "wfm-test"
OTHER = "wfm-prod"
# Read out of the registry, not written down here: the estate's versions move,
# and a suite that hardcodes one fails on the day someone runs the bump for
# real. The target is deliberately a version nothing is on.
FROM_VERSION = nodered.tag_version(nodered.find(APP)["image_tag"])
TO_VERSION = "9.9.9"
OTHER_TAG = nodered.tag_short(nodered.find(OTHER)["image_tag"])

REG = ROOT / "registry.yml"
DOCKER = ROOT / "apps" / APP / "Dockerfile"
PIPELINE = ROOT / "apps" / "build-image-pipeline.yml"
saved = {p: p.read_bytes() for p in (REG, DOCKER, PIPELINE)}

print("bump-node-red.py")
try:
    r = run("--to", "latest", "--all")
    check("a floating version is refused", r.returncode != 0 and "5.0.1" in r.stderr, r.stderr[-200:])

    r = run("--to", TO_VERSION, "--instance", "no-such-instance")
    check("an unknown instance is refused", r.returncode != 0 and "Known:" in r.stderr)

    r = run("--to", TO_VERSION, "--instance", "slu-prod")
    check("an instance without an app is not a target", r.returncode != 0, r.stderr[-200:])

    r = run("--to", TO_VERSION, "--instance", APP, "--dry-run")
    check("--dry-run reports the move",
          f"{APP}:{FROM_VERSION}-1 -> {APP}:{TO_VERSION}-1" in r.stdout, r.stdout)
    check("and writes nothing",
          REG.read_bytes() == saved[REG] and DOCKER.read_bytes() == saved[DOCKER])

    comments_before = saved[REG].decode().count("#")
    r = run("--to", TO_VERSION, "--instance", APP)
    check("the move exits 0", r.returncode == 0, r.stderr[-300:])
    registry, dockerfile = REG.read_text(encoding="utf-8"), DOCKER.read_text(encoding="utf-8")

    check("the image_tag carries the new version", f"{APP}:{TO_VERSION}-1" in registry)
    check("the palette build resets to 1", f"{APP}:{TO_VERSION}-2" not in registry)
    check("the Dockerfile FROM follows",
          re.search(rf"^FROM \S+/node-red:{re.escape(TO_VERSION)}$", dockerfile, re.M) is not None)
    check("no other instance moved", OTHER_TAG in registry)
    check("every comment in the registry survives", registry.count("#") == comments_before)
    check("the Dockerfile keeps its explanation",
          "node-red itself" in dockerfile and "palette.json" in dockerfile)
    check("the generated pipeline was refreshed",
          f"{TO_VERSION}-1" in PIPELINE.read_text(encoding="utf-8"))

    r = run("--to", TO_VERSION, "--instance", APP)
    check("moving an instance that is already there is a no-op",
          f"already on {TO_VERSION}" in r.stdout and "nothing to do" in r.stdout, r.stdout)

    # A Dockerfile and a tag that disagree is exactly what this script exists to
    # prevent, so it must not carry such a state forward silently.
    DOCKER.write_text(re.sub(r"^FROM (\S+)/node-red:\S+$", r"FROM \1/node-red:0.0.1",
                             DOCKER.read_text(encoding="utf-8"), count=1, flags=re.M),
                      encoding="utf-8")
    r = run("--to", "9.9.8", "--instance", APP)
    check("a Dockerfile that disagrees with the tag stops it",
          r.returncode != 0 and "Reconcile" in r.stderr, r.stderr[-200:])
finally:
    for path, content in saved.items():
        path.write_bytes(content)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
