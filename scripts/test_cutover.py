#!/usr/bin/env python3
"""Tests for cutover-plan.py. Run: python3 scripts/test_cutover.py

The tool decides which tabs may be moved to a new runtime one at a time. A
missed coupling is the expensive mistake: the two ends of a link land in
different runtimes, the sending side keeps firing, and nothing reports that the
messages stopped arriving. So the grouping is what gets checked hardest.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("cutover", ROOT / "scripts" / "cutover-plan.py")
cutover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)

FAILED = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def names(groups):
    return [sorted(t["label"] for t in g) for g in groups]


# Two tabs, a link from one into the other: they cannot be split.
linked = [
    {"id": "a", "type": "tab", "label": "A"},
    {"id": "b", "type": "tab", "label": "B"},
    {"id": "out", "type": "link out", "z": "a", "links": ["in"], "wires": []},
    {"id": "in", "type": "link in", "z": "b", "links": [], "wires": []},
]
check("a link across tabs makes one group", names(cutover.groups(linked)) == [["A", "B"]],
      str(names(cutover.groups(linked))))

# The same two tabs without the link move independently.
apart = [n for n in linked if not str(n["type"]).startswith("link ")]
apart += [{"id": "f", "type": "function", "z": "a", "wires": []},
          {"id": "g", "type": "function", "z": "b", "wires": []}]
check("without a link they are separate", names(cutover.groups(apart)) == [["A"], ["B"]],
      str(names(cutover.groups(apart))))

# A link that stays inside its own tab couples nothing.
inside = [
    {"id": "a", "type": "tab", "label": "A"},
    {"id": "b", "type": "tab", "label": "B"},
    {"id": "out", "type": "link out", "z": "a", "links": ["in"], "wires": []},
    {"id": "in", "type": "link in", "z": "a", "links": [], "wires": []},
    {"id": "g", "type": "function", "z": "b", "wires": []},
]
check("a link within one tab couples nothing", names(cutover.groups(inside)) == [["A"], ["B"]],
      str(names(cutover.groups(inside))))

# Coupling is transitive: A-B and B-C is one group of three, not two of two.
chain = [
    {"id": "a", "type": "tab", "label": "A"},
    {"id": "b", "type": "tab", "label": "B"},
    {"id": "c", "type": "tab", "label": "C"},
    {"id": "o1", "type": "link out", "z": "a", "links": ["i1"], "wires": []},
    {"id": "i1", "type": "link in", "z": "b", "links": [], "wires": []},
    {"id": "o2", "type": "link out", "z": "b", "links": ["i2"], "wires": []},
    {"id": "i2", "type": "link in", "z": "c", "links": [], "wires": []},
]
check("coupling is transitive", names(cutover.groups(chain)) == [["A", "B", "C"]],
      str(names(cutover.groups(chain))))

# MQTT is not a coupling: it goes through a broker and survives two runtimes.
mqtt = [
    {"id": "a", "type": "tab", "label": "A"},
    {"id": "b", "type": "tab", "label": "B"},
    {"id": "br", "type": "mqtt-broker", "name": "broker"},
    {"id": "p", "type": "mqtt out", "z": "a", "broker": "br", "topic": "t", "wires": []},
    {"id": "s", "type": "mqtt in", "z": "b", "broker": "br", "topic": "t", "wires": []},
]
check("a shared broker is not a coupling", names(cutover.groups(mqtt)) == [["A"], ["B"]],
      str(names(cutover.groups(mqtt))))
text = cutover.report(mqtt)
check("but the shared config node is reported", "broker" in text and "groups 1, 2" in text, text)
check("and named by its type", "mqtt-broker" in text, text)

lone = cutover.report(apart)
check("nothing shared says so", "No config node is shared" in lone, lone)

# The CLI: a real file in, a readable report out; anything else refused.
with tempfile.TemporaryDirectory() as tmp:
    good = Path(tmp) / "flows.json"
    good.write_text(json.dumps(linked), encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "cutover-plan.py"), str(good)],
                         capture_output=True, text=True)
    check("the CLI exits 0", out.returncode == 0, out.stderr)
    check("and says the two move as one", "move as one" in out.stdout, out.stdout)

    bad = Path(tmp) / "object.json"
    bad.write_text('{"not": "a flow"}', encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "cutover-plan.py"), str(bad)],
                         capture_output=True, text=True)
    check("a JSON object is refused", out.returncode != 0, out.stdout)

# Against the estate: every committed flow parses and groups without error, and
# the one real coupling in it is found.
for path in sorted((ROOT / "apps").glob("*/flows.json")):
    flows = json.loads(path.read_text(encoding="utf-8"))
    found = cutover.groups(flows)
    tabs = [n for n in flows if n.get("type") == "tab"]
    check(f"{path.parent.name}: every tab lands in exactly one group",
          sorted(t["id"] for g in found for t in g) == sorted(t["id"] for t in tabs))

gor = json.loads((ROOT / "apps" / "gor-test" / "flows.json").read_text(encoding="utf-8"))
coupled = [g for g in cutover.groups(gor) if len(g) > 1]
check("gor-test's linked pair is the only group that must move together", len(coupled) == 1)
check("and it is ZUND with ZÜND TEST",
      sorted(t["label"] for t in coupled[0]) == ["ZUND", "ZÜND TEST"] if coupled else False,
      str([t["label"] for t in coupled[0]] if coupled else []))

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
