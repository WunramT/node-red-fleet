#!/usr/bin/env python3
"""Tests for the editor session in nr.py. Run: python3 scripts/test_nr.py

The session is a copy of an app's flow with every tab disabled, and on exit it
is merged back. That merge is the part worth testing: it decides what of a local
session reaches the repository, and getting it wrong either loses an edit or
deploys a disabled tab to an instance.

It runs against a fixture app in a temporary tree, not against a real one under
apps/. Borrowing a live flow made the suite depend on estate data: it asserted
that the workbench has a tab, and a workbench is supposed to be empty whenever
nothing is being tested, so the first promotion that shipped a tab back to prod
broke the tests. It also wrote into a real app file and restored it in a finally
block, which is one crash away from leaving a production flow half merged.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import nodered  # noqa: E402
import nr  # noqa: E402
from normalize import normalize, render  # noqa: E402

APP = "wfm-test"
FAILED = []

FIXTURE = [
    {"id": "tab1", "type": "tab", "label": "First", "disabled": False, "info": ""},
    {"id": "tab2", "type": "tab", "label": "Second", "disabled": True, "info": ""},
    {"id": "n1", "type": "inject", "z": "tab1", "name": "Start", "wires": [["n2"]]},
    {"id": "n2", "type": "debug", "z": "tab1", "name": "Ergebnis", "wires": []},
    {"id": "n3", "type": "function", "z": "tab2", "name": "Off", "wires": []},
    {"id": "c1", "type": "mqtt-broker", "name": "broker", "broker": "example"},
]

# nr.py and nodered.py both resolve paths from their own ROOT, so both move.
TMP = Path(tempfile.mkdtemp(prefix="test_nr_"))
ROOT = TMP
nr.ROOT = TMP
nr.SESSION = TMP / ".editor-session"
nodered.ROOT = TMP
(TMP / "apps" / APP).mkdir(parents=True)
LIVE = TMP / "apps" / APP / "flows.json"
# Written through the normalizer, because a committed flow is canonical and CI
# enforces it. An uncanonical fixture would fail the round-trip check on key
# order rather than on anything the merge did.
LIVE.write_text(render(normalize(FIXTURE)), encoding="utf-8")
(TMP / "apps" / APP / "package.json").write_text(json.dumps(
    {"name": f"node-red-{APP}", "private": True,
     "dependencies": {"node-red-contrib-opcua": "~0.2.339"}}, indent=2) + "\n", encoding="utf-8")
(TMP / "registry.yml").write_bytes((REPO / "registry.yml").read_bytes())


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{'  ' + detail if detail and not condition else ''}")
    if not condition:
        FAILED.append(label)


def tabs(flows):
    return {n["id"]: n for n in flows if n.get("type") == "tab"}


def staged_flows():
    return json.loads((nr.SESSION / APP / "flows.json").read_text(encoding="utf-8"))


def write_staged(flows):
    (nr.SESSION / APP / "flows.json").write_text(json.dumps(flows, indent=2) + "\n", encoding="utf-8")


# The published address is not cosmetic: with the daemon in a VM, which is
# podman on Windows and any dev container, loopback publishes the port
# somewhere the browser cannot reach and the editor looks dead.
check("loopback when the daemon is local", nr.editor_bind(["docker", "compose"], {}) == "127.0.0.1")
check("every interface when the engine is podman",
      nr.editor_bind(["podman", "compose"], {}) == "0.0.0.0")
check("and when we are inside a dev container",
      nr.editor_bind(["docker", "compose"], {"LOCAL_WORKSPACE_FOLDER": "C:/x"}) == "0.0.0.0")

# The banner reports what answered, not what should have. Four rounds of
# deducing the reachable address from where the engine runs got it wrong every
# time, so the probe decides and these tests inject its answer.
real_answers = nr.answers
try:
    nr.answers = lambda url, timeout=1.5: url == "http://172.20.0.2:1880"
    only_ip = nr.editor_urls([("http://node-red-editor:1880", "by name"),
                              ("http://172.20.0.2:1880", "the container"),
                              ("http://localhost:1880", "the published port")], "podman")
    check("it lists only what answered", "172.20.0.2" in only_ip, only_ip)
    check("and drops what did not", "node-red-editor" not in only_ip, only_ip)
    check("and says to forward it when localhost is silent",
          "Ports panel" in only_ip, only_ip)

    nr.answers = lambda url, timeout=1.5: url == "http://localhost:1880"
    local = nr.editor_urls([("http://localhost:1880", "the published port")], "docker")
    check("with localhost answering it does not ask for a forward",
          "Ports panel" not in local and "localhost" in local, local)

    nr.answers = lambda url, timeout=1.5: False
    blind = nr.editor_urls([("http://localhost:1880", "the published port")], "podman")
    check("when nothing answers it says so and gives the command",
          "nothing answered" in blind and "podman port node-red-editor" in blind, blind)
    check("and promises no address", "http://localhost:1880 " not in blind, blind)
finally:
    nr.answers = real_answers

# VS Code forwards a port number and resolves it against this container's
# localhost. The editor is a sibling container, so one hop on localhost is what
# makes the forward possible at all. Proven against a real HTTP server, since
# that needs no container engine.
import threading  # noqa: E402
import urllib.request  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class _Editor(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"editor"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


upstream = HTTPServer(("127.0.0.1", 0), _Editor)
threading.Thread(target=upstream.serve_forever, daemon=True).start()
hop = nr.relay("127.0.0.1", port=18801)
try:
    check("the relay starts", hop is not None)
    hop.target = ("127.0.0.1", upstream.server_address[1])
    with urllib.request.urlopen("http://127.0.0.1:18801", timeout=3) as answer:
        check("localhost answers through it", answer.status == 200)
        check("and it is the editor that answered", answer.read() == b"editor")
    check("a port already taken is refused, not raised",
          nr.relay("127.0.0.1", port=18801) is None)
finally:
    if hop:
        hop.shutdown()
    upstream.shutdown()

check("a target that is not listening closes instead of hanging",
      nr.answers("http://127.0.0.1:18801", timeout=0.5) is False)

# A registry login belongs to the client that ran it; the pulled image belongs
# to the engine. So --baked asks for nothing once the engine has the image, and
# the hint has to point at the pull before the login.
stub = TMP / "bin"
stub.mkdir()
(stub / "engine").write_text(
    '#!/bin/sh\n[ "$3" = "known:1" ] && echo sha256:abc\nexit 0\n', encoding="utf-8")
(stub / "engine").chmod(0o755)
engine = [str(stub / "engine"), "compose"]
check("an image the engine already has needs no pull",
      nr.image_present(engine, "known:1") is True)
check("one it does not have is reported missing",
      nr.image_present(engine, "other:1") is False)

# A login belongs to the client that ran it, so whether one exists here is
# asked, not assumed — and when it does not, the pull has to run where it does.
auth = TMP / "auth.json"
auth.write_text(json.dumps({"auths": {"harbor.example.de": {"auth": "eW8="}}}), encoding="utf-8")
real_env = os.environ.get("REGISTRY_AUTH_FILE")
os.environ["REGISTRY_AUTH_FILE"] = str(auth)
try:
    check("a credential for that registry counts as logged in",
          nr.logged_in("harbor.example.de") is True)
    check("one for another registry does not",
          nr.logged_in("other.example.de") is False)
    auth.write_text("not json", encoding="utf-8")
    check("an unreadable auth file is not a login",
          nr.logged_in("harbor.example.de") is False)
finally:
    if real_env is None:
        del os.environ["REGISTRY_AUTH_FILE"]
    else:
        os.environ["REGISTRY_AUTH_FILE"] = real_env

tag = "harbor.example.de/dap-node-red/wfm-prod:4.0.9-1"
away = nr.pull_hint("docker", tag, here=False)
check("without a login here it names the workstation pull",
      f"podman pull {tag}" in away, away)
check("and says why, so the login is not the first thing tried",
      "no login" in away and "would fail here" in away, away)
check("the login stays as the fallback, on the registry not the image",
      away.index("podman pull") < away.index("docker login harbor.example.de\n"), away)
here = nr.pull_hint("docker", tag, here=True)
check("with a login here it just says it is pulling, and asks for nothing",
      "Pulling" in here and "login" not in here, here)

# And the run stops there rather than handing compose a pull that cannot work.
real = (nr.image_present, nr.logged_in, nr.compose_cmd, nr.run)
nr.image_present = lambda compose, tag: False
nr.logged_in = lambda registry: False
nr.compose_cmd = lambda name: ["engine", "compose"]
nr.run = lambda *a, **k: check("compose is never reached", False)
try:
    inst = {"name": APP, "app": APP, "image_tag": tag}
    check("--baked stops when the image is missing and there is no login here",
          nr.act("edit", inst, {}, baked=True) == 1)
finally:
    nr.image_present, nr.logged_in, nr.compose_cmd, nr.run = real

# The override joins an existing network rather than creating one, which is
# what makes the name resolvable where the network has DNS.
nr.stage_network(APP, "devcontainer_default")
body = (nr.SESSION / APP / "network.yml").read_text(encoding="utf-8")
check("the network override marks it external", "external: true" in body, body)
check("and names the network to join", "devcontainer_default:" in body, body)

# CONTAINER_ENGINE used to be taken on trust, which turned a typo into a
# FileNotFoundError from whichever engine call ran first.
os.environ["CONTAINER_ENGINE"] = "no-such-engine"
try:
    try:
        nr.compose_cmd(APP)
        check("an engine that is not installed is refused", False)
    except SystemExit as stop:
        check("an engine that is not installed is refused, not used",
              "not on PATH" in str(stop), str(stop))
finally:
    del os.environ["CONTAINER_ENGINE"]

# The lifecycle: start detached so the address can be read, follow the log, and
# stop the container on the way out. A foreground `up` left no chance to print
# an address, and left the container running after some exits.
calls = []
real_run = nr.run
real_engine = os.environ.get("CONTAINER_ENGINE")
# The stub engine from above, so this tests the call sequence rather than
# whether a container engine happens to be installed — CI images have none.
os.environ["CONTAINER_ENGINE"] = str(stub / "engine")
nr.run = lambda argv, env=None: (calls.append(list(argv)), 0)[1]
try:
    nr.act("edit", nodered.find(APP), {})
finally:
    nr.run = real_run
    if real_engine is None:
        del os.environ["CONTAINER_ENGINE"]
    else:
        os.environ["CONTAINER_ENGINE"] = real_engine
def subcommand(call: list[str]) -> str:
    """What compose was asked to do, with the -f file pairs dropped."""
    last_file = max(i for i, part in enumerate(call) if part.endswith(".yml"))
    return " ".join(call[last_file + 1:])


sequence = [subcommand(c) for c in calls if "compose" in c]
check("it starts detached, follows the log, then stops",
      sequence == ["up -d", "logs -f", "down"], str(sequence))

print("nr.py editor session")
original_bytes = LIVE.read_bytes()
try:
    check("the fixture has the tabs these tests need", len(tabs(json.loads(original_bytes))) == 2)
    # --- staging ----------------------------------------------------------
    data, was, labels = nr.stage_session(APP)
    check("session directory is not the app directory", data == f".editor-session/{APP}", data)
    check("the app file is untouched by staging", LIVE.read_bytes() == original_bytes)

    original = json.loads(original_bytes.decode("utf-8"))
    check("every tab arrives disabled",
          all(t["disabled"] for t in tabs(staged_flows()).values()) and len(labels) == len(tabs(original)))
    check("nothing else is dropped", len(staged_flows()) == len(original))

    # --- merge with no changes -------------------------------------------
    nr.merge_session(APP, was)
    check("an untouched session leaves the app file byte-identical",
          LIVE.read_bytes() == original_bytes)

    # --- a tab enabled locally, and a node edited ------------------------
    nr.stage_session(APP)
    flows = staged_flows()
    tab_id = next(iter(tabs(flows)))
    tabs(flows)[tab_id]["disabled"] = False              # what you do to work on it
    for node in flows:
        if node.get("type") == "debug":
            node["name"] = "Ergebnis, umbenannt"
    write_staged(flows)
    nr.merge_session(APP, was)

    merged = json.loads(LIVE.read_text(encoding="utf-8"))
    check("the tab's disabled state comes back from Git",
          tabs(merged)[tab_id]["disabled"] == was[tab_id], str(tabs(merged)[tab_id]["disabled"]))
    check("the edit inside the tab survives",
          any(n.get("name") == "Ergebnis, umbenannt" for n in merged))

    # --- a tab added locally ---------------------------------------------
    nr.stage_session(APP)
    flows = staged_flows()
    flows.append({"id": "aaaa1111bbbb2222", "type": "tab", "label": "Neu", "disabled": False, "info": ""})
    write_staged(flows)
    added = nr.merge_session(APP, was)

    merged = tabs(json.loads(LIVE.read_text(encoding="utf-8")))
    check("a new tab is kept", "aaaa1111bbbb2222" in merged)
    check("and keeps its own enabled state", merged.get("aaaa1111bbbb2222", {}).get("disabled") is False)
    check("and is reported to the caller", added == ["Neu"], str(added))

    # --- a tab deleted locally -------------------------------------------
    nr.stage_session(APP)
    flows = [n for n in staged_flows() if n.get("id") != "aaaa1111bbbb2222"]
    write_staged(flows)
    nr.merge_session(APP, was)
    check("a deleted tab stays deleted",
          "aaaa1111bbbb2222" not in tabs(json.loads(LIVE.read_text(encoding="utf-8"))))

    # --- a tab that Git says is disabled ---------------------------------
    nr.stage_session(APP)
    flows = staged_flows()
    tabs(flows)[tab_id]["disabled"] = False
    write_staged(flows)
    nr.merge_session(APP, {tab_id: True})
    check("a tab Git has disabled stays disabled",
          tabs(json.loads(LIVE.read_text(encoding="utf-8")))[tab_id]["disabled"] is True)
    # --- the mount did not land ------------------------------------------
    nr.stage_session(APP)
    write_staged([])                                     # what an empty /data yields
    before = LIVE.read_bytes()
    try:
        nr.merge_session(APP, was)
        check("an empty session is refused, not copied back", False, "no SystemExit")
    except SystemExit as exc:
        check("an empty session is refused, not copied back", "no tabs" in str(exc))
    check("and the app file is untouched by the refusal", LIVE.read_bytes() == before)

    # --- palette: what "Manage palette" installs has to reach the app ------
    # It runs npm in the session's /data, which is gitignored, so without this
    # the module is real locally and absent everywhere that matters.
    APP_PKG = ROOT / "apps" / APP / "package.json"
    pkg_before = APP_PKG.read_bytes()
    session = nr.SESSION / APP
    try:
        (session / "package.json").write_text(json.dumps(
            {"dependencies": {"node-red-contrib-fake": "^1.2.3",
                              "node-red-contrib-opcua": "^0.9.9"}}), encoding="utf-8")
        mod = session / "node_modules" / "node-red-contrib-fake"
        mod.mkdir(parents=True, exist_ok=True)
        (mod / "package.json").write_text(json.dumps({"version": "1.2.4"}), encoding="utf-8")

        before_deps = json.loads(pkg_before)["dependencies"]
        added = nr.merge_palette(APP)
        deps = json.loads(APP_PKG.read_text(encoding="utf-8"))["dependencies"]

        check("a module installed in the editor lands in the app's palette",
              "node-red-contrib-fake" in deps, str(deps))
        check("pinned to the version npm actually installed, not the range",
              deps.get("node-red-contrib-fake") == "1.2.4",
              str(deps.get("node-red-contrib-fake")))
        check("and it is reported", added == ["node-red-contrib-fake@1.2.4"], str(added))
        check("a module the app already pins is left alone",
              deps.get("node-red-contrib-opcua") == before_deps.get("node-red-contrib-opcua"),
              str(deps.get("node-red-contrib-opcua")))
        check("nothing is dropped", set(before_deps) <= set(deps))

        again = nr.merge_palette(APP)
        check("a second session adds nothing twice", again == [], str(again))

        # A new palette is a new image, and the tag the deploy pins is in
        # registry.yml — so the bump belongs in the same commit, not in a
        # human's memory. CI pushes the tag it finds there.
        REG = TMP / "registry.yml"
        reg_before = REG.read_bytes()
        try:
            inst = nr.nodered.find(APP)
            was_tag = nr.nodered.tag_short(inst["image_tag"])
            other_tag = nr.nodered.tag_short(nr.nodered.find("wfm-prod")["image_tag"])
            bumped = nr.bump_palette_tag(inst)
            check("the palette build is raised", bumped and bumped[1].endswith("-2"), str(bumped))
            raised = nr.nodered.tag_short(bumped[1])
            after = REG.read_text(encoding="utf-8")
            check("only that instance's tag moved",
                  after.count(raised) == 1 and other_tag in after, f"{raised} / {other_tag}")
            check("the line keeps its trailing comment",
                  "#" in after.split(raised)[1].split("\n")[0],
                  after.split(raised)[1].split("\n")[0])
            check("and the tag it replaced is gone", was_tag not in after, was_tag)
            check("and every other comment in the file survives",
                  after.count("#") == reg_before.decode().count("#"))
            check("a tag with no numeric build is refused, not guessed",
                  nr.bump_palette_tag({**inst, "image_tag": "repo/app:4.0.9"}) is None)
        finally:
            REG.write_bytes(reg_before)

        # The baked palette is in the image, not under /data, so an empty
        # session list must never be read as "the app has no palette".
        (session / "package.json").write_text(json.dumps({"dependencies": {}}), encoding="utf-8")
        nr.merge_palette(APP)
        check("an empty session leaves the palette intact",
              json.loads(APP_PKG.read_text(encoding="utf-8"))["dependencies"] == deps)
    finally:
        APP_PKG.write_bytes(pkg_before)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
