#!/usr/bin/env python3
"""One entry point for the day-to-day work. Pick an instance, pick an action.

    python3 scripts/nr.py                      # menu
    python3 scripts/nr.py status               # every instance, one table
    python3 scripts/nr.py check    wag-prod
    python3 scripts/nr.py edit     wag-prod
    python3 scripts/nr.py edit     gor-prod --baked      # editor with that app's palette
    python3 scripts/nr.py edit     srem-test --isolated  # editor with no way out
    python3 scripts/nr.py capture  wag-prod
    python3 scripts/nr.py deploy   wag-prod    # dry run; it never deploys for real
    python3 scripts/nr.py promote  wfm-prod wfm-test "Extruder abfrage" --copy
    python3 scripts/nr.py promote  wfm-test wfm-prod "Extruder abfrage" --move

This exists so the instance list lives in exactly one place. A menu with the
thirteen names typed into it would be a second copy of `registry.yml`, and the
copy would go stale the first time an instance is added.

Where each instance answers from a workstation, and the login for it, come from
a gitignored `nr.local.json`. Copy `nr.local.example.json` and fill it in. A
password left out is asked for at the prompt and is not stored.

`deploy` is deliberately dry-run only. A real deploy is a reviewed commit that
Jenkins carries out — a local script that can write to production would make
that reviewable path optional.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guide  # noqa: E402
import nodered  # noqa: E402
from guide import choose  # noqa: E402
from normalize import normalize, render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "nr.local.json"
PY = sys.executable

ACTIONS = {
    "guide":   "walk me through a whole task, step by step",
    "status":  "every instance at once: does it still match Git?",
    "check":   "one instance: does it still match Git?",
    "edit":    "start the local editor on this app (isolated, no live nodes)",
    "capture": "read the running flow back into apps/, to commit it",
    "deploy":  "show what a deploy would change; never deploys for real",
    "promote": "move one tab between two instances' apps, with its dependencies",
}


def local_config() -> dict:
    """Read nr.local.json, tolerating the // header the example file carries.

    Without this the example file is a trap: copying it as instructed produces
    a file that json.loads rejects.
    """
    if not LOCAL.exists():
        return {}
    text = "\n".join(line for line in LOCAL.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("//"))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        sys.exit(f"{LOCAL.name} is not valid JSON: {exc}")


def build_env(inst: dict, cfg: dict, need_password: bool) -> dict:
    """The base URL and login for one instance, as deploy.py expects them."""
    env = dict(os.environ)
    entry = cfg.get(inst["name"], {})

    url = entry.get("url")
    if not url:
        sys.exit(f"{inst['name']}: no url in nr.local.json.\n"
                 f"  Copy nr.local.example.json and fill it in — the table in\n"
                 f"  docs/runbook.md lists where each instance answers.")
    env[nodered.base_url_env(inst["name"])] = url

    if need_password:
        stem = nodered.credential_stem(inst["auth_credential_id"])
        user = entry.get("user")
        password = entry.get("password") or (
            getpass.getpass(f"password for {inst['name']} ({user or 'admin'}): ")
            if user else None)
        if user and password:
            env[f"{stem}_USR"], env[f"{stem}_PSW"] = user, password
    return env


def run(argv: list[str], env: dict | None = None) -> int:
    print(f"\n$ {' '.join(argv)}\n")
    try:
        return subprocess.run(argv, cwd=ROOT, env=env).returncode
    except FileNotFoundError:
        # Windows raises WinError 2 here, which arrives as a traceback ten
        # frames deep and says nothing about which program is missing.
        sys.exit(f"{argv[0]} is not on PATH, so this action cannot run.")


SESSION = ROOT / ".editor-session"


def stage_session(app: str) -> tuple[str, dict[str, bool], list[str]]:
    """Copy the app's flow into a session directory, every tab disabled.

    The editor writes flows.json wherever /data is mounted, so mounting
    apps/<app>/ directly means the local run and the committed file are the
    same thing — and then switching a tab off to work safely would be a change
    on its way to an instance. Staging a copy keeps the two apart.
    """
    flows = json.loads((ROOT / "apps" / app / "flows.json").read_text(encoding="utf-8"))
    was = {n["id"]: bool(n.get("disabled", False)) for n in flows if n.get("type") == "tab"}
    labels = []
    for node in flows:
        if node.get("type") == "tab":
            node["disabled"] = True
            labels.append(node.get("label") or node["id"])

    directory = SESSION / app
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "flows.json").write_text(json.dumps(flows, indent=2) + "\n", encoding="utf-8")
    return f".editor-session/{app}", was, labels


def stage_dns(app: str, search: list[str]) -> str | None:
    """A compose override carrying the instance's search domains.

    Flows address their targets the way their own host resolves them, and some
    do it unqualified — wfm-prod's broker is plain `dpn-svr-iot`. A normal
    network is not enough for that: without the instance's dns_search the name
    does not resolve, and the node goes red for a reason that looks like the
    broker being down.

    They differ per instance and compose cannot interpolate a list, so this is
    written next to the staged flow rather than parameterised in editor.yml.
    """
    if not search:
        return None
    body = ["services:", "  editor:", "    dns_search:"]
    body += [f"      - {domain}" for domain in search]
    path = SESSION / app / "dns.yml"
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return f".editor-session/{app}/dns.yml"


def own_network(compose: list[str]) -> str | None:
    """The network this container is on, when we are running inside one.

    A sibling container on a network of its own is reachable only by an address
    that changes every run, and VS Code forwards ports from inside this
    container. Putting the editor on the same network makes it reachable by
    name, which is stable enough to forward once and keep.
    """
    if not os.environ.get("REMOTE_CONTAINERS") and not os.environ.get("LOCAL_WORKSPACE_FOLDER"):
        return None
    me = os.environ.get("HOSTNAME")
    if not me:
        return None
    out = subprocess.run(
        [compose[0], "inspect", "-f",
         "{{range $name, $conf := .NetworkSettings.Networks}}{{$name}} {{end}}", me],
        capture_output=True, text=True)
    names = out.stdout.split() if out.returncode == 0 else []
    return names[0] if names else None


def stage_network(app: str, network: str) -> str:
    """A compose override that joins an existing network instead of creating one."""
    body = ["networks:", f"  {network}:", "    external: true"]
    path = SESSION / app / "network.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return f".editor-session/{app}/network.yml"


def session_digest(app: str) -> str | None:
    """Fingerprint of the session's flow, to tell "the editor wrote" from "it never ran".

    A compose run that fails — an image it cannot pull, a port already taken —
    leaves the staged copy exactly as staged. Merging that back is harmless,
    because the merge restores every tab's state from Git and lands on the same
    bytes, but reporting it as "copied back" points the reader at a diff that
    does not exist while the actual failure scrolls past above.
    """
    h = hashlib.sha256()
    seen = False
    for name in ("flows.json", "package.json"):
        f = SESSION / app / name
        if f.exists():
            h.update(f.read_bytes())
            seen = True
    return h.hexdigest() if seen else None


def merge_session(app: str, was: dict[str, bool]) -> list[str]:
    """Bring the session's flow back, restoring what Git said about each tab.

    A tab that existed before keeps the disabled state from Git, whatever it
    was switched to locally — that switching is how you work here, not
    something to deploy. A tab you added is new, so it keeps its own state.
    """
    staged = SESSION / app / "flows.json"
    if not staged.exists():
        return []

    flows = json.loads(staged.read_text(encoding="utf-8"))

    # If /data did not mount, Node-RED starts on an empty userDir and writes a
    # flow with nothing in it — and copying that back would delete the app.
    # An editor showing no tabs at all is that failure, not an empty app: the
    # staged copy always has at least the tabs the app has.
    if was and not any(n.get("type") == "tab" for n in flows):
        sys.exit(
            f"the session for {app} came back with no tabs, and the app has "
            f"{len(was)}.\n"
            "  Nothing was written. The editor was almost certainly looking at an\n"
            "  empty directory — the mount did not land, which on podman for\n"
            "  Windows usually means the path is not shared into the machine.\n"
            f"  The session is still there: .editor-session/{app}/flows.json"
        )

    added = []
    for node in flows:
        if node.get("type") != "tab":
            continue
        if node["id"] in was:
            node["disabled"] = was[node["id"]]
        else:
            added.append(node.get("label") or node["id"])

    (ROOT / "apps" / app / "flows.json").write_text(render(normalize(flows)), encoding="utf-8")
    return added


def installed_version(app: str, module: str) -> str | None:
    """The version npm actually put in the session, read off the module itself."""
    manifest = SESSION / app / "node_modules" / module / "package.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except json.JSONDecodeError:
        return None


def merge_palette(app: str) -> list[str]:
    """Carry a module installed through "Manage palette" into the app's palette.

    That install runs npm in the session's /data, so the module is real and
    resolved — and invisible to everything else: the session directory is
    gitignored and only flows.json was ever copied out of it. Writing it into
    apps/<app>/package.json is what ships it, and the version it resolved beats
    one typed from memory.

    Additive on purpose. The baked palette lives in the image, not under /data,
    so a name missing from the session means "already in the image", never
    "removed" — a two-way sync would empty the manifest on the first session.
    """
    session_pkg = SESSION / app / "package.json"
    app_pkg = ROOT / "apps" / app / "package.json"
    if not (session_pkg.exists() and app_pkg.exists()):
        return []
    try:
        installed = json.loads(session_pkg.read_text(encoding="utf-8")).get("dependencies") or {}
    except json.JSONDecodeError:
        return []

    manifest = json.loads(app_pkg.read_text(encoding="utf-8"))
    deps = dict(manifest.get("dependencies") or {})
    added = []
    for module, declared in sorted(installed.items()):
        if module in deps:
            continue
        # A range would make the built image drift from the one tested here.
        exact = installed_version(app, module) or declared.lstrip("^~>=< ")
        deps[module] = exact
        added.append(f"{module}@{exact}")

    if added:
        manifest["dependencies"] = dict(sorted(deps.items()))
        app_pkg.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return added


def bump_palette_tag(inst: dict) -> tuple[str, str] | None:
    """Raise the palette-build suffix of this instance's image_tag.

    A new palette means a new image, and CI pushes the tag registry.yml names —
    so an unchanged tag is rebuilt with different content under the same name,
    which nothing reports afterwards. None when the tag has no numeric suffix
    to raise, because guessing one would name an image CI never built.
    """
    old = inst["image_tag"]
    head, sep, build = old.rpartition("-")
    if not sep or not build.isdigit():
        return None
    new = f"{head}-{int(build) + 1}"
    nodered.set_image_tag(inst["name"], new)
    return old, new


def editor_bind(compose: list[str], environ) -> str:
    """The address to publish the editor's port on.

    Loopback only reaches you when the daemon runs on this machine. podman on
    Windows or macOS keeps it in a VM, and a dev container talks to the host's
    daemon, so there the port has to go on every interface for the VM to
    forward it out. Otherwise it is published somewhere nothing can reach and
    the editor looks dead.
    """
    in_vm = compose[0] == "podman" or bool(environ.get("LOCAL_WORKSPACE_FOLDER"))
    return "0.0.0.0" if in_vm else "127.0.0.1"


class _Relay(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    target: tuple[str, int]


class _Pipe(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection(self.server.target, timeout=5)
        except OSError:
            return
        with upstream:
            done = threading.Event()

            def pump(src: socket.socket, dst: socket.socket) -> None:
                try:
                    while chunk := src.recv(65536):
                        dst.sendall(chunk)
                except OSError:
                    pass
                finally:
                    done.set()

            for a, b in ((self.request, upstream), (upstream, self.request)):
                threading.Thread(target=pump, args=(a, b), daemon=True).start()
            done.wait()


def relay(target: str, port: int = 1880) -> _Relay | None:
    """Listen on this container's own localhost and forward to the editor.

    VS Code forwards a port number, which it resolves against localhost inside
    this container. The editor is a sibling container, so nothing listens there
    and no network or publish setting changes that: the port is published on
    the engine's machine, which is not this container and not the browser's
    host either. One hop on localhost is what closes that gap, and it leaves
    the editor's own network, DNS and isolation untouched.
    """
    try:
        server = _Relay(("127.0.0.1", port), _Pipe)
    except OSError:
        return None
    server.target = (target, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def answers(url: str, timeout: float = 1.5) -> bool:
    """Whether something serves HTTP there, right now."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except Exception:
        return False


def image_present(compose: list[str], tag: str) -> bool:
    """Whether the engine already has this image, so no pull and no login."""
    out = subprocess.run([compose[0], "images", "-q", tag],
                         capture_output=True, text=True)
    return bool(out.returncode == 0 and out.stdout.strip())


def logged_in(registry: str) -> bool:
    """Whether this client holds a credential for that registry.

    Asked rather than assumed, because a login belongs to the client that ran
    it: the auth file a `podman login` wrote on the workstation is in that
    user's home and a container cannot read it.
    """
    files = [Path(os.environ["REGISTRY_AUTH_FILE"])] if os.environ.get("REGISTRY_AUTH_FILE") else []
    files += [Path.home() / ".docker" / "config.json",
              Path.home() / ".config" / "containers" / "auth.json"]
    for auth in files:
        try:
            entries = json.loads(auth.read_text(encoding="utf-8")).get("auths") or {}
        except (OSError, ValueError):
            continue
        if any(host == registry or host.endswith("/" + registry) for host in entries):
            return True
    return False


def pull_hint(engine: str, tag: str, here: bool) -> str:
    """What to do when the engine lacks a baked image.

    The pull is the engine's and so is the image once it has it, but the login
    is the client's. So when this client has no credential the pull has to be
    run where one exists — on the workstation, for a dev container — and every
    later session then finds the image without logging in anywhere.
    """
    if here:
        return (f"\nPulling {tag}\nfirst; the engine does not have it yet.\n")
    return (f"\n{tag}\nis not on the engine yet, and this client has no login for\n"
            f"{nodered.tag_registry(tag)}, so the pull would fail here.\n"
            f"\n"
            f"Run this on your workstation, where you are logged in:\n"
            f"    podman pull {tag}\n"
            f"\n"
            f"The image belongs to the engine, which both clients share, so one pull is\n"
            f"enough — this session then finds it and needs no login. Logging in here\n"
            f"instead works too, but that credential is gone on the next rebuild:\n"
            f"    {engine} login {nodered.tag_registry(tag)}\n")


def editor_address(compose: list[str]) -> str | None:
    """The editor container's own address, asked of the engine."""
    out = subprocess.run(
        [compose[0], "inspect", "-f",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", "node-red-editor"],
        capture_output=True, text=True)
    found = out.stdout.split() if out.returncode == 0 else []
    return found[0] if found else None


def editor_urls(candidates: list[tuple[str, str]], engine: str) -> str:
    """Where to open the editor, out of the candidates that answered.

    Printed after probing rather than derived from where the engine runs. Four
    rounds went into guessing this: a published port only helps if the machine
    publishing it runs the browser, a container name only resolves if the
    network has DNS, and podman's default network does not. So ask.
    """
    live = [(url, why) for url, why in candidates if answers(url)]
    if not live:
        return ("\neditor is up, and nothing answered on the addresses this machine\n"
                f"can see. Ask the engine where it put the port:\n"
                f"    {engine} port node-red-editor\n")

    lines = ["", "editor is up. These answered just now:"]
    lines += [f"  {url}   {why}" for url, why in live]
    if not any(url.startswith("http://localhost") for url, _ in live):
        lines += ["",
                  "  localhost did not, so the browser is outside this machine.",
                  "  Forward the line above: VS Code Ports panel, Forward a Port."]
    return "\n".join(lines) + "\n"


def editor_candidates(compose: list[str], in_vm: bool, shared: str | None) -> list[tuple[str, str]]:
    """Every address the editor might answer on, best first."""
    found = []
    if shared:
        found.append(("http://node-red-editor:1880", "by name, if the network has DNS"))
    address = editor_address(compose)
    if address:
        found.append((f"http://{address}:1880", "the container's own address"))
    found.append(("http://localhost:1880", "the published port"))
    return found


def compose_cmd(instance: str) -> list[str]:
    """`docker compose` or `podman compose`, whichever this machine has.

    Workstations here run podman and the servers run Docker, so a hard-coded
    engine breaks `edit` on exactly the machines it exists for.
    """
    engine = os.environ.get("CONTAINER_ENGINE")
    if engine and (shutil.which(engine) or Path(engine).exists()):
        return [engine, "compose"]
    if engine:
        # Taken on trust, this surfaced later as a FileNotFoundError from
        # whichever call happened to run first.
        sys.exit(f"CONTAINER_ENGINE names {engine!r}, which is not on PATH.")
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return [candidate, "compose"]
    sys.exit(
        "The local editor needs a container engine, and neither docker nor\n"
        "  podman is on PATH. Set CONTAINER_ENGINE if yours is called something\n"
        "  else. It is the only action that needs one: status, check, capture\n"
        "  and deploy talk to the Admin API and need nothing but Python.\n"
        "\n"
        "  For a test instance the other route is the intended one anyway —\n"
        "  edit in that instance's own editor, then bring the change back:\n"
        f"      python3 scripts/nr.py capture {instance}\n"
        "  See docs/runbook.md, 'Changing a flow', route B.\n"
        "\n"
        "  An engine inside WSL is not enough: this runs as a Windows process\n"
        "  and needs the .exe on the Windows PATH."
    )


def act(action: str, inst: dict | None, cfg: dict, baked: bool = False,
        isolated: bool = False) -> int:
    if action == "status":
        env = dict(os.environ)
        for i in nodered.instances():
            if not i.get("app"):
                continue
            entry = cfg.get(i["name"], {})
            if entry.get("url"):
                env.update(build_env(i, cfg, need_password=bool(entry.get("password"))))
        return run([PY, "scripts/drift-check.py", "--all"], env)

    assert inst is not None
    if action == "edit":
        if not inst.get("app"):
            sys.exit(f"{inst['name']} has no app — there is no flow to edit. "
                     f"See open question 3 in docs/open-questions.md.")
        compose = compose_cmd(inst["name"])
        data, was, labels = stage_session(inst["app"])
        env_bind = editor_bind(compose, os.environ)
        in_vm = env_bind != "127.0.0.1"

        print(f"\nEditor for {inst['name']} -> {data}/ (a copy, not apps/{inst['app']}/)\n"
              f"It prints the address to open once it is up. Ctrl-C finishes.\n"
              f"\n"
              f"{len(labels)} tab(s) arrive DISABLED: {', '.join(labels) or '—'}\n"
              f"Enable the one you want to work on, or add a new tab. Only what you\n"
              f"enable runs — and it runs for real, against real systems.\n"
              f"\n"
              f"On Ctrl-C the flow is copied into apps/{inst['app']}/flows.json with each\n"
              f"existing tab's disabled state restored from Git, so the switching stays\n"
              f"local. Review it with git diff.\n")
        # Inside a dev container the docker daemon is the host's, so the bind
        # mount must name a host path. LOCAL_WORKSPACE_FOLDER is what the dev
        # container sets to that path; outside one it is unset and the compose
        # file falls back to its own relative path.
        env = {**os.environ, "APP": inst["app"], "EDITOR_BIND": env_bind}
        if os.environ.get("LOCAL_WORKSPACE_FOLDER"):
            env["REPO_ROOT"] = os.environ["LOCAL_WORKSPACE_FOLDER"]

        # The registry pins each instance to the Node-RED version it runs, and
        # the editor has to match it: a 5.x editor writes fields a 4.0.x runtime
        # does not know, into a file that is meant to deploy unchanged.
        # harbor.example/dap-node-red/wfm-prod:4.0.9-1 -> 4.0.9
        version = nodered.tag_version(inst["image_tag"])
        env["NODE_RED_VERSION"] = version
        if baked:
            # That app's own image, so its palette nodes open as themselves
            # rather than as "unknown".
            env["EDITOR_IMAGE"] = inst["image_tag"]
            # Stop before the compose run when the pull cannot succeed: the
            # engine lacks the image and this client has no credential for it.
            if not image_present(compose, inst["image_tag"]):
                here = logged_in(nodered.tag_registry(inst["image_tag"]))
                print(pull_hint(compose[0], inst["image_tag"], here))
                if not here:
                    return 1
        env["EDITOR_DATA"] = data
        files = ["-f", "compose/editor.yml"]
        if isolated:
            env["EDITOR_NETWORK"] = "isolated"
        else:
            # Pointless with no gateway, so only for the networked default.
            override = stage_dns(inst["app"], inst.get("dns_search") or [])
            if override:
                files += ["-f", override]
                print(f"search domains: {', '.join(inst['dns_search'])}")
            shared = own_network(compose)
            if shared:
                files += ["-f", stage_network(inst["app"], shared)]
                env["EDITOR_NETWORK"] = shared
        print(f"editor image:   {env.get('EDITOR_IMAGE', 'nodered/node-red:' + version)}")
        print(f"editor network: {env.get('EDITOR_NETWORK', 'bridged')}"
              f"{'  (no route out)' if isolated else '  (databases and brokers reachable)'}")
        print(f"editor port:    {env_bind}:1880"
              f"{'  (the engine runs in a VM, so loopback alone would not reach you)' if in_vm else ''}\n")
        staged = session_digest(inst["app"])
        code = None
        hop = None
        try:
            code = run([*compose, *files, "up", "-d"], env)
            if code == 0:
                candidates = editor_candidates(compose, in_vm, env.get("EDITOR_NETWORK"))
                # In a container, VS Code can only forward a port it finds on
                # this container's localhost, so put one there.
                address = editor_address(compose)
                if address and os.environ.get("LOCAL_WORKSPACE_FOLDER"):
                    hop = relay(address)
                    if hop:
                        candidates.insert(0, ("http://localhost:1880",
                                              "forwarded from this container to "
                                              f"{address}, so VS Code can pick it up"))
                # compose returns when the container started, not when Node-RED
                # is listening, and the probe is the whole point of this line.
                for _ in range(20):
                    if any(answers(url, timeout=0.5) for url, _ in candidates):
                        break
                    time.sleep(0.5)
                print(editor_urls(candidates, compose[0]))
                code = run([*compose, *files, "logs", "-f"], env)
            return code
        finally:
            if hop:
                hop.shutdown()
            run([*compose, *files, "down"], env)
            # Also on Ctrl-C, which is the normal way to end an editor session.
            if session_digest(inst["app"]) == staged:
                print(f"\nthe editor wrote no flow, so apps/{inst['app']}/flows.json is "
                      f"untouched.")
                if code:
                    registry = nodered.tag_registry(inst["image_tag"])
                    print(f"  The compose run above failed. 'unauthorized ... action: pull'\n"
                          f"  is a missing registry login, and the login belongs to the engine\n"
                          f"  that pulls — which is this one, whatever the compose provider is\n"
                          f"  called: `{compose[0]} compose` points the provider at its own\n"
                          f"  socket, so 'Error response from daemon' can be {compose[0]}\n"
                          f"  answering through the Docker-compatible API.\n"
                          f"    {compose[0]} login {registry}\n"
                          f"  Then confirm the image is reachable before retrying:\n"
                          f"    {compose[0]} pull {inst['image_tag']}")
            else:
                added = merge_session(inst["app"], was)
                print(f"\ncopied back into apps/{inst['app']}/flows.json"
                      f"{' — new tab(s) kept as you left them: ' + ', '.join(added) if added else ''}")
                print(f"  git diff apps/{inst['app']}/flows.json")
                palette = merge_palette(inst["app"])
                if palette:
                    print(f"\npalette: {', '.join(palette)} written into "
                          f"apps/{inst['app']}/package.json, pinned to what you installed.")
                    bumped = bump_palette_tag(inst)
                    if bumped:
                        print(f"  image_tag: {nodered.tag_short(bumped[0])} -> "
                              f"{nodered.tag_short(bumped[1])} in registry.yml, so CI builds a\n"
                              f"  new tag instead of replacing the one this instance runs.")
                    else:
                        print(f"  image_tag for {inst['name']} does not end in -<number>, so the\n"
                              f"  palette build could not be raised. Do it by hand before pushing:\n"
                              f"  CI pushes the tag registry.yml names, and an unchanged tag gets\n"
                              f"  rebuilt with different content.")
                    print(f"  A flow deploy installs nothing, so this needs the other transport:\n"
                          f"  commit both, let CI build, then deploy with DEPLOY_PALETTE=true.\n"
                          f"  docs/runbook.md, 'Palette change'.")

    env = build_env(inst, cfg, need_password=True)
    script = {"check": "drift-check.py", "capture": "capture.py", "deploy": "deploy.py"}[action]
    args = [PY, f"scripts/{script}", "--instance", inst["name"]]
    if action == "check":
        args.append("--show-diff")
    if action in ("capture", "deploy"):
        args.append("--dry-run")
    code = run(args, env)

    if action == "capture" and code == 0:
        if input("\nwrite it into apps/ for real? [y/N] ").strip().lower() == "y":
            code = run([PY, "scripts/capture.py", "--instance", inst["name"]], env)
    return code


def main() -> int:
    cfg = local_config()
    all_instances = nodered.instances()

    if {"--help", "-h"} & set(sys.argv[1:]):
        print(__doc__.strip().split("\n\n")[0])
        print("\nactions")
        for name, what in ACTIONS.items():
            print(f"  {name:<10} {what}")
        print("\nguided tasks (nr.py guide <task>)")
        for key, task in guide.TASKS.items():
            print(f"  {key:<12} {task['title']}")
        return 0

    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    known = {"--baked", "--isolated", "--copy", "--move", "--dry-run"}
    if flags - known:
        sys.exit(f"unknown option(s): {', '.join(sorted(flags - known))}. "
                 f"Understood: {', '.join(sorted(known))}, and only for edit.")

    action = argv[0] if argv else None
    if action == "guide":
        return guide.walk(argv[1] if len(argv) > 1 else choose(
            "What are you here to do?",
            [(key, task["title"]) for key, task in guide.TASKS.items()]))
    if action and action not in ACTIONS:
        sys.exit(f"unknown action '{action}'. One of: {', '.join(ACTIONS)}")
    if not action:
        action = choose("What do you want to do?", [("guide", ACTIONS["guide"])]
                        + [(k, v) for k, v in ACTIONS.items() if k != "guide"])
        if action == "guide":
            return guide.walk(choose(
                "What are you here to do?",
                [(key, task["title"]) for key, task in guide.TASKS.items()]))

    if action == "status":
        return act(action, None, cfg)

    if action == "promote":
        # Two instances and a tab, so the instance picker does not fit. The
        # instance names are the vocabulary everywhere else, so they are the
        # vocabulary here too, and this translates them to app directories.
        if len(argv) < 4:
            sys.exit("usage: nr.py promote <from-instance> <to-instance> <tab> "
                     "--copy|--move [--dry-run]\n"
                     "  --copy for prod -> workbench, --move for workbench -> prod.\n"
                     "  See docs/runbook.md, 'Changing a flow'.")
        apps = {}
        for name in argv[1:3]:
            inst = nodered.find(name)
            if not inst.get("app"):
                sys.exit(f"{name} has no app of its own, so there is nothing to promote")
            apps[name] = inst["app"]
        return run([PY, "scripts/promote.py",
                    "--from", apps[argv[1]], "--to", apps[argv[2]], "--tab", argv[3],
                    *sorted(flags & {"--copy", "--move", "--dry-run"})])

    name = argv[1] if len(argv) > 1 else None
    if not name:
        options = [
            (i["name"], f"{i['host']:<16} {'app: ' + i['app'] if i.get('app') else 'no app — empty'}"
                        f"{'' if cfg.get(i['name'], {}).get('url') else '   [no url in nr.local.json]'}")
            for i in all_instances
        ]
        name = choose("Which instance?", options)

    inst = nodered.find(name)
    return act(action, inst, cfg, baked="--baked" in flags,
               isolated="--isolated" in flags)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        # Ctrl-C and Ctrl-D both mean "not now", and neither is a crash.
        print()
        sys.exit(130)
