# FlowFuse migration

Working document for the two device-agent instances. Measured 2026-09-11.
Delete it once both are cut over, as `wfm-prod-migration.md` was — the parts
that generalize belong in [`runbook.md`](runbook.md).

Direction and order are settled (decision 12, [`architecture.md`](architecture.md)):
copy the flow, stand up the plain container, **unenroll the device**, retire the
agent. The credential key comes across in `device.yml`.

## The two are not the same job

| | `pod-svr-lin01` | `dpn-svr-iot` |
|---|---|---|
| nodes / types / tabs | 154 / 25 / 2 | 852 / 41 / 11 |
| Node-RED | 4.0.8 pinned | `latest`, resolved to 4.0.9 |
| what it talks to | Modbus + TCP to machines, MQTT out | Postgres, MSSQL, MQTT, HTTP APIs on its own host |
| inbound | 20 `tcp in` on ports 50002/50003 | `/dashboard`, `/node_red_api/sap_import_finished`, `/update_tableau_workbooks` |
| function external modules | none | `axios` (6), `ajv` (1) |
| risk if it runs twice | **9 `modbus-write` nodes — it writes to machines** | duplicate rows and mails |

`pod` is the one to do first: fewer nodes, a pinned version, and its palette is
four modules. `dpn` is eleven concerns in one runtime and needs decisions before
it moves.

## Palette, taken from the flow rather than the package.json

FlowFuse installs more than the flow uses. What the image bakes:

- **pod**: `node-red-contrib-modbus`, `node-red-contrib-mssql-plus`,
  `node-red-contrib-postgresql`, `node-red-contrib-buffer-parser`.
  Dropped: `queue-gate` (unused), `@flowfuse/nr-assistant`,
  `@flowfuse/nr-project-nodes` (both unused).
- **dpn**: `@flowfuse/node-red-dashboard`, `node-red-contrib-postgresql`,
  `node-red-contrib-mssql-plus`, `node-red-contrib-queue-gate`,
  `node-red-contrib-google-sheets`, `node-red-node-email`, plus `axios` and
  `ajv` for the function nodes. Dropped: `modbus` and `google-translate`
  (unused), both `@flowfuse/*` platform modules.

Neither flow uses a `project link` node, so nothing routes through FlowFuse's
broker and nothing has to be rebuilt on NATS or MQTT before the cutover. That
was the blocking question; it is answered.

## Networking: nothing to publish but 1880

`pod`'s twenty `tcp in` nodes are all in **client** mode, connecting out to
`zund-cut01`…`zund-cut10` on 50002 and 50003. So the container publishes none of
them; what it needs is name resolution for the ten cutters, which is the
`dns_search` field the registry already carries.

Both agents publish `1880` on their host today, and both hosts' nginx is the
stock configuration — `location /` over `/usr/share/nginx/html`, no `proxy_pass`
in `conf.d`. So the new container takes the same shape: **publish 1880,
`admin_root: ""`**, and `dpn`'s inbound paths (`/dashboard`,
`/node_red_api/sap_import_finished`, `/update_tableau_workbooks`) answer at the
same host and port as before, with no nginx change on either host. The agent
serves its own editor at `/device-editor` with FlowFuse's auth; ours is
`adminAuth` at the root, from a Jenkins credential like every other instance.

## What still has to be measured

- **`axios` and `ajv` in a baked image.** Seven of `dpn`'s function nodes declare
  external modules. Node-RED installs those into the userDir with npm at
  runtime, which a baked image behind a firewall cannot do, and whether modules
  already present in the image satisfy it is not worth guessing. Measure it on
  the workbench before the window: `nr.py edit dpn-test --baked`, a function node
  that requires `axios`, Deploy, read the log.
- **Names.** Proposed `pod-prod` and `dpn-prod`, each with an empty `-test` twin
  as a workbench, matching the rest of the estate.

**The agent's `.npmrc` is not repo content.** It carries a registry credential
for `registry.flowfuse.com`, scoped to `@flowfuse-nodes` — a scope none of the
modules we keep belong to. It is not copied, not committed, and not needed:
`@flowfuse/node-red-dashboard` is a different scope and resolves from the public
registry, which the first CI build confirms.

## dpn-prod, as built

The flow normalized to 852 nodes over eleven tabs, and every one of them is its
own group — no link crosses a tab boundary anywhere in it. Eleven steps,
smallest first:

| | tab | nodes |
|---|---|---|
| 7 | Email | 3 |
| 11 | PoliMowa | 5 |
| 9 | EPC | 12 |
| 6 | homag | 44 |
| 1 | beil | 58 |
| 4 | Bäumer | 59 |
| 8 | Koch | 65 |
| 3 | zund | 101 |
| 5 | DBT | 102 |
| 10 | MDE_Collection | 149 |
| 2 | huh | 198 |

Two config nodes are shared: the local MQTT broker across seven groups, and the
`dpn-svr-postgres` connection across two. Both are fine held twice — a broker
and a database take many connections — so they do not force anything to move
together. Start with `Email` or `PoliMowa`: three and five nodes, so the first
step proves the mechanism rather than the flow.

**Twelve credentials sit in this flow in clear text** — eleven Postgres
passwords and one mail token — and they are in Git history as of the export
commit. Rotating them is not optional, and rotation forces the rest: once the
old password is dead, the flow needs the new one, and writing it back in clear
text repeats the problem. So the pair goes together, on the workbench, before
the first tab moves: rotate, and switch those fields to `env` so the value comes
from the container's environment and Git carries only the variable name. Same
for `pod-prod`'s one field.

```bash
python3 scripts/secrets-to-env.py apps/dpn-prod/flows.json            # what is in there
python3 scripts/secrets-to-env.py apps/dpn-prod/flows.json --write    # switch them
```

Eleven of the twelve are `postgreSQLConfig` passwords, and they carry a
`passwordFieldType`, so they switch. `env` is the only way out for them:
`node-red-contrib-postgresql@0.14.2` offers `['str', 'global', 'env']` on that
field and registers no `credentials` block, so there is nowhere in
`flows_cred.json` for it to go — unlike a node whose password the credential
store owns. `global` is also outside Git, but the value would have to reach the
context through a flow first, which buys nothing. Grouped by value they come to **four**
variables, not eleven — five of the pgbouncer configs and one unnamed node all
hold the same secret. The twelfth is the mail node's `token`, and it is
most likely not a secret at all: that node's `authtype` is `BASIC`, which does
not use the field, and the value reads as an identifier — `node-red-node-email`
defaults it to the msg property name `oauth2Response`. Its real SMTP login is in
`flows_cred.json`, which is why it never appears in the flow. Confirm it in the
editor; the scan matches on the field's name and says so.

The four variables go into the `node-red-prod` service on `dpn-svr-iot`, beside
its other host-side configuration. Not into `registry.yml` — its `variables` map
is committed.

The palette is the flow's, not the agent's: `modbus` and `google-translate` are
installed there and used by no node, and both `@flowfuse/*` platform modules go
with the platform. `axios` and `ajv` are not nodes at all — seven function nodes
`require` them, so they are in the image, and whether Node-RED resolves them
from there is what the workbench has to prove before the window.

## Cutover, tab by tab

The instance does not have to move in one step. The new container comes up with
every tab **disabled**, and then one group of tabs at a time is enabled here and
disabled in FlowFuse. The risk is one tab instead of a whole instance, and each
step is an ordinary flow deploy — Git commit, Jenkins, no restart.

The rule that makes it safe: **a tab runs in exactly one runtime at any moment.**
Enabled here means disabled there, in that order for a reader and the other way
round for a writer — `pod` writes Modbus to machines, so its tabs get disabled in
FlowFuse first, then enabled here.

What may not be split is decided by `link in` / `link out`: those pass messages
in-process, so a link crossing a tab boundary stops delivering the moment the two
ends run in different runtimes, and the sending side keeps firing as if nothing
happened. `cutover-plan.py` reports the groups:

```bash
python3 scripts/cutover-plan.py apps/pod-prod/flows.json
```

MQTT, NATS and HTTP are not couplings — they go through a broker or a socket and
work across runtimes. Shared config nodes are listed rather than grouped, because
whether they can be held twice depends on the thing behind them: a Modbus device
or a machine's TCP port usually takes one connection at a time.

`flows_cred.json` goes into the new `/data` **before the first tab is enabled**,
with the key in both files. Credentials belong to nodes, and a deploy from Git
carries none.

## pod-prod, as built

`apps/pod-prod/` is in the repository and `registry.yml` carries the instance.
The flow normalized to 154 nodes across two tabs, and `cutover-plan.py` reports
them as **two independent groups** — no link crosses between "Zund Europol" (53
nodes) and "Druckluft" (89), and they share no config node. So pod moves in two
steps, each one tab.

The image is `pod-prod:4.0.8-1`: the Node-RED version the agent runs, not the
estate's 4.0.9. The migration changes the control plane; the version move is
`bump-node-red.py` afterwards.

Still to do before the first tab:

- `nodered-pod-prod-auth` and `nodered-pod-prod-credsecret` in Jenkins. The
  secret is the `credentialSecret` from `device.yml` — the key `flows_cred.json`
  is encrypted with.
- The `node-red-prod` service in `/home/administrator/Base_Container/docker-compose.yml`,
  publishing 1880 once the agent is gone, `dns_search` `rah.polipol.intra` and
  `pod.polipol.intra` so `zund-cut01`…`zund-cut10` resolve.
- `settings.js` with `httpAdminRoot: '/node-red-prod'`, `adminAuth`, and the
  key; `/data/.config.runtime.json` with the same key; `flows_cred.json` copied
  in — all before the first tab is enabled.
- **A Postgres password sits in this flow in clear text**, in a
  `postgreSQLConfig` node, and it is in Git history now. Rotate it. Moving the
  field to `env` is a flow change for the workbench, not for the cutover.

**Both tabs are committed `disabled`.** That is the state the instance starts
in, and it is also the file that is copied into `/data` before the first start
— so `flows_cred.json` lands next to a flow whose nodes exist. Node-RED drops
credentials belonging to no node the first time it saves, and an empty `/data`
means every node is missing. Taking a tab over is then one commit
(`disabled: false`) and one flow deploy.

A `pod-test` workbench is not part of the cutover and comes after it.

## Standing the container up

`deploy.py` resolves the instance with `docker inspect <compose_service>`, so
the container must be **named** for that to work: `container_name: node-red-prod`,
not compose's generated `base_container-node-red-prod-1`.

```bash
# 1. the directory, laid out like the other hosts and owned by the uid the
#    container runs as — 1000 is the image's own node-red user, which is what
#    the service below uses by leaving `user:` out
sudo mkdir -p /home/administrator/Base_Container/node-red/prod/data
sudo chown -R 1000:1000 /home/administrator/Base_Container/node-red/prod/data
ls -ldn /home/administrator/Base_Container/node-red/prod/data

# 2. back up the compose file before editing it — it holds every other service
cd /home/administrator/Base_Container
cp docker-compose.yml docker-compose.yml.$(date +%F)

# 3. add the service, then check the file parses without starting anything
docker compose -f docker-compose.yml config --services
```

The service, with no `ports:` while the agent still holds 1880:

```yaml
  node-red-prod:
    container_name: node-red-prod
    image: harbor.aks-infra.polipol-service.de/dap-node-red/pod-prod:4.0.8-1
    restart: always
    environment:
      - TZ=Europe/Berlin
    volumes:
      - ./node-red/prod/data:/data
    networks:
      - app_network
    dns:
      - 192.168.48.28
    dns_search:
      - rah.polipol.intra
      - pod.polipol.intra
```

Copy the shape from a host that already works rather than trusting this block.
Measured on `wag-prod`: `user` `1001:1001`, bind `.../node-red/prod/data`, no
published port, `restart: always`, on `app_network` — and `image:
nodered/node-red:latest`, which is the estate's open gap, not the pattern to
copy (go-live plan, phase 2). `pod-prod` names its registry tag from the start.

The network exists on both hosts already (`172.32.1.0/24`, with each host's
nginx on it), so the compose file has to declare it as external rather than
create one:

```yaml
networks:
  app_network:
    external: true
```
`dns` and `dns_search` are the agent's, so `zund-cut01`…`zund-cut10` resolve the
way they do today.

A service in that file starts on any bare `docker compose up -d`. Until `/data`
is populated that would create an empty flow file, so populate `/data` in the
same sitting.

## Cutover, per instance

1. `docker cp` `flows.json`, `flows_cred.json` and `package.json` out of the
   running agent. Normalize the flow, commit it as `apps/<app>/`.
2. Registry entry, image built by CI, exact tag.
3. **Window.** Stop the agent first — for `pod` this is not about duplicate data,
   it is about two runtimes writing Modbus to the same machines.
4. Stand up the plain container with the `device.yml` key in **both**
   `settings.js` and `/data/.config.runtime.json` (runbook, "Moving an instance
   to a new container").
5. Verify: a stored credential decrypts, inbound paths answer, `nr.py check`
   clean.
6. **Unenroll the device** in FlowFuse, then remove the agent service from its
   compose file. Not just stop it: `restart: always` outlives a stop, and
   `image: latest` with only `device.yml` mounted means one `docker compose pull`
   after the unenroll leaves nothing to recover from.
