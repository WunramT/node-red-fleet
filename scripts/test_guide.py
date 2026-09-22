#!/usr/bin/env python3
"""Tests for guide.py. Run: python3 scripts/test_guide.py

The shape lets a `run` or `do` step reference a slot before it is ever asked
for, so that is the thing most worth checking, task by task. Then the
guardrails: only the three allowed programs run, and no step runs a git write
or --force. And that a path under apps/ is built from the app slot rather than
the instance slot, because those are different fields in registry.yml.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "guide.py"
FAILED = []

sys.path.insert(0, str(ROOT / "scripts"))
import guide  # noqa: E402

SLOT_RE = re.compile(r"\{(\w+)\}")


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def run(*args):
    out = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


print("guide.py")

code, out = run("--list")
check("--list exits 0", code == 0, out)
for key, task in guide.TASKS.items():
    check(f"--list names {key}", key in out, out)
    check(f"--list shows {key}'s title", task["title"] in out, out)

for key, task in guide.TASKS.items():
    code, out = run("--print", key)
    check(f"--print {key} exits 0", code == 0, out)
    check(f"--print {key} shows its title", task["title"] in out, out)
    check(f"--print {key} runs nothing", "$" not in out, out)

code, out = run("--print", "no-such-task")
check("--print of an unknown task is non-zero", code != 0)
for key in guide.TASKS:
    check(f"unknown --print names {key} as valid", key in out, out)

code, out = run("no-such-task")
check("an unknown task exits non-zero", code != 0)
for key in guide.TASKS:
    check(f"unknown task names {key} as valid", key in out, out)

for key, task in guide.TASKS.items():
    asked = set()
    for step in task["steps"]:
        kind = step[0]
        if kind == "ask":
            _, slot, question, source = step
            check(f"{key}: {slot}'s source is valid", source in guide.SOURCES, source)
            check(f"{key}: {slot} is asked only once", slot not in asked, slot)
            asked.add(slot)
            # An instance answer also carries its app directory, because the
            # instance name is not the directory: registry.yml keeps `app`
            # separate and two of them could differ.
            if source != "text":
                asked.add(f"{slot}_app")
        elif kind == "run":
            _, parts, why = step
            check(f"{key}: run argv[0] is an allowed program",
                  parts[0] in guide.ALLOWED_PROGRAMS, parts[0])
            joined = " ".join(parts)
            check(f"{key}: run step is not a git write or --force",
                  "git commit" not in joined and "git push" not in joined
                  and "--force" not in joined, joined)
            for slot in SLOT_RE.findall(joined):
                check(f"{key}: {{{slot}}} in a run step was asked earlier", slot in asked, joined)
            for path_slot in re.findall(r"apps/\{(\w+)\}", joined):
                check(f"{key}: the apps/ path uses an app slot, not an instance name",
                      path_slot.endswith("_app"), joined)
        elif kind == "do":
            _, text = step
            for slot in SLOT_RE.findall(text):
                check(f"{key}: {{{slot}}} in a do step was asked earlier", slot in asked, text)
        else:
            check(f"{key}: step kind is ask, run or do", False, kind)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
