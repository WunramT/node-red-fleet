# Open questions

What is still unknown, ranked by how much it blocks, with the command that closes it.

The numbers are identifiers, not an order: 1, 2 and 3 are answered and gone, and the gaps stay so that "open question 4" means the same thing in every document that already says it.

## Run this first

`scripts/collect-inventory.py` visits every site server over SSH. Read-only; it reports the existence of `credentialSecret`, `adminAuth` and any `FORGE_*` token, never their values.

```bash
pip install paramiko
# credentials go in hosts.local.json — gitignored, see the script's docstring
python3 scripts/collect-inventory.py
```

It writes `inventory/REPORT.md` (the answers), `registry.draft.yml` and `samples/*.flows.json`. It has already been run — `registry.yml` and the table at the bottom of this file carry its results. Re-run it after any change to a host.

## Nothing is blocking

Every instance runs its flow out of Git. What follows waits on a decision, a
measurement or somebody's time — none of it stops a deploy.

## Open

### 4. What are `slu-prod` and `slu-test` for?

Both are **running** containers with `adminAuth` on, reachable, and answering `401` — they are not stopped. They are empty: no `flows.json`, no `flows_cred.json`, and `/data` untouched since July 2025.

So there is nothing to deploy to them, and starting them changes nothing.

Two of the three steps this used to list are done: both carry an app — an empty `flows.json`, no palette, the version each already runs — so CI builds their images and the pipeline can reach them. What is left is the first one, which is the question itself:

1. **A flow.** Someone builds it, or copies one from elsewhere. What are these two instances for?

Two things follow from them having an app: `nodered-slu-prod-auth`, `nodered-slu-test-auth` and both `-credsecret` ids have to exist in Jenkins before the next `ALL` run, and a flow built in the browser without being captured would be deleted by the next deploy of the empty one.

They are also the safest possible first true deploy — an empty instance has nothing to lose.

**Unblocks:** nothing. Two instances stay idle until someone decides what they do.

### 5. Is `wag-prod`'s 15-node flow real production work?

`wag-svr-lin01` was rebuilt the week before the inventory. Its prod instance has 15 nodes and no palette modules; its test instance has 222 nodes and two. That pattern reads more like a prod instance not yet migrated back after the rebuild than like a small production application.

If prod is genuinely unmigrated, it is the ideal first target — nothing to lose. If it is live, the 15-node flow is still the easier of the two to bring under Git first.

### 6. Three things the go-live left behind

The estate is live; these are what the plan that got it there had not finished.

- **The eight database passwords are switched, not rotated.** `secrets-to-env.py`
  moved 25 fields across 8 apps onto `env`, so Git carries variable names and the
  hosts carry the values. The old values are still live in the databases and
  still in the history of any clone made before this repository was recreated.
  Rotating is one round of service edits per secret — the tool's output says
  which instances each one touches.
- **The drift page is built and not published** (decision 11).
  `Jenkinsfile.drift` sweeps every host daily and `render-drift.py` turns the
  result into one static page; both land as build artifacts. What is left is a
  place to put `public/index.html` where the team reaches it without opening
  Jenkins — GitLab Pages, or the nginx that already runs on these hosts. The
  It is kept as a Jenkins artifact per build, which is a history but needs a
  login. Giving it a URL anyone can open is what is left, and deliberately not
  done by writing into a host: publishing a static page that way is ordinary,
  but this job writes nothing that outlives it (`betrieb.md`).
- **The palette path has never run for real.** Flow deploys are proven daily;
  the second transport — image rebuild plus `DEPLOY_PALETTE=true` — has not been
  exercised end to end. Do it once on a workbench, outside core hours, because
  it recreates the container.

**Unblocks:** nothing that is running. The first is a security debt, the second
is what the team sees, the third is a proof.

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
| srem-test authenticates but does not serve its flow? | The reading was wrong, not the runtime: it was measured from a dev container, where MTU 1500 over a smaller tunnel lets the token exchange through (a few hundred bytes) and drops the flow. `srem-test` is in Git and deploys like the rest | `betrieb.md`, "Vom Arbeitsplatz aus" |
| Which FlowFuse-only nodes do the two flows use? | None that survive the move: no `project link` in either, so nothing routed through FlowFuse's broker and nothing had to be rebuilt on NATS or MQTT. Both are cut over and run from Git | `betrieb.md` |
| The Jenkins credentials? | Created for the 16 instances that were migrated; `slu-prod` and `slu-test` are the four ids still owed, since they only recently gained an app | question 4 above |
