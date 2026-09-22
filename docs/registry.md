# registry.yml

The instance inventory. One entry per running Node-RED instance, plus values shared by all of them. It is the only place that knows which app runs where.

Validated in GitLab CI against `schemas/registry.schema.json`. CI fails on: duplicate `name`, an `app` that has no `apps/<name>/` directory, a missing credential id, an unpinned `image_tag`, or any unknown key.

## Shape

```yaml
global_variables: {}

instances:
  - name: wag-prod
    host: wag-svr-lin01
    compose_service: node-red-prod
    compose_file: /home/administrator/base_container/docker-compose.yml
    app: wag-prod
    admin_root: /node-red-prod
    auth_credential_id: nodered-wag-prod-auth
    credential_secret_id: nodered-wag-prod-credsecret
    dns_search: [rah.polipol.intra, wag.polipol.intra]
    image_tag: harbor.aks-infra.polipol-service.de/dap-node-red/wag-prod:5.0.1-1
    variables: {}
```

## Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | unique instance key across the whole registry, and the `--instance` argument. Host-qualified (`cho-prod`), because the compose service name is not unique — six hosts each run a service called `node-red-prod` |
| `compose_service` | yes | the service name on that host, for `docker compose up -d <service>` |
| `compose_file` | yes | absolute path to that host's compose file. Four different paths are in use — `code/node-red/`, `energy/`, `Base_Container/`, `base_container/` — so it cannot be derived and is stored |
| `host` | yes | server the instance runs on; must appear in the Jenkins host map |
| `app` | yes | directory under `apps/`, or `null` for a standalone instance whose flow is not shared |
| `admin_root` | yes | the **runtime's** `httpAdminRoot`, e.g. `/node-red-prod`. The API base path — the flows endpoint is `<base><admin_root>/flows`. The deploy calls the container directly, so this is what the runtime serves, not what a browser reaches through a proxy. A path that answers in a browser proves nothing about the runtime: `wfm-prod` returned `401` on `/node-red-prod` through nginx while two deploys failed with `404`, because only the runtime's own `httpAdminRoot` decides. Across this estate each runtime does serve its own prefix, so `wfm-prod`'s `admin_root` is `/node-red-prod` |
| `auth_credential_id` | yes | Jenkins credential holding the `adminAuth` user/password used for `POST <admin_root>/auth/token` |
| `credential_secret_id` | yes | Jenkins credential holding this instance's pinned `credentialSecret` |
| `dns_search` | no | DNS search domains for the container |
| `image_tag` | yes | exact Harbor tag. A `latest`, `main` or otherwise floating tag fails validation |
| `variables` | no | per-instance env vars, merged over `global_variables` |

`app: null` is normal, not a gap. Most instances are one-offs; only instances that genuinely share logic point at the same `apps/` directory. The schema does not model app→{dev,prod} pairs, because the measured `wag-svr-lin01` pair is two unrelated applications.

That holds under decision 15 too: a `*-test` instance is a workbench with its own app, not a second view of prod's. What moves between the two is one tab at a time, through `scripts/promote.py`.

## Variable resolution

`global_variables`, then `variables` on top. The merged map becomes the container's environment.

It reaches the flow one way only: **Node-RED's own `${ENV}` substitution**, which resolves whole property values inside the running instance. A committed flow containing `${MQTT_BROKER_HOST}` therefore still opens in the editor.

`deploy.py` renders nothing. No app is shared, so no flow has to vary per instance — see decision 14 in [`decisions.md`](decisions.md).

## Base URL

`host` and `admin_root` are enough. There is no `admin_base_url` field, because `deploy.py` runs on the target host and reaches the instance by container IP on `app_network` — the base is resolved at runtime from the compose service name, not stored per instance (decision 10).

## Scope

13 entries today: 6 servers with a dev/prod pair, plus the single instance on `wfm-svr-lin01`. `collect-inventory.py` writes a pre-filled draft from the live hosts.

Two of those hosts are missing from the Jenkins host map, which knows 8 — `wfm-svr-lin01` and `dpn-svr-iot` have to be added to it before the pipeline can reach them.

Instances still running under FlowFuse are **not** entries yet. They join the registry once they have been migrated to plain containers (decision 12); until then the draft carries them as a comment, so the file records that they exist without claiming the pipeline can deploy them.
