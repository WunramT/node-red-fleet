#!/usr/bin/env python3
"""Tests for secrets-to-env.py. Run: python3 scripts/test_secrets.py

Two things matter more than the rewrite itself: that no secret reaches the
output, and that a field the tool cannot switch is left alone rather than
guessed at — a wrong FieldType silently breaks a database connection.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "secrets-to-env.py"
spec = importlib.util.spec_from_file_location("s2e", SCRIPT)
s2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s2e)

FAILED = []
SHARED = "hunter2-shared-value"
OTHER = "a-different-value"


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def flow():
    return [
        {"id": "t1", "type": "tab", "label": "T"},
        {"id": "a", "type": "postgreSQLConfig", "name": "db one",
         "password": SHARED, "passwordFieldType": "str", "host": "h"},
        {"id": "b", "type": "postgreSQLConfig", "name": "db two",
         "password": SHARED, "passwordFieldType": "str", "host": "h"},
        {"id": "c", "type": "postgreSQLConfig", "name": "db three",
         "password": OTHER, "passwordFieldType": "str", "host": "h"},
        {"id": "d", "type": "e-mail", "z": "t1", "name": "mail",
         "token": "a-token-with-no-typed-input", "wires": []},
        {"id": "e", "type": "postgreSQLConfig", "name": "already done",
         "password": "SOME_VAR", "passwordFieldType": "env", "host": "h"},
        {"id": "f", "type": "MSSQL-CN", "name": "no secret here", "server": "s"},
    ]


convertible, manual = s2e.plan(flow())
check("a typed field is convertible", len(convertible) == 3, str(convertible))
check("one without a FieldType is not", [m["field"] for m in manual] == ["token"], str(manual))
check("a field already on env is left out",
      all(e["id"] != "e" for e in convertible + manual))
check("a node with no secret field is not mentioned",
      all(e["id"] != "f" for e in convertible + manual))

shared = {e["variable"] for e in convertible if e["id"] in ("a", "b")}
check("fields holding the same secret share one variable", len(shared) == 1, str(shared))
check("a different secret gets its own variable",
      next(e["variable"] for e in convertible if e["id"] == "c") not in shared)

after = s2e.apply(flow(), convertible)
by_id = {n["id"]: n for n in after}
check("the value is replaced by the variable name",
      by_id["a"]["password"] == next(e["variable"] for e in convertible if e["id"] == "a"))
check("and the field type says env", by_id["a"]["passwordFieldType"] == "env")
check("the untouched field keeps its value", by_id["d"]["token"] == "a-token-with-no-typed-input")
check("and the untouched node keeps its type", by_id["e"]["passwordFieldType"] == "env")

check("a value that reads as an identifier is marked as maybe-not-a-secret",
      next(m["identifier"] for m in manual if m["field"] == "token") is False,
      str(manual))
ident = s2e.plan([{"id": "m", "type": "e-mail", "name": "mail",
                   "token": "oauth2Response", "wires": []}])[1]
check("and one that really is an identifier says so", ident[0]["identifier"] is True, str(ident))
check("the note reaches the report",
      "may be a property name" in s2e.report([], ident, written=False))

text = s2e.report(convertible, manual, written=True)
check("no secret appears in the report", SHARED not in text and OTHER not in text, text)
check("the report sends the variables to the compose file", "compose file" in text, text)
check("and says not to put them in the registry", "registry.yml" in text, text)
check("and says the committed ones still need rotating", "Rotate" in text, text)

# One secret used by two instances is one variable, not two — that is the
# number that decides how much rotation there is to do.
shared_taken = {}
one, _ = s2e.plan(flow(), shared_taken, source="app-one")
two, _ = s2e.plan([{"id": "z", "type": "postgreSQLConfig", "name": "elsewhere",
                    "password": SHARED, "passwordFieldType": "str"}],
                  shared_taken, source="app-two")
check("the same secret in another file reuses the variable",
      two[0]["variable"] == one[0]["variable"], f"{two[0]['variable']} vs {one[0]['variable']}")
across = s2e.report(one + two, [], written=False)
check("the report counts distinct secrets, not fields", "2 distinct secret(s) behind 4 field(s)" in across, across)
check("and names both instances on the shared one", "app-one, app-two" in across, across)
check("across files it says so", "across 2 file(s)" in across, across)
check("still no secret in it", SHARED not in across and OTHER not in across, across)

# End to end, including that a second run finds nothing left to do.
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "flows.json"
    path.write_text(json.dumps(flow()), encoding="utf-8")

    first = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
    check("the report run exits 0", first.returncode == 0, first.stderr)
    check("and changes nothing", json.loads(path.read_text(encoding="utf-8")) == flow())
    check("no secret in the CLI output", SHARED not in first.stdout, first.stdout)

    wrote = subprocess.run([sys.executable, str(SCRIPT), str(path), "--write"],
                           capture_output=True, text=True)
    check("the write run exits 0", wrote.returncode == 0, wrote.stderr)
    body = path.read_text(encoding="utf-8")
    check("the secret is gone from the file", SHARED not in body and OTHER not in body)
    check("the file is still canonical",
          subprocess.run([sys.executable, str(ROOT / "scripts" / "normalize.py"), "--check", str(path)],
                         capture_output=True).returncode == 0)

    again = subprocess.run([sys.executable, str(SCRIPT), str(path), "--write"],
                           capture_output=True, text=True)
    check("a second write finds nothing to switch", "No field to switch" in again.stdout, again.stdout)
    check("and still reports the one it cannot", "e-mail" in again.stdout, again.stdout)

    bad = Path(tmp) / "object.json"
    bad.write_text('{"not": "a flow"}', encoding="utf-8")
    out = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    check("a JSON object is refused", out.returncode != 0, out.stdout)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
