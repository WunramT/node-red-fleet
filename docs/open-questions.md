# Open questions

What is still unknown, ranked by how much it blocks, with the command that closes it.

## Run this first

`scripts/collect-inventory.py` visits every site server over SSH. Read-only; it reports the existence of `credentialSecret`, `adminAuth` and any `FORGE_*` token, never their values.

```bash
pip install paramiko
# credentials go in hosts.local.json — gitignored, see the script's docstring
python3 scripts/collect-inventory.py
```

It writes `inventory/REPORT.md` (the answers), `registry.draft.yml` and `samples/*.flows.json`. It has already been run — `registry.yml` and the table at the bottom of this file carry its results. Re-run it after any change to a host.

## Blocking

### 1. Which FlowFuse-only nodes the two flows actually use

The credential key is answered (2026-09-11): it is `credentialSecret` in
`device.yml`, and neither agent's `.config.runtime.json` carries a
`_credentialSecret` of its own, so `flows_cred.json` is encrypted with the key
from the config and comes across with the file. The cheap migration.

What is open is what the flows use that only exists under FlowFuse. Both
projects pull `@flowfuse/nr-project-nodes`; a project-link node routes through
FlowFuse's broker and stops working once the device is unenrolled.

```bash
# on the host, against the running agent
C=$(docker ps -qf ancestor=flowfuse/device-agent:latest | head -1)
docker exec $C node -e '
const f=require("/opt/flowfuse-device/project/flows.json"), n={};
f.forEach(x=>n[x.type]=(n[x.type]||0)+1);
Object.entries(n).sort((a,b)=>b[1]-a[1]).forEach(([t,c])=>console.log(c,t));'
```

Any `project link in` / `project link out` / `project link call` is work that has
to be replaced before the cutover — with NATS or MQTT, which both hosts run.
Anything `ui-*` is FlowFuse Dashboard, which is open source and survives.

**Unblocks:** the migration procedure for two servers. It blocks nothing on the
other eight.

### 2. srem-test authenticates but does not serve its flow

Measured 2026-09-14 from a dev container: `POST /auth/token` answers `200` in
0.2s, `GET /flows` times out at 40s on both API versions. That was read as "the
runtime is up and wedged", and **that reading is now in doubt**: on 2026-09-21
eleven instances on six hosts showed the same pattern from a dev container, split
strictly by response size — everything at or below 291 bytes answered, `cho-test`
at 2.4 KB and everything larger timed out, `wfm-prod` included, two weeks after
it took a real deploy. Eleven runtimes do not wedge at once. The token exchange
proves the route with a few hundred bytes; a path that cannot carry a response
past one TCP segment passes it and stalls on the flow.

The cause was measured on 2026-09-21: an unauthenticated request for something
large, with no Node-RED involved, returned 0 bytes after 30s in the dev
container and immediately on the host — the container bridge is MTU 1500 over a
smaller tunnel (`inbetriebnahme.md`, 0.6).

So the question is open in a different way: re-measure `srem-test` **from its
own host**, or at least outside the dev container, before diagnosing the
runtime. `srem-test` is also the instance carrying 1008 lines of drift and a
plaintext Postgres password in its flow, so it was never deployable anyway.

```bash
ssh srem-svr-lin01 'docker logs node-red-test --tail 100'
ssh srem-svr-lin01 'docker exec node-red-test ls -l /data/flows.json /data/.config.runtime.json'
```

**Unblocks:** rolling `srem-test` into the pipeline. Nothing else — it is one
workbench instance.

### 3. Jenkins credentials for 14 instances

`registry.yml` now **names** 26 credentials — `nodered-<instance>-auth` and `nodered-<instance>-credsecret`. Naming them is not the same as having them: the ids validate, and a deploy fails at runtime until the credentials exist in Jenkins.

`credential_secret_id` in particular cannot be created before the backup gate, because it must hold each instance's **existing** generated key. Creating it from a fresh value re-encrypts every stored credential into garbage. Order matters here — [`runbook.md`](runbook.md).

`wfm-prod` needs its `adminAuth` switched on before a credential can exist: it is off, so there is nothing to authenticate against yet. `wfm-test` is new, so its `adminAuth` and both credentials are set up from scratch — that is the pair the pipeline is proven against first.

**Unblocks:** any real deploy.

### 4. What are `slu-prod` and `slu-test` for?

Both are **running** containers with `adminAuth` on, reachable, and answering `401` — they are not stopped. They are empty: no `flows.json`, no `flows_cred.json`, and `/data` untouched since July 2025.

So there is nothing to deploy to them, and starting them changes nothing. To bring them into the pipeline, three things are needed, in this order:

1. **A flow.** Someone builds it, or copies one from elsewhere. This is the actual question — what are these two instances for?
2. `apps/slu-prod/` and `apps/slu-test/`, each with `flows.json`, `package.json` and a `Dockerfile`. `scaffold-apps.py` writes them once a flow exists in `samples/`.
3. `app: slu-prod` in `registry.yml` in place of `null`, and `nodered-slu-prod-auth` in Jenkins.

The pipeline needs no change. They are also the safest possible first true deploy — an empty instance has nothing to lose.

**Unblocks:** nothing. Two instances stay idle until someone decides what they do.

## Not blocking

### 5. Is `wag-prod`'s 15-node flow real production work?

`wag-svr-lin01` was rebuilt the week before the inventory. Its prod instance has 15 nodes and no palette modules; its test instance has 222 nodes and two. That pattern reads more like a prod instance not yet migrated back after the rebuild than like a small production application.

If prod is genuinely unmigrated, it is the ideal first target — nothing to lose. If it is live, the 15-node flow is still the easier of the two to bring under Git first.

## Answered

| Question | Answer | Recorded in |
|---|---|---|
| The palette versions? | Collected from the running instances; all 12 apps carry a `package.json` and a `Dockerfile` | `apps/` |
| Harbor project for the images? | `dap-node-red` exists. Tags are `<registry>/dap-node-red/<app>:<node-red-version>-<palette build>`, filled in for all 13 | `registry.yml` |
| The two settings.js deviations? | Both normalized to what the others do — `wfm-prod` gets `adminAuth`, `cho-prod` goes back to `level: "info"`. One settings.js in the repo | decision 13 |
| What is every instance's admin root? | Probed on all 13: 8 on `/node-red-prod` or `/node-red-test`, 5 on plain `/`. All in `registry.yml` | [`architecture.md`](architecture.md) |
| Which Node-RED versions are running? | Three — 4.0.5, 4.0.9, 5.0.1. Pin each instance to its current version first; converging is a separate upgrade | [`architecture.md`](architecture.md) |
| Are the settings.js files the same file? | Yes — one template plus env overrides is viable. The literal text differs by whitespace, comment state and settings.js vintage; the real config differences are three, listed below | [`architecture.md`](architecture.md) |
| Which instances share logic? | None. No two flows match, so every instance gets its own `apps/` directory | [`architecture.md`](architecture.md) |
| Where does the FlowFuse flow live? | Inside the agent container at `/opt/flowfuse-device/project/flows.json` — `docker cp` from a running agent, not a platform export | [`architecture.md`](architecture.md) |
| Can the FlowFuse credentials come across? | Yes — `credentialSecret` in `device.yml`, with no `_credentialSecret` in either agent's `.config.runtime.json` | [`architecture.md`](architecture.md) |
| How does the deploying agent reach the Admin API? | Jenkins ships `deploy.py` over SSH and runs it on the target host, reaching the container by IP on `app_network` | decision 10 |
| How many servers and instances? | 16 runtimes on 10 servers: 14 plain, 2 FlowFuse. 12 carry a flow — `slu-prod` and `slu-test` are empty, and `wfm-test` was added rather than found | [`architecture.md`](architecture.md) |
| `wag-svr-lin01` or `wag-svr-lin01n`? | `wag-svr-lin01`, rebuilt the week before the inventory — current baseline | [`architecture.md`](architecture.md) |
| Does this repo become the scaffold? | Yes; the template stack has been removed | [`architecture.md`](architecture.md) |
| Would a UI help? | Yes, as a static read-only drift report — not a control plane | decision 11 |
