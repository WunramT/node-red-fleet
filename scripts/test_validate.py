#!/usr/bin/env python3
"""Tests for validate-registry.py's palette-tag guard. Run: python3 scripts/test_validate.py

CI builds an app's image when its package.json or Dockerfile changes and pushes
the tag registry.yml names, so a palette change with an unchanged tag silently
replaces the image an instance already runs. This guard is the only thing that
catches that, which makes two things worth proving: that it fires, and that it
says so out loud when it cannot run instead of passing quietly.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILED = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


if not shutil.which("git"):
    print("  skipped: no git on PATH")
    sys.exit(0)

APP = "wfm-test"
TMP = Path(tempfile.mkdtemp(prefix="test_validate_"))
try:
    # A copy of the real registry and schema in a repo of its own, so a commit
    # can be made and diffed against without touching this one.
    (TMP / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "validate-registry.py", TMP / "scripts")
    shutil.copytree(REPO / "schemas", TMP / "schemas")
    shutil.copy(REPO / "registry.yml", TMP / "registry.yml")
    shutil.copytree(REPO / "apps", TMP / "apps")
    REG = TMP / "registry.yml"
    PKG = TMP / "apps" / APP / "package.json"

    def git(*args):
        return subprocess.run(["git", *args], cwd=TMP, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "base")

    def validate(*args):
        return subprocess.run([sys.executable, "scripts/validate-registry.py", *args],
                              cwd=TMP, capture_output=True, text=True)

    base = validate()
    check("the copied registry validates on its own", base.returncode == 0, base.stderr)

    # A palette change without a tag bump: the case the guard exists for.
    pkg = json.loads(PKG.read_text(encoding="utf-8"))
    pkg["dependencies"]["node-red-contrib-cron-plus"] = "~2.1.0"
    PKG.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    out = validate("--changed-since", "HEAD")
    check("a palette change with an unchanged image_tag fails", out.returncode == 1, out.stderr)
    check("and the message names the app", APP in out.stderr, out.stderr)
    check("and says to raise the build suffix", "build suffix" in out.stderr, out.stderr)

    # The same change with the tag raised is what the rule asks for.
    text = REG.read_text(encoding="utf-8")
    old_tag = next(l for l in text.splitlines() if "image_tag" in l and f"/{APP}:" in l)
    text = text.replace(old_tag, old_tag.replace(":4.0.9-1", ":4.0.9-2")
                        if ":4.0.9-1" in old_tag else old_tag.rsplit("-", 1)[0] + "-99")
    REG.write_text(text, encoding="utf-8")
    out = validate("--changed-since", "HEAD")
    check("raising it passes", out.returncode == 0, out.stderr)

    # A flow change is not a palette change; the guard must not fire on it.
    git("-c", "commit.gpgsign=false", "commit", "-qam", "bump")
    (TMP / "apps" / APP / "flows.json").write_text("[]\n", encoding="utf-8")
    out = validate("--changed-since", "HEAD")
    check("a flow-only change does not trip the palette guard", out.returncode == 0, out.stderr)

    # It cannot run: a reference that is not in the clone, and no git at all.
    # Both have to be visible on stderr, because a silent skip in CI reads as a
    # guard that passed.
    out = validate("--changed-since", "0" * 40)
    check("an unknown ref does not fail the run", out.returncode == 0, out.stderr)
    check("but says it could not diff", "cannot diff against" in out.stderr, out.stderr)
    check("and names the shallow clone as the likely cause", "GIT_DEPTH" in out.stderr, out.stderr)

    shutil.rmtree(TMP / ".git")
    out = validate("--changed-since", "HEAD")
    check("without a repository it does not crash", out.returncode == 0, out.stderr)
    check("and says the image needs git", "needs git" in out.stderr, out.stderr)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
