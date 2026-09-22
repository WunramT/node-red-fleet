#!/usr/bin/env python3
"""Tests for normalize.py. Run: python3 scripts/test_normalize.py

These cover the properties the deploy path depends on. The real test is
running --check against every flow in samples/, which is a separate step.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import normalize, normalize_text, render  # noqa: E402

FAILED = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


# A flow with the structures a real one has: two tabs, a subflow, a group,
# a config node with no z, and nodes deliberately out of order.
FLOW = [
    {"id": "tab2", "type": "tab", "label": "Second", "disabled": False},
    {"id": "tab1", "type": "tab", "label": "First", "disabled": False},
    {"id": "sf1", "type": "subflow", "name": "Helper", "in": [], "out": []},
    {"id": "nodeC", "type": "debug", "z": "tab1", "x": 300, "y": 100, "name": "C", "wires": []},
    {"id": "nodeA", "type": "inject", "z": "tab1", "x": 100, "y": 100, "name": "A",
     "wires": [["nodeC"]]},
    {"id": "nodeB", "type": "function", "z": "tab2", "g": "grp1", "x": 100, "y": 200,
     "name": "B", "func": "return msg;", "wires": [[]]},
    {"id": "grp1", "type": "group", "z": "tab2", "x": 80, "y": 160, "w": 200, "h": 80,
     "nodes": ["nodeB"]},
    {"id": "cfg1", "type": "mqtt-broker", "name": "broker", "broker": "mqtt.example.local"},
    {"id": "sfnode", "type": "change", "z": "sf1", "x": 100, "y": 60, "wires": [[]]},
]

print("normalize.py")

out = normalize(FLOW)

check("z survives — it is the tab id, not a position",
      all(n.get("z") == o.get("z") for n, o in
          zip(sorted(out, key=lambda n: n["id"]), sorted(FLOW, key=lambda n: n["id"]))))

check("no key is dropped",
      {k for n in FLOW for k in n} == {k for n in out for k in n},
      str({k for n in FLOW for k in n} - {k for n in out for k in n}))

check("x/y survive so the editor layout is preserved",
      next(n for n in out if n["id"] == "nodeA")["x"] == 100)

check("group w/h survive",
      {"w", "h"} <= set(next(n for n in out if n["id"] == "grp1")))

check("group membership g survives",
      next(n for n in out if n["id"] == "nodeB")["g"] == "grp1")

check("tab order is preserved, not sorted",
      [n["id"] for n in out if n["type"] == "tab"] == ["tab2", "tab1"])

check("nodes are grouped under their tab, in tab order, sorted by id within it",
      [n["id"] for n in out if n.get("z") in ("tab1", "tab2")]
      == ["grp1", "nodeB", "nodeA", "nodeC"],
      str([n["id"] for n in out if n.get("z") in ("tab1", "tab2")]))

check("id and type lead every node",
      all(list(n)[:2] == ["id", "type"] for n in out))

check("wires come last",
      all(list(n)[-1] == "wires" for n in out if "wires" in n))

check("idempotent", normalize(out) == out)
check("idempotent through text", normalize_text(normalize_text(json.dumps(FLOW))) == normalize_text(json.dumps(FLOW)))

structural = [n for n in FLOW if n["type"] in ("tab", "subflow")]
rest = [n for n in FLOW if n["type"] not in ("tab", "subflow")]
check("shuffling the nodes Node-RED may reshuffle does not affect the result",
      normalize(structural + list(reversed(rest))) == out)

check("reordering tabs DOES change the result — tab order is editor state",
      [n["id"] for n in normalize(list(reversed(structural)) + rest) if n["type"] == "tab"]
      == ["tab1", "tab2"])

check("output is valid JSON that round-trips",
      json.loads(render(out)) == out)

check("2-space indent, trailing newline",
      render(out).startswith("[\n  {") and render(out).endswith("}\n]\n"))

NON_ASCII = "Größe → ok ❌"
check("non-ascii is kept readable, not escaped",
      NON_ASCII in render(normalize([{"id": "a", "type": "t", "name": NON_ASCII}])))

check("every node id survives",
      {n["id"] for n in FLOW} == {n["id"] for n in out})

# --check / --write behaviour
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "flows.json"
    script = Path(__file__).resolve().parent / "normalize.py"

    p.write_text(json.dumps(FLOW), encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), "--check", str(p)], capture_output=True, text=True)
    check("--check fails on an unnormalized file", r.returncode == 1)

    r = subprocess.run([sys.executable, str(script), "--write", str(p)], capture_output=True, text=True)
    check("--write succeeds", r.returncode == 0)

    r = subprocess.run([sys.executable, str(script), "--check", str(p)], capture_output=True, text=True)
    check("--check passes after --write", r.returncode == 0, r.stderr)

    before = p.read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(script), "--write", str(p)], capture_output=True, text=True)
    check("--write twice is a no-op", p.read_text(encoding="utf-8") == before)

    p.write_text("{not json", encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), "--check", str(p)], capture_output=True, text=True)
    check("malformed JSON fails loudly", r.returncode == 1 and "flows.json" in r.stderr)

    p.write_text('{"not": "a list"}', encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), "--check", str(p)], capture_output=True, text=True)
    check("a JSON object instead of an array fails", r.returncode == 1)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
