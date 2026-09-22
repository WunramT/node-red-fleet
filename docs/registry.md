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

`app: null` is allowed and currently unused: it is for an instance the pipeline should not deploy to, and since `slu-*` were given an empty app there is no such instance left. The schema does not model app→{dev,prod} pairs, because the measured `wag-svr-lin01` pair is two unrelated applications — one app is one instance.

That holds under decision 15 too: a `*-test` instance is a workbench with its own app, not a second view of prod's. What moves between the two is one tab at a time, through `scripts/promote.py`.

## Variable resolution

`global_variables`, then `variables` on top. The merged map describes what an instance's environment should hold — **no tool writes it into a container**. What the container gets is what its service in the host's compose file names, by hand (`betrieb.md`, "Passwörter, die der Flow aus der Umgebung liest"). Secrets never go here in any case: this file is committed.

It reaches the flow one way only: **Node-RED's own `${ENV}` substitution**, which resolves whole property values inside the running instance. A committed flow containing `${MQTT_BROKER_HOST}` therefore still opens in the editor.

`deploy.py` renders nothing. No app is shared, so no flow has to vary per instance — see decision 14 in [`decisions.md`](decisions.md).

## Base URL

`host` and `admin_root` are enough. There is no `admin_base_url` field, because `deploy.py` runs on the target host and reaches the instance by container IP on `app_network` — the base is resolved at runtime from the compose service name, not stored per instance (decision 10).

## Scope

18 entries: nine hosts with a `-prod`/`-test` pair each, and every one carries an app — so every one is something CI builds an image for and the pipeline can deploy to. `slu-prod` and `slu-test` are included with an empty flow while what they are for is decided (`open-questions.md`, question 4).

The Jenkins host map knows all of them. `collect-inventory.py` writes a pre-filled draft from the live hosts; re-run it after a host changes rather than editing this file from memory.

`pod-prod`, `pod-test`, `dpn-prod` and `dpn-test` were the two FlowFuse servers. They are ordinary entries since the cutover (decision 12) — the platform holds nothing any more, and the registry is again the only place that knows what runs where.
