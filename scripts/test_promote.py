#!/usr/bin/env python3
"""Tests for promote.py. Run: python3 scripts/test_promote.py

Promotion decides what of a workbench reaches production and what of production
keeps running, so the cases that matter are the ones where it must NOT act: a
config node the destination already has, a subflow other tabs there use, and a
source that has to keep serving.

It runs against two fixture apps in a temporary tree. Using real ones tied the
suite to estate data — tab labels, node ids, a node count — all of which move
the moment someone uses the project as designed, and an empty workbench is the
normal state of a workbench.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from normalize import normalize, render  # noqa: E402

FAILED = []

PROD = [
    {"id": "t-runs", "type": "tab", "label": "Runs", "disabled": False, "info": ""},
    {"id": "p-in", "type": "inject", "z": "t-runs", "name": "Start", "wires": [["p-fn"]]},
    {"id": "p-fn", "type": "function", "z": "t-runs", "name": "Shape", "wires": [["p-out"]]},
    {"id": "p-out", "type": "mqtt out", "z": "t-runs", "broker": "cfg-broker", "wires": []},
    {"id": "t-other", "type": "tab", "label": "Other", "disabled": False, "info": ""},
    {"id": "p-keep", "type": "debug", "z": "t-other", "name": "Keep", "wires": []},
    {"id": "cfg-broker", "type": "mqtt-broker", "name": "prod broker", "broker": "prod.example"},
    {"id": "cfg-tls", "type": "tls-config", "name": "prod tls"},
]
BENCH = [
    {"id": "t-bench", "type": "tab", "label": "Bench", "disabled": False, "info": ""},
    {"id": "b-own", "type": "debug", "z": "t-bench", "name": "Own", "wires": []},
    {"id": "cfg-broker", "type": "mqtt-broker", "name": "bench broker", "broker": "bench.example"},
]

TMP = Path(tempfile.mkdtemp(prefix="test_promote_"))
SCRIPT = TMP / "scripts" / "promote.py"
(TMP / "scripts").mkdir(parents=True)
for name in ("promote.py", "normalize.py"):
    shutil.copy(REPO / "scripts" / name, TMP / "scripts" / name)
A, B = TMP / "apps" / "alpha" / "flows.json", TMP / "apps" / "beta" / "flows.json"
for path, nodes in ((A, PROD), (B, BENCH)):
    path.parent.mkdir(parents=True)
    # Canonical, because a committed flow is: promote rewrites both sides
    # through the normalizer, and an uncanonical fixture would fail the
    # untouched-source check on node order rather than on anything promote did.
    path.write_text(render(normalize(nodes)), encoding="utf-8")


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def run(*args):
    out = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


def flows(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ids(path):
    return {n["id"] for n in flows(path)}


def tab_of(path, label):
    return next((n for n in flows(path) if n.get("type") == "tab" and n.get("label") == label), None)


print("promote.py")
keep_a = A.read_bytes()
try:
    # --- the direction that must not touch the source ---------------------
    code, out = run("--from", "alpha", "--to", "beta", "--tab", "Runs", "--copy")
    check("copy succeeds", code == 0, out)
    check("the tab arrives in the destination", tab_of(B, "Runs") is not None)
    check("and prod keeps it — it is still running there", tab_of(A, "Runs") is not None)
    check("prod is otherwise untouched", A.read_bytes() == keep_a)

    moved = tab_of(B, "Runs")
    check("it arrives disabled on the workbench", moved.get("disabled") is True,
          str(moved.get("disabled")))
    check("and says so", "arrives DISABLED" in out, out)
    on_tab = [n for n in flows(B) if n.get("z") == moved["id"]]
    check("its nodes come along", len(on_tab) == 3, str(len(on_tab)))
    check("so do the config nodes it names", "cfg-broker" in ids(B))
    check("the tab it does not name stays behind", tab_of(B, "Other") is None)
    check("and so does that tab's config node", "cfg-tls" not in ids(B))
    check("the workbench's own tab is left alone", tab_of(B, "Bench") is not None)

    # --- a destination config node is never overwritten --------------------
    broker = next(n for n in flows(B) if n["id"] == "cfg-broker")
    check("the destination's own broker survived the first promotion",
          broker.get("broker") == "bench.example", str(broker.get("broker")))

    code, out = run("--from", "alpha", "--to", "beta", "--tab", "Runs", "--copy")
    broker = next(n for n in flows(B) if n["id"] == "cfg-broker")
    check("a second promotion keeps it too", broker.get("broker") == "bench.example",
          str(broker.get("broker")))
    check("and says so", "keeps beta's own config node" in out, out)
    check("the tab is replaced, not duplicated",
          len([n for n in flows(B) if n.get("type") == "tab" and n.get("label") == "Runs"]) == 1)

    # --- shipping back clears the workbench -------------------------------
    code, out = run("--from", "beta", "--to", "alpha", "--tab", "Runs", "--move")
    check("move succeeds", code == 0, out)
    check("the workbench loses the tab", tab_of(B, "Runs") is None)
    check("and its nodes with it", not [n for n in flows(B) if n.get("z") == moved["id"]])
    check("the workbench keeps its config node for next time", "cfg-broker" in ids(B))
    shipped = tab_of(A, "Runs")
    check("prod has it", shipped is not None)
    check("and it arrives enabled there", shipped.get("disabled") is False,
          str(shipped.get("disabled")))
    check("deploy order is spelled out", "Deploy beta first" in out, out)

    # --- guard rails ------------------------------------------------------
    code, out = run("--from", "beta", "--to", "beta", "--tab", "Bench", "--copy")
    check("refuses the same app twice", code != 0 and "same app" in out)

    code, out = run("--from", "alpha", "--to", "beta", "--tab", "no-such-tab", "--copy")
    check("names the tabs it does have", code != 0 and "Present:" in out, out)

    code, out = run("--from", "alpha", "--to", "beta", "--tab", "Runs")
    check("refuses without --copy or --move", code != 0 and "--copy --move" in out, out)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
