# Go-live plan

From here to the point where the team sees the pipeline live and uses it. As of 2026-09-08.
The step-by-step version of this, per server and in German, is
[`inbetriebnahme.md`](inbetriebnahme.md) — this file holds the phases and the reasoning,
that one the order and the commands.

Where we stand, in one sentence: every building block (registry, normalizer, `apps/`, `deploy.py`, `drift-check.py`, Jenkinsfile) is built and dry-run-verified for **one** instance (`wag-prod`) — but no real `POST /flows` has ever happened, and only for `wag-prod` are the Jenkins credentials demonstrably in place. Background and reasoning: [`architecture.md`](architecture.md), [`decisions.md`](decisions.md), [`open-questions.md`](open-questions.md), [`runbook.md`](runbook.md).

The order is deliberate: look first, then write on the safest candidate, then roll out in breadth. No step skips the one before it.

---

## Phase 0 — Take stock of the whole fleet (no risk)

Read-only. The goal: know which of the 16 instances currently match Git, before anything is written anywhere.

**Done on 2026-09-08** (fleet dry run via Jenkins): 8 clean (`cho-prod`, `cho-test`, `gor-prod`, `gor-test`, `jan-prod`, `jan-test`, `srem-prod`, `wag-prod`), 2 drifted (`srem-test` 1008 lines, `wag-test` 37 lines), `wfm-prod` never reached (the Jenkins credential was missing).

- [ ] Run the sweep. From a workstation that is `nr.py status`, which supplies the URLs from `nr.local.json`; `drift-check.py` called directly only resolves an address on the target host. This used to need Jenkins, because `srem-prod`, `srem-test` and `slu-prod` publish no port — since the proxy routes every host by path, all 14 are addressable from a workstation, and `nr.local.example.json` carries the URLs (`runbook.md`, "Reaching an instance from a workstation") — addressable, but on 2026-09-21 only the instances whose flow fits in one packet actually answered there, so the sweep that counts is still the one that runs on the hosts. A scheduled Jenkins job is still what phase 3 needs; the one-off sweep no longer waits for it.
- [ ] Record the result per instance: `clean` / `drifted` / `unreachable` / `no-app`.
- [ ] For every `drifted` instance: review the diff (`--show-diff`). Decide per instance — commit it or discard it deliberately (`runbook.md`, "On 409"). Do **not** touch these instances through automation before that decision is made.
- [ ] For every `unreachable` instance: find the cause (missing credential? host not reachable? wrong `admin_root`?).

**Result of this phase:** a table showing where a later first deploy would be a risk-free no-op (`clean`) and where something has to happen first.

---

## Phase 1 — Prove the write path (done, 2026-09-10)

The write path is live, on both a workbench and a production instance:

- `wfm-test` took the first real `POST /flows` from Git.
- `wfm-prod` was **cut over** to a new container named like the other hosts: the
  flow deployed out of Git against a reviewed `rev`, credentials carried across
  by hand because Git holds none (`runbook.md`, "Moving an instance to a new
  container"), old container stopped first because the flow publishes every
  five seconds.

Still open on `wfm-svr-lin01`:

- [ ] Remove the stopped `node-red` service from
      `/home/administrator/Base_Container/docker-compose.yml`. Until then any
      `docker compose up -d` on that file starts a second publisher. Leave
      `node-red/data/` and the pre-cutover tarball on disk — that tarball is
      the only copy of the credentials as they were.
- [ ] The `409` abort has not been seen live yet. Change something in the
      editor, do not commit it, then deploy with the `EXPECT_REV` from *before*
      that change. Expect exit 2 and nothing written. Without `EXPECT_REV` the
      run reads the current rev and posts against it moments later, so the edit
      is inside that rev and gets flattened — the abort is only reachable with
      the reviewed rev (`runbook.md`, "Flow deploy").
- [ ] Run both change loops once end to end (README, "Change a tab" and "New
      tab"), on `Flow 1` rather than the publishing tab.

## Phase 1b — Plaintext database passwords, estate-wide

Found 2026-09-14 while migrating `pod-prod`: `node-red-contrib-postgresql` keeps
its password in `flows.json` rather than in the credential store, so every one of
them is committed and permanent in history.

```bash
python3 scripts/secrets-to-env.py apps/*/flows.json
```

**25 fields across 8 apps — but only 8 distinct secrets**, because several
instances share a database login. `dpn-prod` holds 11 of the fields; `gor-prod`
and `srem-prod` 3 each; `gor-test`, `srem-test` and `wag-test` 2; `jan-test` and
`pod-prod` 1.

- [ ] Rotate all 8. The tool's output says which instances each one touches, so
      one rotation is one round of service edits rather than a guess.
- [ ] `secrets-to-env.py apps/*/flows.json --write`, then commit. The value
      becomes a variable name and Git carries nothing else.
- [ ] Put each variable in that instance's service in its host's compose file —
      not in `registry.yml`, whose `variables` map is committed.
- [ ] Deploy each changed flow. It is an ordinary flow deploy, no restart, but
      the container needs its environment first, so the compose edit comes
      before the deploy.

Rotating without switching leaves the next commit carrying the new secret;
switching without rotating leaves the old one live in history. Both halves or
neither.

## Phase 2 — Roll out to the remaining `clean` instances

`wfm-prod` and `wfm-test` are done. For every instance reported `clean` in phase 0, `wag-prod` first:

- [ ] Run the backup gate (as in phase 1).
- [ ] Pin `credentialSecret`.
- [ ] For `cho-prod` additionally: `level: "info"` instead of `"trace"` (decision 13).
- [ ] Create the Jenkins credentials for `auth_credential_id` and `credential_secret_id` where they are still missing — phase 0 should already show that through `unreachable`.
- [ ] **Point the compose service at the registry's tag.** Measured on
      `wag-prod` 2026-09-14: it runs `nodered/node-red:latest` from Docker Hub,
      not the image CI builds. Until each service names its `image_tag`, that
      field describes something nothing runs — and `DEPLOY_PALETTE=true` would
      recreate the container on `latest`, without the baked palette. One edit
      per host, and it restarts the container, so it belongs with the
      `settings.js` edit rather than after it.
- [ ] One `DRY_RUN=true` per instance as a check, then `DRY_RUN=false`.

Instances that drifted in phase 0 do **not** come along automatically — their turn comes after the deliberate commit-or-discard decision.

---

## Phase 3 — Visibility for everyone (the drift page)

So far `drift-check.py` exists only as a CLI tool. For everyday team use, the static overview page foreseen in `decisions.md` (decision 11) is still missing.

- [ ] Add a CI stage that runs `drift-check.py --all --json` regularly (e.g. daily, a scheduled Jenkins job) and renders the JSON into a simple static HTML page.
- [ ] Publish the page (GitLab Pages or similar) — read-only, no deploy button (deliberately, see decision 11).
- [ ] Shows per instance: Git status (clean/drifted), flow version, image tag, last deploy time.

**This is the point where it can be shown to the team**, without anyone having to operate a CLI: one page, one glance, a clear status per instance.

---

## Phase 4 — Test the palette path once

Only the flow deploy path (no restart) has been tested for real so far. The second transport — image rebuild plus restart — is still entirely unproven.

- [ ] On a test instance (e.g. `wag-test`), make a harmless change to `apps/wag-test/package.json` and commit it.
- [ ] Check that GitLab CI builds and signs a new image.
- [ ] Set `image_tag` in `registry.yml` to the new tag.
- [ ] Run the Jenkins job with `DEPLOY_PALETTE=true`, `DRY_RUN=false` — deliberately outside core hours, because it restarts the container (an ingest gap is expected, see `architecture.md`).

---

## After that: the two things that stay open

These do **not** block "showing it live" — they concern only the 2 FlowFuse instances and 2 empty instances, not the 12 apps already finished:

- **FlowFuse migration** (`pod-svr-lin01`, `dpn-svr-iot`): the credential key is answered — it comes across in `device.yml`. What is left is the FlowFuse-only nodes (open question 1) and the cutover itself. Its own undertaking, after phase 2.
- **`slu-prod` / `slu-test`**: empty, no decision on what they are for. No `apps/` directory until that is settled.

---

## In short

| Phase | What | Risk | Result |
|---|---|---|---|
| 0 | Drift check across all 16 instances | none (read-only) | **done** — 8 clean, 2 drifted, 1 blocked |
| 1 | Set up `wfm-test`, first real deploy to it | none (new, empty instance) | write path + `409` case proven live |
| 1b | Rotate 8 database passwords, move them to env | low (a flow deploy each) | no secret in the repository going forward |
| 2 | Roll out to all `clean` instances, `wag-prod` first | low, the pattern repeats | all 12 apps run through the pipeline |
| 3 | Static drift page | none | showable to the whole team, without a CLI |
| 4 | Test the palette path once | medium (restart) | second transport proven |

**"Live" in the sense of "can be shown to and used by the team"** is realistically reached after phase 3: real deploys run through the pipeline for every finished instance, and there is a page anyone can read without prior knowledge.
