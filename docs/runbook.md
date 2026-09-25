# Runbook

Operational procedures. Constraints and reasoning: [`decisions.md`](decisions.md).

## Backup gate — before any automation touches a container

`credentialSecret` is unset on every inventoried instance, so Node-RED generated a random key and stored the only copy in `/data/.config.runtime.json`. `flows_cred.json` is worthless without it. Losing that file loses every stored credential, irreversibly.

Run from the **host**, against the bind mount, with plain file copies. Not `docker exec` — the point is to capture the files independently of a running container.

Per instance, tar together:

- `flows.json`
- `flows_cred.json`
- `.config.runtime.json`
- `settings.js`
- `package.json`

Then pull the tarballs off the box before anything else happens.

## Moving an instance to a new container

`wfm-prod` was done this way on 2026-09-10: the flow moved from the old
`node-red` container to a `node-red-prod` named like the other hosts. Five
things decide whether it works, and four of them are invisible if you skip
them.

**Git holds no credentials, and must not.** `GET /flows` never returns them, so
a deploy into a fresh container carries the logic and neither the stored logins
nor any uploaded certificate. Those live in the old container's
`flows_cred.json`, encrypted with its key.

**The key lives in two files, and both have to name the old one.**

- `settings.js` → `credentialSecret`: the key to use from now on.
- `/data/.config.runtime.json` → `_credentialSecret`: the key the file on disk
  is actually encrypted with.

When they differ, Node-RED reads with the second and re-encrypts with the
first, which is how a rotation is meant to work — and why setting only
`settings.js` decrypts with a key that never encrypted anything. The symptom is
`Error loading credentials: ... is not valid JSON` with binary in the message,
and the instance keeps running with no credentials at all. Set both, with the
container stopped, and check with a fingerprint rather than by eye:

```bash
sudo python3 - <<'PY'
import hashlib, json, pathlib, re
OLD = pathlib.Path('<old>/data'); NEW = pathlib.Path('<new>/data')
key = json.loads((OLD / '.config.runtime.json').read_text())['_credentialSecret']
s, n = re.subn(r'(credentialSecret:\s*")[^"]*(")', lambda m: m.group(1) + key + m.group(2),
               (NEW / 'settings.js').read_text(), count=1)
assert n == 1, 'no active credentialSecret line'
(NEW / 'settings.js').write_text(s)
rt = NEW / '.config.runtime.json'
data = json.loads(rt.read_text()) if rt.exists() else {}
data['_credentialSecret'] = key
rt.write_text(json.dumps(data))
print('fingerprint:', hashlib.sha256(key.encode()).hexdigest()[:8])
PY
```

**Do not press Deploy in the empty editor** between copying `flows_cred.json`
and deploying the flow. Node-RED drops credentials belonging to no node when it
saves, and until the flow lands there are no nodes.

**Stop the old container before deploying the new one**, not after. Both hold
the same flow the moment the deploy lands, and a flow that publishes would
publish twice.

**`restart: always` outlives a stop.** An explicit `docker compose stop` is
remembered across a daemon restart, but any `docker compose up -d` on that file
starts the service again, and one file usually holds every service on the host.
Remove the service, do not just stop it.

## The one settings.js edit

Every change to `settings.js` restarts the container. Three changes are pending — the `credentialSecret` pin, and on two instances a deviation to normalize — so they are made in one edit and one restart per instance, after the backup gate.

**1. Read the generated key.** It is the only copy.

```bash
ssh <host> "sudo cat /path/to/<instance>/data/.config.runtime.json"
```

Store the `_credentialSecret` value in a Jenkins credential and record the id as `credential_secret_id` in `registry.yml`. Never echo it into a log, a pipeline output, or a commit.

**2. Edit `settings.js`.** For every instance:

```js
credentialSecret: "<the value from step 1>",
```

Pinning to the **existing** value means no re-encryption. A new value makes every stored credential unreadable.

On `cho-prod` additionally, bringing it back to what the other twelve do (decision 13):

```js
level: "info",     // was "trace"
```

On `wfm-prod` additionally, uncomment the `adminAuth` block (and on the new `wfm-test`, set it up from the start). Generate the hash inside the container so the password never reaches the shell history — type it, then Ctrl-D:

```bash
ssh wfm-svr-lin01
docker exec -i node-red node -e 'const b=require("bcryptjs");let d="";process.stdin.on("data",c=>d+=c).on("end",()=>console.log(b.hashSync(d.trim(),8)))'
```

If `bcryptjs` does not resolve in that image, `docker exec -it node-red npx node-red-admin hash-pw` does the same and prompts for the password. Put the username and password into a Jenkins credential and record the id as `auth_credential_id`.

**For an instance that does not exist yet** there is no container to exec into, so use a throwaway one — the version does not have to match, the hash is the same either way:

```bash
docker run --rm -i -w /usr/src/node-red nodered/node-red:4.0.8 \
  node -e 'const b=require("bcryptjs");let d="";process.stdin.on("data",c=>d+=c).on("end",()=>console.log(b.hashSync(d.trim(),8)))'
```

Which of the two credentials is chosen and which is given is worth keeping straight, because getting it backwards is silent: `auth_credential_id` holds a login **you invent** for the new instance, and `credential_secret_id` holds the key that instance's `flows_cred.json` is **already** encrypted with. Inventing that one makes every stored credential unreadable.

**3. Restart, service-scoped.**

```bash
docker compose -f <compose_file> up -d <compose_service>
```

The compose file and service name differ per host; both are in `registry.yml`.

**4. Verify.** Open the editor and confirm a stored credential still decrypts. On `wfm-prod`, confirm the login prompt appears and that `curl -s -o /dev/null -w '%{http_code}' http://<ip>:1880/flows` now returns `401` rather than `200`.

## Standing up a new service

Three things decide whether `deploy.py` can reach it at all, and all three are
in the host's compose file:

- **`container_name` must be the `compose_service` from `registry.yml`.** The
  deploy resolves the instance with `docker inspect <compose_service>`, and
  compose's generated name (`base_container-node-red-prod-1`) is not that.
- **The network is declared external.** These hosts already have the network
  their nginx sits on, so the file joins it rather than creating a second one:

  ```yaml
  networks:
    app_network:
      external: true
  ```

- **`dns` and `dns_search` come from whatever ran there before.** A flow that
  addresses a machine by name resolves only with the search domains that
  runtime had; `registry.yml` carries them per instance so they are not
  guessed.

And one that decides whether it keeps its credentials: **`flows_cred.json`
belongs in `/data` before the first flow lands**, with the key in both files
("Moving an instance to a new container"). Node-RED drops credentials belonging
to no node the first time it saves, and until the flow is deployed there are no
nodes.

## A new instance's /data must belong to the container user

The bind-mounted directory has to be owned by the uid the container runs as,
before it starts — and setting a foreign owner needs root. **The uid is not the
same everywhere**: `wfm` runs `1004:1004`, `wag-prod` `1001:1001`, and the image's
own `node-red` user is `1000`. What matters is that the service's `user:` and the
directory's owner agree, so read the uid off the host rather than assuming one:

```bash
docker inspect <an existing node-red on that host> --format '{{.Config.User}}'
```

For a new instance on a host that has none, leaving `user:` out and chowning to
the image's own user is the version with the fewest moving parts:

```bash
sudo mkdir -p <compose dir>/node-red/prod/data
sudo chown -R 1000:1000 <compose dir>/node-red/prod/data
ls -ldn <compose dir>/node-red/prod/data    # must match the uid the service runs as
```

Without it the runtime starts and serves reads, so it looks healthy, and then
dies the first time it writes. On `wfm-test` that was the token endpoint
persisting a session:

```
[warn] Flushing file /data/.sessions.json.$$$ to disk failed : EACCES
[red] Uncaught Exception:
```

`restart: always` brings it straight back, so the symptom at the other end is a
connection accepted and dropped without an HTTP response, not a permission
error. `Creating new flow file` on every start is the other tell: the flow file
cannot be written either, so no deploy would ever have persisted.

## Compose split

The compose file differs per host — `code/node-red/`, `energy/`, `Base_Container/`, `base_container/` — and the inventory's neighbour probe found no non-Node-RED service in any of those projects. If that holds, the split is already done and there is no work here.

It contradicts the earlier report that NATS shares `wag`'s file, so confirm before believing it:

```bash
ssh <host> "docker compose -f <compose_file> config --services"
```

Either way, every compose call names its service — `docker compose up -d <compose_service>`. That costs nothing and holds whichever answer comes back.

## Jenkins credentials

Three sets, all **Global** scope. The id must match `registry.yml` exactly; it is case-sensitive.

| Id | Kind | Holds |
|---|---|---|
| `<host>_pw` | Username with password | the SSH login for that server |
| `nodered-<instance>-auth` | Username with password | that instance's `adminAuth` user and password |
| `nodered-<instance>-credsecret` | **Secret text** | that instance's `credentialSecret` — one value, no username |

**Host logins.** Ten, one per server. Eight already exist from the inherited pipeline; `wfm-svr-lin01_pw` and `dpn-svr-iot_pw` are new, because those two hosts were missing from the old map.

**Admin API logins.** Twelve, not fourteen: `slu-prod` and `slu-test` carry `app: null`, so the pipeline never deploys to them and never asks for their credential.

```
nodered-cho-prod-auth    nodered-jan-prod-auth    nodered-wag-prod-auth
nodered-cho-test-auth    nodered-jan-test-auth    nodered-wag-test-auth
nodered-gor-prod-auth    nodered-srem-prod-auth   nodered-wfm-prod-auth
nodered-gor-test-auth    nodered-srem-test-auth   nodered-wfm-test-auth
```

`nodered-wfm-prod-auth` cannot be created usefully yet: `wfm-prod` has `adminAuth` switched off. Jenkins fails on a credential id that does not exist — that is how the first fleet-wide run died — so switch `adminAuth` on first (decision 13), then create the credential with the same user and password. `wfm-test` is new, so its `adminAuth` is set up from the start and its credential can be created straight away.

The two FlowFuse servers need no credential at all yet, and neither does `pod-svr-lin01_pw` or `dpn-svr-iot_pw`. Those instances are not in `registry.yml` (decision 12), so the pipeline never resolves a credential for them. They enter after the migration, in this order: copy the flow, start the plain container, **unenroll the device**, retire the agent. Unenrolling last would let the agent overwrite the flow from the platform.

**Credential secrets.** Fourteen, created during the backup gate from the value each instance **already** has — except `wfm-test`, which is new, so its value is generated once rather than pinned. No script reads them today — they are the copy of the key that lives off the server, and the key itself stays in `settings.js` on the host. A freshly invented value re-encrypts every stored credential into garbage.

## Flow deploy

```
python3 scripts/deploy.py --instance <name> --dry-run              # prints the diff and the rev
python3 scripts/deploy.py --instance <name> --expect-rev <rev>     # writes only if it still holds
```

That is the on-host form, which is what Jenkins runs. From a workstation the address and login come from `nr.local.json`, so it is `python3 scripts/nr.py deploy <name>` — dry-run only, deliberately (below).

Sequence and the `rev` handshake: [`architecture.md`](architecture.md).

**A `404` from `POST <admin_root>/auth/token` is one of two things**, and both are in the instance's own `settings.js`:

- `admin_root` does not match the runtime. The deploy runs on the host and calls the container directly, so `admin_root` has to be the value of `httpAdminRoot` **in that runtime** — not the path the instance answers on in a browser. A reverse proxy in front of the host can add a prefix the runtime does not have, or strip one it does, and both were found here: `wag-prod` serves `/node-red-prod` itself and the proxy passes it through, while `wfm-prod` is reached in a browser as `http://wfm-svr-lin01/node-red-prod` but serves the API at `/` on the container, because nginx strips the prefix. So a `curl` that works from a workstation is not evidence about `admin_root`.
- `adminAuth` is not configured. Node-RED registers `/auth/token` only when it is, so every login attempt answers `404` rather than `401`.

One probe separates them, and it needs no password:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://<container-ip>:1880<admin_root>/flows
#   401 → the path is right and adminAuth is on
#   404 → admin_root is wrong for this instance
#   200 → the path is right and adminAuth is off
```

Then read it off the runtime and make `registry.yml` match:

```bash
docker exec <compose_service> grep -nE 'httpAdminRoot|adminAuth' /data/settings.js
```

`admin_root` is a per-instance fact — `collect-inventory.py` probes it against the container for exactly this reason — and never a value to copy from a sibling. Two instances on one host can differ, and the browser URL can differ from both.

**Take the rev from the dry run into the deploy.** It is what makes the conflict abort reachable: a deploy without it reads the current rev and posts against it moments later, so a browser edit made before the run is inside that rev and gets flattened. With it, anything that changed the instance between the review and the write stops the write. In Jenkins the parameter is `EXPECT_REV`, and it belongs to one instance — a fleet run cannot pin it.

### Reaching an instance from a workstation

The pipeline runs `deploy.py` on the target host, where every instance is at `http://<container-ip>:1880` and the script finds it through Docker. From a workstation there is no single answer, which is why decision 10 exists — but during bring-up it is useful, so set `NODE_RED_BASE_URL` to the **host only**. The script appends `admin_root` from `registry.yml`; passing the URL you have open in the browser doubles it.

**From a workstation, go through `nr.py`.** It reads `nr.local.json` and sets those variables per instance before calling the same scripts — `nr.py status` for the fleet, `nr.py check <inst>` for one. Calling `drift-check.py` or `capture.py` directly there falls through to `docker inspect` on the local engine, which has none of these containers: the run reports `no such object` (or, on Windows, `FileNotFoundError` because `docker` is not on the PATH of that process) for every instance, and both mean "no base URL was supplied", not "the instance is down". `nr.py status` does not prompt for passwords — it cannot ask sixteen times — so a full sweep needs them stored in `nr.local.json`; without one, `adminAuth` answers `401` and that instance reads as unreachable.

| Instance | From a workstation |
|---|---|
| all 14 | `http://<host>/node-red-prod` and `http://<host>/node-red-test` |

That is where they answer, not a promise that the answer arrives. Measured
2026-09-21 from a dev container: the token call succeeded on every instance and
`GET /flows` timed out on every one whose flow exceeds a single TCP segment —
291 bytes through, 2.4 KB not, across six hosts, while the same request on the
host itself returned immediately. The container bridge sits at MTU 1500 over a
smaller tunnel, so anything past one segment is dropped and nothing says so.
It reads as a fleet of wedged runtimes and is not one: the VPN adapter is 1350
and the container 1500 — "Reaching an instance from a workstation" below. `.devcontainer/` now
lowers the interface at start, so a rebuilt dev container sweeps normally;
`check`, `capture` and `status` need no engine at all, so they also just run
outside it.
The reading that counts is still the one from the target host, which is what the
pipeline takes (decision 10).

The proxy routes by path on every host, which is why `srem-prod`, `srem-test` and `slu-prod` — none of which publish a port — are reachable from a workstation at all now. `nr.local.example.json` carries the full list in that form.

Where a port is published it still answers directly, and that is the shorter path when the proxy is what you are debugging: `cho` on 1880/1881, `jan` on 1880/1881, `gor` on **1881/1880** — prod and test reversed against what the names suggest — `slu-test` on 1882, `wfm-prod` on 1880 and `wfm-test` on 1881. `wag`, `srem` and `slu-prod` publish nothing.

**The proxy path is not `admin_root`.** Both end up in the same request from a workstation, so it is easy to conclude they are the same value, and they are not: the proxy path is what the browser uses, `admin_root` is what the runtime serves on the container, and the deploy uses only the second. Where the two overlap the tools strip the duplicate and print a note; where they differ — `wfm-prod` is reached as `/node-red-prod` and serves at `/` — copying one into the other makes every Jenkins call `404`.

Both lists are snapshots; `collect-inventory.py` re-derives them, and only the host-side path is what the pipeline depends on.

To sweep several instances in one run, set the base per instance — `NODE_RED_BASE_URL_<INSTANCE>`, with `-` as `_` and upper-cased. A plain `NODE_RED_BASE_URL` still applies to anything without its own override:

```powershell
$env:NODE_RED_BASE_URL_WAG_PROD  = "http://wag-svr-lin01"
$env:NODE_RED_BASE_URL_WAG_TEST  = "http://wag-svr-lin01"
$env:NODE_RED_BASE_URL_CHO_PROD  = "http://cho-svr-lin01:1880"
$env:NODE_RED_BASE_URL_CHO_TEST  = "http://cho-svr-lin01:1881"
$env:NODE_RED_BASE_URL_GOR_PROD  = "http://gor-svr-lin01:1881"
$env:NODE_RED_BASE_URL_GOR_TEST  = "http://gor-svr-lin01:1880"
$env:NODE_RED_BASE_URL_JAN_PROD  = "http://jan-svr-lin01:1880"
$env:NODE_RED_BASE_URL_JAN_TEST  = "http://jan-svr-lin01:1881"
$env:NODE_RED_BASE_URL_WFM_PROD  = "http://wfm-svr-lin01:1880"
$env:NODE_RED_BASE_URL_WFM_TEST  = "http://wfm-svr-lin01:1881"

python3 scripts/drift-check.py --all --json inventory/drift.json
```

`srem-prod`, `srem-test` and `slu-prod` publish no port and will report as unreachable from a workstation. That is accurate, not a fault: reaching them means running on the host, which is what the pipeline does.

**If every instance reports unreachable from a workstation, suspect a proxy first.** `urllib` honours `http_proxy` and `https_proxy`, so a corporate proxy takes the request for an internal host and usually closes it without an HTTP response — which reads as a connection reset rather than as a refusal. Put the site domain in `no_proxy`:

```powershell
$env:NO_PROXY = "polipol.intra,polipol-service.de,10.0.0.0/8,192.168.0.0/16"
```

A refused connection means the opposite: nothing is listening, so the instance or its port is the thing to check.

**On `409`:** the running flow diverged from Git. Someone edited in the browser. Recover the edit rather than discarding it:

1. `GET <admin_root>/flows` and save the running flow.
2. Run it through `scripts/normalize.py`.
3. Diff against the committed `flows.json`.
4. Commit it, or discard it deliberately.
5. Deploy again.

## Palette change

A new palette module does **not** travel with a flow deploy. `POST /flows`
carries flow logic and installs nothing, so a flow whose nodes the target image
does not have deploys "successfully" and then logs `Unrecognised node type` and
does not run. The palette is the second transport, and it restarts the
container.

**Installing a module through "Manage palette" in the local editor is carried
into the app for you.** That install runs npm in the session's `/data`, which is
gitignored — so on exit `nr.py` writes any module the app does not already pin
into `apps/<app>/package.json`, at the version npm actually resolved, and says
so. Review it with `git diff`; the rest of the sequence is still yours.

The merge is **additive**. The baked palette lives in the image, not under
`/data`, so a module missing from the session means "already in the image",
never "removed" — a two-way sync would empty the manifest on the first session.
Removing a module is therefore a manual edit of `apps/<app>/package.json`.

1. Have the dependency in `apps/<app>/package.json` with an exact version —
   from the editor session, or written by hand. The editor session also raises
   the palette-build suffix of that instance's `image_tag` in `registry.yml`,
   because the two belong in one commit: CI pushes the tag it finds there, so
   a palette change with an unchanged tag replaces the image the instance runs
   instead of building a new one. `validate-registry.py --changed-since <ref>`
   fails on exactly that, and CI runs it against the previous commit. The check
   needs git and the base commit in the clone, so `validate:registry` runs on
   `python:3.13` rather than `-slim` and with `GIT_DEPTH: 0`. When it cannot
   run it says so on stderr and passes — read a "palette-tag check skipped" in
   a CI log as the guard missing, not as a clean bill.
2. Commit to the default branch — GitLab CI builds and signs a new image. It
   builds **only** when that app's `package.json` or `Dockerfile` changed, and
   only on the default branch: a flow commit must not rebuild, because it would
   push the same pinned tag with different content. The tag is
   `<node-red-version>-<palette build>`, so raise the suffix in `registry.yml`
   in the same commit: `wfm-test:4.0.9-1` becomes `wfm-test:4.0.9-2`.
3. Update `image_tag` in `registry.yml` to that exact tag. `latest` fails
   validation (decision 5).
4. Jenkins with `DEPLOY_PALETTE=true`, `DRY_RUN=false`. It deploys the flow
   first and recreates the service after, which is the order that works: the
   new container starts on the flow that was just written, with the palette it
   needs. This restarts the container; the ingest gap is expected here.

**Nothing is edited on the host.** The compose services take their image from an
environment variable whose fallback is the tag pinned at the time:

```yaml
node-red-prod:
  image: ${IMAGE_NODE_RED_PROD:-harbor.aks-infra.polipol-service.de/dap-node-red/wag-prod:5.0.1-1}
```

so the pipeline passes the tag from `registry.yml` into the `up -d` call, and
`registry.yml` stays the only place a version is written. The variable name comes
from the service: `node-red-prod` → `IMAGE_NODE_RED_PROD`.

The fallback is why the pipeline checks afterwards what the container actually
runs. If a host spells the variable differently, nothing fails — compose uses the
baked-in tag, `up -d` reports success, and the instance comes back on the old
palette, which surfaces much later as `Unrecognised node type` in a flow that
deployed cleanly. The check turns that into a failed build naming both tags. To
see what a host expects:

```bash
ssh <host> "grep -n 'image:' <compose_file>"
```

To see the new nodes in the local editor, `nr.py edit <inst> --baked` — but only
after step 3, because `--baked` runs whatever `image_tag` names.

## Node-RED version upgrade

The estate runs three versions, because every instance was first started on a
different date against `latest` (`architecture.md`, "Version spread").
Converging them is an upgrade, and it reaches an instance the same way a new
palette does: a rebuilt image and a container recreate.

The version lives in **two** places per instance, and they have to agree:

- `apps/<app>/Dockerfile` — the `FROM docker.io/nodered/node-red:<version>`, which is what CI builds
- `registry.yml` — the `image_tag`, `<app>:<version>-<palette build>`, which is what the deploy pins, what triggers the rebuild, and what `nr.py edit` runs locally

```bash
python3 scripts/bump-node-red.py --to 5.0.1 --all --dry-run     # what it would do
python3 scripts/bump-node-red.py --to 5.0.1 --instance wfm-test  # one instance
```

It rewrites both, resets the palette build to 1, refuses when the two
disagree already, and regenerates `apps/build-image-pipeline.yml`. Then
`validate-registry.py`, `git diff`, and a commit to the default branch — CI
builds each changed app.

**Roll the deploys one instance at a time**, each one `DEPLOY_PALETTE=true`,
`DRY_RUN=false`. `--all` in the bump is fine, because that is a commit; `--all`
in the deploy is not, because each one is a container recreate. Order:
a workbench, then that site's prod, then the next site.

What to watch on the first one:

- **The build is the cheap test.** A palette module that does not support the
  new Node-RED or its Node.js fails `npm install` in CI, before anything is
  deployed. That is the signal you want, and it costs nothing.
- **Node count unchanged** after the recreate, and the palette nodes load
  rather than showing as unknown.
- **`Error loading credentials` in the log** would mean the `credentialSecret`
  did not survive — it is in `settings.js`, which the upgrade does not touch,
  so this should not happen. Check anyway; it is one line.
- **Drift afterwards.** A newer runtime can write fields an older one did not
  the first time someone deploys from the browser. If `nr.py check` reports
  drift with no edit behind it, capture it once and commit that normalization
  deliberately, rather than treating it as an unexplained diff.
- **Read the release notes between the two versions first.** 4.x to 5.x is a
  major boundary; this repository carries no opinion about what changed there,
  and the pipeline cannot tell you.

One thing gets *better* immediately: `nr.py edit` derives the editor version
from `image_tag`, so once an instance is on 5.0.1 its local editor is too. The
mismatch that pinning exists to prevent — a 5.x editor writing fields into a
flow a 4.0.x runtime cannot read — stops being possible for that instance.

## Changing a flow

`scripts/nr.py` wraps everything below. It reads the instance list from `registry.yml`, so it cannot list an instance that does not exist or miss one that does.

```bash
python3 scripts/nr.py            # pick an instance, pick an action
python3 scripts/nr.py status     # every instance: does it still match Git?
python3 scripts/nr.py edit wag-prod
```

The dev container in `.devcontainer/` brings Python, the dependencies and access to Docker for the editor container, so these commands run unchanged inside VS Code.

Where each instance answers and the login for it go in a gitignored `nr.local.json`; copy `nr.local.example.json`. A password left out is asked for at the prompt and is not stored.

`nr.py deploy` is dry-run only, on purpose. A real deploy is a reviewed commit that Jenkins carries out; a local script that could write to production would make that path optional.

`nr.py` is not a convenience wrapper around those scripts, it is what makes them
work off the host. `drift-check.py`, `capture.py` and `deploy.py` take the
instance's address and login from the environment; on the target host the
address comes from Docker and Jenkins supplies the login, and from a workstation
nothing does — so `nr.py` reads `nr.local.json` and sets both before calling the
same script. Called directly from a workstation they resolve nothing and report
every instance as unreachable. Both forms appear below: `nr.py` in the loops,
the raw command where it says what runs on the host.

Two routes. Which one is right depends on whether the instance may run the change while you make it.

### Route A — edit locally, then deploy

For a production flow, or a new flow. Nothing runs while you work.

```bash
python3 scripts/nr.py check wag-prod                    # 1. confirm Git matches the instance
python3 scripts/nr.py edit wag-prod                     # 2. editor on http://localhost:1880
                                                        # 3. edit, press Deploy
python3 scripts/normalize.py --write apps/wag-prod/flows.json
git diff apps/wag-prod/flows.json                       # 4. review — it should be small
git commit -am "flows(wag-prod): ..." && git push        # 5.
```

Then in Jenkins: `INSTANCE=wag-prod`, `DRY_RUN=true` to see the diff the pipeline sees, then `DRY_RUN=false`.

Step 1 is not optional. If the instance has drifted, your local edit is against a stale base and the deploy will hit a `409`.

**`nr.py edit` probes for the address and prints what answered.** It starts the
editor detached, tries every address it could be on, waits until one serves
HTTP, and lists only those. Then it follows the log until Ctrl-C and stops the
container.

Which address works is not something to deduce, and four attempts at deducing
it were all wrong. A published port only reaches you if the machine publishing
it runs your browser, and `podman machine` does not forward it out. A container
name only resolves if the network has DNS, and podman's default network does
not. From a dev container the editor is a sibling container, so nothing listens
on 1880 inside the dev container and VS Code must not forward that port: the
forward would claim `localhost:1880` in the browser and tunnel it to nothing,
which renders as a grey page rather than a refused connection.

**In a dev container it puts a hop on localhost.** The Ports panel takes a port
number and resolves it against `localhost` inside the dev container, and the
editor is a sibling container, so nothing is there to forward. No network or
publish setting changes that: the port is published on the engine's machine,
which is neither this container nor the browser's host. So `nr.py edit` listens
on `127.0.0.1:1880` in the dev container and forwards to the editor's address.
VS Code then finds the port on its own, and the editor keeps its own network,
DNS and isolation.

The hop only exists while the editor runs, and only in a dev container. On a
workstation with a local daemon the published port is already right.

Prefer `nr.py edit` over the bare compose call: it pins the editor to the Node-RED version that instance runs, which a plain `APP=... docker compose up` does not, and it finds the container engine — podman on a workstation, Docker on a server, or whatever `CONTAINER_ENGINE` names. A 5.x editor writes fields into the flow that a 4.0.x runtime does not know, in a file whose whole purpose is to deploy unchanged. For a flow that uses palette nodes, add `--baked` so the editor runs that app's own image and those nodes open as themselves instead of as "unknown".

**`--baked` needs the image on the engine, not a login in this shell.** A
registry login belongs to the client that ran it — `podman login` on Windows
writes that Windows user's auth file, which a dev container cannot read — but
the pulled image belongs to the *engine*, and both clients talk to the same
one. Measured: an image pulled on the workstation shows up in `docker images`
inside the dev container. So the login is not per session. Pull the image once
on the workstation,

```
podman pull harbor.aks-infra.polipol-service.de/dap-node-red/<app>:<tag>
```

and every later `--baked` run finds it and asks for nothing. Note the pull is
per **app**: `wfm-test` and `wfm-prod` are separate images.

`nr.py edit --baked` checks both halves before it starts anything. If the
engine lacks the image and this client has no credential for the registry, it
stops with that `podman pull` command rather than handing compose a pull that
cannot succeed — no editor starts and no app file is touched. `pull_policy:
missing` in `compose/editor.yml` keeps it from pulling again once the image is
there. Logging in inside the dev container works too, and `nr.py` then just
pulls, but that credential goes away with the container. A tag the engine has
never seen — after a palette rebuild — has to be pulled once more, by whichever
client is logged in.

If you do log in here, use the engine `nr.py` actually used, which it prints: on Windows `podman compose` hands the work to `docker-compose.exe` and points it at podman's own socket, so `Error response from daemon: unauthorized ... action: pull` is podman answering through its Docker-compatible API and `podman login` is what fixes it — the name of the compose binary in the message says nothing about which engine pulls. When the pull fails the editor never starts, and `nr.py` says so rather than reporting a copy-back; the app file is untouched.

**The editor arrives with every tab disabled.** That is the protection, and it
replaces one that did not hold: most nodes here carry no credentials and several
address their target by literal IP, so "it cannot authenticate" and "it cannot
resolve" protect nothing, and safe mode ends at the first Deploy — which is how
the editor saves your work. So `nr.py edit` stages the flow into
`.editor-session/<app>/` with the tabs switched off and mounts that, never
`apps/<app>/`. Enable the tab you are working on, or add one; only that runs,
and it runs for real against real systems, which is the deliberate act rather
than the accident. On Ctrl-C the flow is copied back with each existing tab's
disabled state restored from Git, so local switching never reaches a commit.

For a flow you do not know, `--isolated` puts the editor on a network with no
gateway: nothing outside the container is reachable, Deploy or no Deploy.

**The editor container cannot double your data.** That is the obvious hazard — a production flow with MQTT and OPC UA nodes, opened in a second runtime that reaches the same broker, acts twice. The disabled tabs are what stop it, with safe mode covering the window before the first Deploy and `--isolated` available when even that is too much. What remains: a config node may open a connection while its own nodes are disabled — a connect without traffic — and an exec node runs inside the container. `compose/editor.yml` spells this out, including how to verify the isolated mode in your own engine, because some compose providers ignore the flag.

### Route B — edit in the browser, then capture

For a test instance, or when the change has to run to be judged. The edit is live immediately, which is the point.

```bash
python3 scripts/nr.py capture wag-test     # shows the diff, then asks before writing
git diff && git commit -am "flows(wag-test): ..." && git push
```

That one command is both steps: it runs the capture as a dry run, prints what would come back, and writes `apps/wag-test/` only after a `y`. On the host, or with `NODE_RED_BASE_URL_WAG_TEST` set, the script underneath is `python3 scripts/capture.py --instance wag-test [--dry-run]`.

`capture.py` writes to the repository and never to an instance. It is also the recovery from a `409`.

### Adding a new flow to an instance that has one

There is no separate procedure. A flow file holds every tab of that instance, so a new flow is a new tab inside `apps/<app>/flows.json`. Use route A: add the tab in the local editor, deploy the whole file.

### Promoting a change between a workbench and prod

A `*-test` instance is a workbench, empty by default (decision 15). These are
the two loops, and both end with a deploy of **both** instances.

**Changing a tab that prod already runs**

```bash
python3 scripts/nr.py check wfm-prod                                  # 1. is prod still Git's?
python3 scripts/nr.py promote wfm-prod wfm-test "Extruder abfrage" --copy
git commit -am "promote(wfm-test): bring Extruder abfrage onto the workbench"
python3 scripts/nr.py edit wfm-test                                   # 2. build it
git commit -am "flows(wfm-test): ..."                                 #    then deploy wfm-test
                                                                      # 3. try it on the instance
python3 scripts/nr.py promote wfm-test wfm-prod "Extruder abfrage" --move
git commit -am "promote(wfm-prod): ship Extruder abfrage"
                                                                      # 4. deploy wfm-test, then wfm-prod
```

Step 1 is not decoration: promoting from a prod that has drifted puts a stale
tab on the workbench, and the browser edit it hides surfaces as a `409` at the
end instead of as a `capture` at the start.

**A tab that does not exist yet** skips the first promotion — build it on the
workbench, deploy there, then `--move` it to prod.

**`--copy` and `--move` are not interchangeable, and there is no default.**
`--copy` for prod → workbench, because prod has to keep running the tab while
you change it. `--move` for workbench → prod, because a tab left enabled on the
workbench runs alongside prod: two runtimes on the same PLC and the same topic,
and the only symptom is data arriving twice.

**The arriving state follows the direction.** A `--copy` lands **disabled** on
the workbench, so nothing starts by itself and becomes a second publisher on a
live topic; enable it there when you want it to run. A `--move` lands
**enabled** in prod, because that is where it is meant to run — and if prod had
that tab disabled before, the report says so, since re-enabling it silently
would be a change nobody asked for.

Enabling on the workbench is a change to that instance, so it shows up as
drift until someone captures it. That is correct: the workbench's own state is
its own business, and the tab you ship is the one you validated.

**Deploy order for a `--move`: the source first.** The tab stops on the
workbench before it starts in prod, so the two never overlap. For a changed tab
prod serves the old version until the new one lands.

**Read the report.** Where the destination lacks a config node the tab needs, it
is created with the *source's* values and named in the output. That is the one
manual gate in the loop: set it for its own instance before deploying, or the
workbench publishes into prod's broker. An existing config node is never
overwritten, which is how each side keeps its own broker across promotions.

**The workbench must not write outward.** Reading an OPC UA server twice is
tolerable; publishing twice is not. Repoint the workbench's `mqtt out` target —
in `apps/<app>-test/flows.json`, where it stays, because promotion leaves
destination config nodes alone.

### Which route for which instance

| | Route |
|---|---|
| `*-prod` | A — the instance must not run a half-finished change |
| `*-test` | B is usually faster; A also works |
| a brand-new app | A — there is nothing running to conflict with |

Note that `*-prod` and `*-test` on one host are **different applications**, not two stages of one — separate flow files, separate config nodes, separate brokers. They are connected only where someone connects them deliberately, one tab at a time, through `promote` (above). A change does not flow from test to prod on its own.

## Passwords a flow reads from the environment

Some nodes — `node-red-contrib-postgresql` first among them — keep their
password in the flow rather than in the credential store. Those fields are set
to `env`: the flow carries only the **name** of the variable, and the value
comes from the container's environment.

Which means: **the variable has to be in the compose service before the flow is
deployed.** If it is missing, Node-RED connects with an empty password, the
deploy still reports success, and the database is simply unreachable. And
because the process environment is built at start, changing a variable is a
container restart, not a flow deploy.

Check without revealing a value:

```bash
docker exec <service> printenv | cut -d= -f1 | sort
```

`registry.yml: variables` describes what an instance's environment should hold.
No tool writes it into a container — the host's compose file does, by hand
(`registry.md`).

## Drift check

```
python3 scripts/drift-check.py --all
python3 scripts/drift-check.py --instance gor-prod --show-diff
python3 scripts/drift-check.py --all --json inventory/drift.json --fail-on-drift
```

On the host, or wherever `NODE_RED_BASE_URL_*` is set — the scheduled job in decision 11 is this command. From a workstation the same two readings are `nr.py status` and `nr.py check gor-prod`, which supply the addresses; only `--json` has no wrapper, so a report from there needs the variables.

Read-only: `GET /flows`, normalize, diff against Git, report. It never writes to an instance and offers no flag that would.

Exit 0 when clean, 1 when an instance is unreachable, and 3 only with `--fail-on-drift` — for a scheduled check that should go red. Without the flag drift is reported and the exit stays 0, because drift is information, not a failure.

An unreachable instance does not stop the sweep; it is one row in the report. `--json` writes the full report, diffs included, which is what the daily job collects and sends on (decision 11).

## The drift job

`Jenkinsfile.drift`, cron `H 6 * * *`. One `drift.json` over the whole estate,
kept in Jenkins, and one POST to a Node-RED endpoint where a flow decides what
happens next.

```
cron('H 6 * * *')
   └─ one SSH session per host: drift-check.py --host <host> --json
        └─ collect the fragments  →  drift.json
             ├─ Jenkins artifact: drift.json  (the history)
             └─ POST to http://<container>:1880/node-red-test/drift
                                              →  the flow on dpn-test
```

**Nothing is left on any host.** The sweep runs in a directory under `/tmp` that
the `post` block removes again; the result lives in Jenkins. An earlier version
put a rendered page in an nginx directory and the JSON into an instance's
`/data`. The first of those is ordinary — every pipeline publishes a static page
— the second is not: it couples CI to the data directory of a running
application, and it put the watchdog inside one of the things it watches.

**There is no page any more, and no `RENDER_HOST`.** Rendering needed `python3`,
which the Jenkins controller has not and every site host has, so the job named
one host and rendered there. That made a single named machine a dependency of
every sweep — for an artifact nobody could open without logging into Jenkins
anyway. `drift.json` is the record now, and the flow behind the webhook is the
presentation. Every host in the run has exactly one job: answer the sweep.
`scripts/render-drift.py` is still in the repository for anyone who wants a page
out of a downloaded `drift.json`; nothing calls it.

**Why per host and not centrally:** `drift-check` reaches a runtime through
Docker on the machine it runs on (decision 10). Called centrally, `--all`
reports every instance `unreachable`. That is what `--host` is for. The loop is
inside the job, not an operator step: ten SSH sessions, ten fragments, one file.

**Why drift does not turn the run red:** drift is somebody's browser edit that
is not in Git yet — information, not a failure (decision 9). The job goes
UNSTABLE when a **host was unreachable** or the **webhook was not accepted**;
the build description then names what is missing, and the unreachable host is a
row in `drift.json` rather than a gap in it.

### The webhook

Three parameters drive it:

| Parameter | Meaning |
|---|---|
| `WEBHOOK_INSTANCE` | target instance from `registry.yml`, default `dpn-test`. Empty switches the webhook off |
| `WEBHOOK_PATH` | the endpoint as the runtime serves it, default `/node-red-test/drift` |
| `WEBHOOK_TOKEN_CREDENTIAL` | optional Jenkins credential (secret text), sent as `X-Drift-Token` |

The POST goes **from the instance's own host**, the same way `deploy.py` reaches
it: the container address is resolved there, so no published port, no proxy
path and no network route Jenkins does not already have.

One instance receives the result for all 18 — the payload is the whole
`drift.json`, not one row.

`http in` sits under `httpNodeRoot`, **not** under `admin_root`. Measured on
`dpn-test`: an `http in` node with URL `/drift` answers on
**`/node-red-test/drift`** — which is why that, and not `/drift`, is the
default. `WEBHOOK_PATH` is sent exactly as given: it carries the full path the
runtime serves, and there is nothing in the registry to derive it from.

If the instance does not answer `2xx` the build goes UNSTABLE: the numbers are
archived, nobody heard them.

### The flow that reacts to it

**One flow for the whole estate**, not one per instance. It runs on `dpn-test`,
because that is what a workbench is for, and it is built like any other tab —
`nr.py edit dpn-test`, normalize, commit, deploy. Five nodes:

1. **`http in`**, method `POST`, URL `/drift`.
2. **`http response`**, status 204 — wired **straight off the `http in`**.
   Without a reply `curl` waits out its timeout and the job goes yellow for
   nothing.
3. **`function`**, which compares against the previous picture and only passes
   on a change. `node.status` is not decoration: on "nothing changed" the node
   deliberately emits nothing, and without a status line that is
   indistinguishable from broken.

   ```javascript
   const rows = Array.isArray(msg.payload) ? msg.payload : [];
   const now = {};
   let drifted = 0;
   for (const row of rows) {
       // The comparison key is state plus extent: "clean" becoming "drifted" is
       // a change, and so is 4 changed lines becoming 40. Unchanged drift is not.
       now[row.instance] = row.state === "drifted"
           ? `drifted:${row.changed_lines}`
           : row.state;
       if (row.state === "drifted") { drifted++; }
   }

   const before = flow.get("driftState") || {};
   flow.set("driftState", now);

   const stamp = new Date().toTimeString().slice(0, 5);
   const seen = `${stamp} · ${rows.length} instances, ${drifted} drifted`;

   // On the very first run everything is "new" — that is the start of the
   // measurement, not an event. Same after a restart: the context lives in
   // memory, so the measurement starts over there.
   if (!Object.keys(before).length) {
       node.status({ fill: "grey", shape: "ring", text: `${seen} · baseline` });
       return null;
   }

   const changed = Object.keys(now)
       .filter(k => now[k] !== before[k])
       .map(k => `${k}: ${before[k] || "unknown"} → ${now[k]}`);

   if (!changed.length) {
       node.status({ fill: "green", shape: "dot", text: `${seen} · unchanged` });
       return null;
   }

   node.status({ fill: "yellow", shape: "dot", text: `${seen} · ${changed.length} change(s)` });
   msg.payload = changed.join("\n");
   msg.topic = `Node-RED drift: ${changed.length} change(s)`;
   return msg;
   ```

4. **The notification** — `e-mail` (present in the `dpn-test` image), an
   `mqtt out` onto a topic somebody already reads, or a `debug` node to start
   with.

`flow.get`/`flow.set` live in memory: after a restart the comparison state is
empty and the first message after it is skipped. To keep it across restarts,
point `contextStorage` at a file in `settings.js` — which is a `settings.js`
change, and therefore a container restart.

### Seeing that it works

Two POSTs with a difference between them — the first sets the baseline, the
second reports:

```bash
ssh dpn-svr-iot
IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' node-red-test)
U=http://$IP:1880/node-red-test/drift

curl -sS -X POST -H 'Content-Type: application/json' \
     -d '[{"instance":"wag-prod","state":"clean"},{"instance":"cho-prod","state":"clean"}]' $U

curl -sS -X POST -H 'Content-Type: application/json' \
     -d '[{"instance":"wag-prod","state":"drifted","changed_lines":7},{"instance":"cho-prod","state":"clean"}]' $U
```

The second call yields `wag-prod: clean → drifted:7`. The next real sweep puts
the state back to reality.

**If it stays silent, check three things, in this order:**

1. Is there a status line under the `function` node? Then it is working, and
   nothing changed.
2. Was it the first POST after a deploy or a restart? A flow deploy resets the
   context of the tabs it changed, and the context is in memory — the
   measurement starts over.
3. Is the `http response` node wired **to the `http in`** rather than behind the
   function? Behind it, it never gets a message on "nothing changed", the
   request stays open, and `curl` runs into its timeout.

**The watching instance is watched too.** `dpn-test` is a row in the same
`drift.json`, and as soon as a tab runs there the sweep reports it `drifted`
until the flow is in Git. That is not a special case — it is the same loop as
for any other tab, and the first real pass through it.

**The flow is deliberately not the only alarm.** If the instance receiving the
webhook is down, nobody would be told — exactly the failure mode a monitor must
not have. The counterweight is the build status: the job goes UNSTABLE when the
POST does not arrive, and Jenkins' own notification hangs off that. The flow is
the flexible evaluation, not the supervision.
