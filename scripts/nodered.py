#!/usr/bin/env python3
"""What the flow tools share: the instance list, where an instance answers,
and the Admin API.

Standard library only. Jenkins ships this and deploy.py to a site host, and a
site host is not guaranteed to have pip.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 30

# Named after the ids in registry.yml, with every non-alphanumeric character
# replaced by _ and the whole thing upper-cased. The Jenkinsfile builds the
# same names in Groovy when it writes the credentials into the environment.
_ENV_SAFE = re.compile(r"[^A-Za-z0-9]")


def credential_stem(credential_id: str) -> str:
    """`nodered-wag-prod-auth` -> `NODERED_WAG_PROD_AUTH`, whose _USR and _PSW hold the login."""
    return _ENV_SAFE.sub("_", credential_id).upper()


def base_url_env(instance: str) -> str:
    """`wag-prod` -> `NODE_RED_BASE_URL_WAG_PROD`, the per-instance base URL override."""
    return f"NODE_RED_BASE_URL_{_ENV_SAFE.sub('_', instance).upper()}"


# --------------------------------------------------------------------------
# The instance list
# --------------------------------------------------------------------------

_loaded_from: Path | None = None


def instances() -> list[dict]:
    """The instance list, from registry.yml where that is possible.

    The YAML wins wherever it can be read. registry.json is the copy Jenkins
    ships to a host without PyYAML: a transport artifact, gitignored, and
    allowed to be older than the registry beside it — so reading it first let a
    stale copy shadow the real one silently.
    """
    global _loaded_from
    as_yaml, as_json = ROOT / "registry.yml", ROOT / "registry.json"

    if as_yaml.exists():
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is not None:
            _loaded_from = as_yaml
            return yaml.safe_load(as_yaml.read_text(encoding="utf-8"))["instances"]

    if as_json.exists():
        _loaded_from = as_json
        return json.loads(as_json.read_text(encoding="utf-8"))["instances"]

    raise SystemExit(
        "Neither registry.yml with PyYAML nor registry.json is available.\n"
        "Generate the JSON where PyYAML exists:\n"
        "  python3 scripts/validate-registry.py --emit-json"
    )


def find(instance: str) -> dict:
    """One instance by name, or exit naming the ones that exist.

    The error names the file the list actually came from: a stale registry.json
    shadowing the registry used to report an instance as absent that was
    plainly there.
    """
    known = instances()
    for inst in known:
        if inst["name"] == instance:
            return inst
    raise SystemExit(f"no instance named {instance} in "
                     f"{_loaded_from.name if _loaded_from else 'the registry'}.\n"
                     f"  Known: {', '.join(i['name'] for i in known)}")


# --------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------

def container_url(service: str, instance: str | None = None) -> str:
    """Where the instance answers, asked of Docker rather than assumed.

    This is the path on the target host, where the container is local and
    Docker knows its address. On a workstation it cannot work — the container
    is on another machine — and the raw Docker error says nothing about that,
    so both failures are answered with the command that does work there.
    """
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", service],
            capture_output=True, text=True,
        )
    except OSError as exc:   # no docker on PATH: a workstation, or a shell without it
        raise _no_local_container(service, instance, f"docker: {exc.strerror or exc}")
    if out.returncode != 0:
        raise _no_local_container(service, instance, out.stderr.strip())
    ip = out.stdout.split()
    if not ip:
        raise SystemExit(f"{service} has no container address — is it running?")
    return f"http://{ip[0]}:1880"


def _no_local_container(service: str, instance: str | None, detail: str) -> SystemExit:
    """Docker here does not have this container — which is normal off the host.

    Falling through to `docker inspect` means no base URL was supplied, and
    that is the thing to report. The Docker error underneath is a symptom of
    running the wrong command from the wrong machine, and reported alone it
    sends the reader to look for a stopped container that is in fact running.
    """
    # The first line has to stand alone: drift-check's table prints that line
    # and nothing else, and a reader who only sees "no such object" goes looking
    # for a stopped container that is in fact running on another machine.
    name = instance or service
    return SystemExit(
        f"no base URL — run it through nr.py (`nr.py check {name}`), "
        f"or set {base_url_env(name)}\n"
        f"  Docker here has no {service!r} container: {detail}\n"
        "  That resolution is the target host's, where the container is local.\n"
        "  From a workstation or a dev container nothing supplies the address,\n"
        "  so nr.py does it — it reads the URLs from nr.local.json:\n"
        f"    python3 scripts/nr.py check {name}\n"
        "    python3 scripts/nr.py status          # every instance\n"
        "  Calling drift-check.py or deploy.py directly needs that URL in the\n"
        f"  environment instead: {base_url_env(name)}=http://<host> — the host\n"
        "  only, registry.yml supplies admin_root. runbook.md, 'Reaching an\n"
        "  instance from a workstation'."
    )


def env_credentials(credential_id: str) -> tuple[str, str] | None:
    """The login for one instance, from the environment (decision 7)."""
    stem = credential_stem(credential_id)
    user, password = os.environ.get(f"{stem}_USR"), os.environ.get(f"{stem}_PSW")
    return (user, password) if user and password else None
def base_url(inst: dict) -> str:
    """Where to reach this instance, without the admin root.

    On a host Docker answers and every instance is at its container address on
    1880. From a workstation there is no single answer — some sit behind nginx,
    some publish a port, some neither — so an override comes first:

        NODE_RED_BASE_URL_GOR_PROD=http://gor-svr-lin01:1881
        NODE_RED_BASE_URL=http://one-host-for-everything   # fallback

    An override that already carries the admin root is the obvious mistake, and
    doubling the root gives a 404 that explains nothing. So it is stripped here
    rather than diagnosed later.
    """
    admin_root = inst.get("admin_root") or ""
    base = (os.environ.get(base_url_env(inst["name"]))
            or os.environ.get("NODE_RED_BASE_URL")
            or container_url(inst["compose_service"], inst["name"])).rstrip("/")
    if admin_root and base.endswith(admin_root):
        stripped = base[: -len(admin_root)]
        print(f"note: the base URL already ends in {admin_root!r}, which "
              f"registry.yml supplies — using {stripped}")
        return stripped
    return base


def _silent(method: str, url: str, exc: Exception, token: str | None) -> SystemExit:
    """Nothing came back. What that means depends on whether a token was in hand.

    Getting one narrows it, but not to one cause. The token exchange proves the
    route, the proxy and admin_root — and it proves them with a few hundred
    bytes. A flow is kilobytes, so a path that cannot carry a response past the
    first TCP segment passes the first call and stalls on the second, which
    looks exactly like a wedged runtime. What tells them apart is how many
    instances do it: one is a runtime, several on different hosts is the path.
    """
    if token:
        return SystemExit(
            f"{method} {url} -> {type(exc).__name__}: {exc}\n"
            f"  This instance authenticated moments ago, so the route, the proxy\n"
            f"  and admin_root are right — but that answer was a few hundred\n"
            f"  bytes and this one is kilobytes. Two causes fit, and the number\n"
            f"  of affected instances tells them apart:\n"
            f"  1. SEVERAL instances, on different hosts, timing out here while\n"
            f"     the small ones answer: the path cannot carry a response past\n"
            f"     one packet. Measured here: a dev container's bridge at MTU\n"
            f"     1500 over a smaller VPN, with the ICMP that would say so\n"
            f"     filtered. Measure it, do not guess — an unauthenticated\n"
            f"     request for something large on the same route:\n"
            f"       curl -o /dev/null -w '%{{size_download}}B %{{time_total}}s\\n' <base>/\n"
            f"     Stalls too, and no Node-RED is involved.\n"
            f"  2. ONE instance, while its siblings answer: that runtime is up\n"
            f"     and wedged, not down. It did not answer within {TIMEOUT}s:\n"
            f"       docker logs <service> --tail 100\n"
            f"       docker exec <service> ls -l /data/flows.json\n"
            f"     A blocked event loop or a storage module waiting on a mount.\n"
            f"  Either way the reading that counts is the one from the target\n"
            f"  host, where the pipeline runs (decision 10)."
        )
    return SystemExit(
        f"{method} {url} -> {type(exc).__name__}: {exc}\n"
        "  Something accepted the connection but sent no HTTP response.\n"
        "  A closed port would refuse, so check, in this order:\n"
        "    1. a proxy: urllib honours http_proxy/https_proxy. For an\n"
        "       internal host, add it to no_proxy.\n"
        "    2. what is actually on that port: curl -v <url>\n"
        "    3. whether the instance is running at all."
    )


def request(url: str, *, method="GET", body=None, token=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    # v1 returns a bare array with no rev, which would make the conflict check
    # impossible. v2 returns {rev, flows}.
    req.add_header("Node-RED-API-Version", "v2")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            payload = response.read()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        # 409 is a real answer the caller handles; everything else is reported
        # with the URL, because a bare status tells nobody what was called.
        if exc.code == 409:
            raise
        # A 404 on /auth/token says nothing about credentials, and the
        # generic advice below sends the reader to the wrong file. Both causes
        # are read off the instance, and neither is visible from a browser:
        # a reverse proxy in front of the host can add a prefix the runtime
        # does not have, or strip one it does.
        if exc.code == 404 and url.endswith("/auth/token"):
            root = url[:-len("/auth/token")]
            raise SystemExit(
                f"{method} {url} -> 404 Not Found\n"
                "  Nothing serves the Admin API there. Two causes:\n"
                "    1. admin_root does not match the instance. It is the runtime's\n"
                "       own httpAdminRoot — and this request goes straight to the\n"
                "       container, past any reverse proxy. A path that works in a\n"
                "       browser may be the proxy's, added or stripped in front of a\n"
                "       runtime that serves something else.\n"
                "    2. adminAuth is not configured. Node-RED registers /auth/token\n"
                "       only when it is, so a login attempt 404s instead of failing.\n"
                "  One probe tells them apart, and it needs no password:\n"
                f"    curl -s -o /dev/null -w '%{{http_code}}\\n' {root}/flows\n"
                "      401 -> the path is right and adminAuth is on: look elsewhere\n"
                "      404 -> admin_root is wrong for this instance\n"
                "      200 -> the path is right and adminAuth is off\n"
                "  Then ask the runtime itself:\n"
                "    docker exec <service> grep -nE 'httpAdminRoot|adminAuth' /data/settings.js\n"
                "  registry.md, 'admin_root'."
            ) from None
        raise SystemExit(
            f"{method} {url} -> {exc.code} {exc.reason}\n"
            + {401: "  The credentials were rejected, or adminAuth expects a different user.",
               404: "  Nothing answers on that path. Check admin_root in registry.yml against "
                    "the instance, and that NODE_RED_BASE_URL is the host only.",
               }.get(exc.code, f"  {exc.read()[:300].decode('utf-8', 'replace')}")
        ) from None
    except urllib.error.URLError as exc:
        # A timeout arrives here when it happens while connecting and as a bare
        # TimeoutError when it happens while reading. Same fault, so the same
        # diagnosis: which phase it struck in is not the reader's problem.
        if isinstance(exc.reason, TimeoutError):
            raise _silent(method, url, exc.reason, token) from None
        raise SystemExit(f"{method} {url} -> unreachable: {exc.reason}") from None
    except (http.client.HTTPException, ConnectionError, TimeoutError) as exc:
        # RemoteDisconnected and friends come through urlopen unwrapped, so
        # without this the caller gets a traceback instead of a diagnosis — and
        # drift-check's sweep stops at the first host that does this.
        #
        # A token in hand changes the diagnosis completely. Getting one means
        # this runtime answered a POST moments ago, so the network, the proxy
        # and the path are all proven and only the runtime is left. Without one,
        # nothing about the far end is established yet.
        raise _silent(method, url, exc, token) from None


def get_token(base: str, admin_root: str, user: str, password: str) -> str:
    _, data = request(f"{base}{admin_root}/auth/token", method="POST", body={
        "client_id": "node-red-admin",
        "grant_type": "password",
        "scope": "*",
        "username": user,
        "password": password,
    })
    token = (data or {}).get("access_token")
    if not token:
        raise SystemExit("auth/token returned no access_token")
    return token


# --------------------------------------------------------------------------
# Image tags
#
# `harbor.example/dap-node-red/wag-prod:5.0.1-1` is a registry host, a repo
# path, the Node-RED version, and the palette build. Four callers used to take
# that string apart with four different expressions.
# --------------------------------------------------------------------------

def tag_registry(tag: str) -> str:
    """The registry host, for a login hint."""
    return tag.split("/", 1)[0]


def tag_short(tag: str) -> str:
    """`wag-prod:5.0.1-1`, for a message that does not need the registry."""
    return tag.rsplit("/", 1)[-1]


def tag_version(tag: str) -> str:
    """The Node-RED version, which is what an editor has to match."""
    return tag.rsplit(":", 1)[1].rsplit("-", 1)[0]


def set_image_tag(instance: str, tag: str) -> None:
    """Point one instance's image_tag at `tag`, in place.

    Edited as text, not through PyYAML, which would drop every comment in the
    registry — including the ones recording which version each instance runs.
    """
    registry = ROOT / "registry.yml"
    text = registry.read_text(encoding="utf-8")
    block = re.search(rf"^  - name: {re.escape(instance)}$.*?(?=^  - name: |\Z)",
                      text, re.S | re.M)
    if not block:
        raise SystemExit(f"no instance named {instance} in registry.yml")
    line = re.search(r"^(\s+image_tag:\s*)(\S+)(.*)$", block.group(0), re.M)
    if not line:
        raise SystemExit(f"{instance} has no image_tag line to change")
    edited = block.group(0).replace(line.group(0),
                                    f"{line.group(1)}{tag}{line.group(3)}", 1)
    registry.write_text(text.replace(block.group(0), edited, 1), encoding="utf-8")
