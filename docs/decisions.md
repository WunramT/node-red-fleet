# Decisions

Closed decisions with their reasoning. A decision marked **Closed** is settled; reopen it only with new evidence about the environment, not with a fresh preference.

## 1. Git is the source of truth — Closed

Flows live in Git as normalized JSON. Every change is a commit, reviewable as a diff, revertable.

The alternative was a database-backed control plane (the *DAP Node-RED Management Platform* concept: FastAPI + Vue 3 + PostgreSQL + Jinja2 + SSH deploy). Rejected:

- a DB has no history a reviewer can read, and needs a UI before anyone can see what is deployed
- Jinja2 placeholders baked into the stored flow make the flow unopenable in the editor — no return path, so work drifts back to manual editing
- SSH file writes with no `rev` check silently overwrite hand edits
- a container restart on every flow deploy opens an MQTT ingest gap, daily

Harvested from that concept and kept: the manifest shape (`global_variables` + per-instance `variables` → `registry.yml`), the `setup_server.sh` bootstrap idea, and `--dry-run`. Its narrow Jinja2 render step turned out to have no job here — decision 14.

Its port-allocation logic is not needed. Ports are already allocated, if inconsistently — 6 instances publish one, 7 do not — and nothing in this design adds an instance, so there is nothing to allocate.

## 2. Admin API is the flow transport — Closed

`POST <admin_root>/flows` with the `rev` from a preceding `GET`. It is the only transport that deploys a flow without restarting the container and detects concurrent edits.

## 3. A `rev` conflict aborts the pipeline — Closed

`409` means the running flow diverged from Git — someone edited in the browser. That is information. Flattening it destroys the edit and teaches everyone that the pipeline eats their work.

No `--force`. Not as a flag, not as a fallback, not behind a confirmation prompt. Resolution is: pull the running flow, normalize it, commit or discard it deliberately, then deploy.

## 4. Palette changes go through the image, not the API — Closed

npm modules cannot be installed through the Admin API. Palette changes rebuild `apps/<app>/Dockerfile` and recreate the service. That restart is acceptable because palette changes are rare; a flow-deploy restart would not be, because flow changes are daily.

`functionExternalModules: true` is set but unused. It is the loophole that would let a function node pull its own npm dependency at runtime and make the baked image a false guarantee — so CI fails on any `"module":` declaration in a function node. Keeping usage at zero is what keeps the image the single palette source.

## 5. Exact image tags — Closed

`nodered/node-red:latest` with `restart: always` means each host silently runs whatever it last pulled. Every image reference is pinned to an exact tag, in `base/Dockerfile` and in `registry.yml`.

## 6. Flows in the repo stay editor-valid — Closed

No placeholders, no template syntax in a committed `flows.json`. Rendering happens in CI, on a copy. The moment a committed flow stops opening in the editor, the editor→Git return path dies and manual deployment comes back.

Node-RED's `${ENV}` substitution replaces a whole property value, and it runs inside the instance, so a committed flow containing `${MQTT_BROKER_HOST}` still opens in the editor. Composite strings would need more, but no app is shared, so none of them has to vary — decision 14.

## 7. Secrets come from Jenkins credentials only — Closed

No secret value in the repo, in pipeline logs, or in tool output. The inventory script reports the *existence* of a `credentialSecret`, never its value; anything built on it preserves that property.

`credentialSecret` must be pinned to each instance's **existing** generated value — a new value re-encrypts and breaks every stored credential. Procedure: [`runbook.md`](runbook.md).

## 8. Split CI: GitLab builds, Jenkins deploys — Closed

Discovered in this repo rather than decided: `.gitlab-ci.yml` builds, scans and cosign-signs images to Harbor via `ci-cd-catalog` components; `Jenkinsfile` reaches the site hosts over SSH with per-host credentials. The reach differs, so the split follows it — validation and build in GitLab, deploy in Jenkins. See [`architecture.md`](architecture.md).

## 9. drift-check is read-only — Closed

It reports. It never writes, never reconciles, never "fixes" an instance. A reconciling drift checker is decision 3 reintroduced through the back door.

## 10. The flow deploy runs on the target host — Closed

Jenkins ships `deploy.py` over its existing SSH connection and runs it there; the script reaches the instance by container IP on `app_network`.

Port publishing is inconsistent — 6 of 13 instances publish one — and the site servers sit in separate subnets, so a central agent would reach some instances and not others. From the host every instance is reachable by container IP, which the inventory confirmed on all 13. One code path instead of two.

SSH carries the script; it never writes a flow file — that would be decision 2 abandoned for the transport it replaced.

## 11. The visibility layer is a static report — Closed

A read-only page rendered from `drift-check.py` output plus Git: which instances match Git, which drifted, which flow version and image tag each one runs. Generated in CI, published as a static page.

The pull toward a real web application is understandable — the current state is genuinely invisible. It is still the wrong trade. A read-only report answers every question that matters here with a JSON producer and an HTML template. A web application answers the same questions and adds a backend, a database, an auth layer and a deployment of its own, and then grows a deploy button — at which point deploys stop being reviewed commits and decision 1 is undone from inside the browser.

Deploys stay in the pipeline, where they are reviewed and recorded. The page shows state.

## 12. FlowFuse is a migration source, not a deploy target — Closed

The two FlowFuse-managed servers are exported once into `apps/`, brought up as plain containers, and then deployed like every other instance. FlowFuse gets no transport, no `registry.yml` entry, no pipeline stage.

Keeping it alongside would mean maintaining two control planes with two answers to "what is running", which is the problem this project exists to end. And FlowFuse owning the flows is decision 1's rejected shape — a database as source of truth, with the flow reachable only through its platform.

What the migration costs, and what it does not, depends on where the authoritative flow lives and whether the credential key can be exported. Both are inventory questions before they are design questions. See [`architecture.md`](architecture.md) and [`open-questions.md`](open-questions.md).

## 13. One settings.js, and the two deviations normalized — Closed

The 13 plain instances configure the same thing. Nine groups by literal text collapsed to five by configuration, and of those five, two were the per-instance admin root and one was the settings.js scaffold that 5.0.1 generates. Two were real deviations, and both are being brought back to what the others do:

- **`wfm-prod` gets `adminAuth`.** It answered `200` unauthenticated — its editor and Admin API were open to anyone who could reach the container, on a host that publishes 1880.
- **`cho-prod` goes back to `level: "info"`.** It was the only instance logging at `trace`.

So the repository holds **one** `settings.js`, with the genuinely per-instance values — `httpAdminRoot`, `dns_search`, `adminAuth`, `credentialSecret` — supplied per instance rather than forked into 13 files.

Every change to `settings.js` restarts the container, so they are batched: the backup gate, the `credentialSecret` pin and these two fixes are one edit and one restart per instance, not three. Procedure: [`runbook.md`](runbook.md).

## 14. No render step, because nothing is shared — Closed

`deploy.py` posts the committed flow as it stands. There is no env-var substitution pass and no Jinja2 pass.

The render step existed to parameterize one artifact for N instances, and the inventory established that N is always 1: no two instances share a flow, so every app deploys to exactly one instance and there is nothing to vary. A composite string like `mqtt-${SITE}/events` can simply hold its literal value, which is also what keeps the flow openable in the editor (decision 6).

Node-RED's own `${ENV}` substitution still works at runtime for whole property values, fed by `variables` in `registry.yml` through the container environment. That path costs nothing and stays.

If instances ever do share an app, the Jinja2 pass comes back — on a copy, in CI, for composite strings only. Until then it would be a moving part with no job.

## 15. A test instance is a workbench, not a copy of prod — Closed

`*-test` holds nothing by default. Work starts by copying the tab in question
onto it, and ends by moving that tab to `*-prod`. `scripts/promote.py` does
both; `docs/runbook.md` has the two loops.

The alternative was the obvious one: keep test as a standing mirror of prod, so
a change can be tried against a full copy. It has only two states and both are
worse.

- **Mirror running.** There are no test PLCs and no test extruder. Two runtimes
  would poll the same OPC UA endpoints and publish the same messages to the same
  broker. That is not a test environment, it is production twice, and the only
  symptom is that the data arrives twice.
- **Mirror disabled.** Then it is inert, and it rots: prod moves on, the copy
  does not. Before working on a tab you would refresh it from prod anyway — so
  the mirror buys nothing, while leaving a large stale artifact that reads like
  the truth.

The measured estate already worked this way, which is what settles it: `cho-test`
has 6 nodes against prod's 68, `wag-test` 222 against prod's 15, `srem-test` 79
against prod's 205. These were never staging copies. They are workbenches with
leftovers, and `wag-test` at 222 nodes shows what happens when nobody clears one.

Two consequences worth naming:

- **Drift stays meaningful.** An empty workbench is `clean`; one in use is
  `drifted`, which now reads as "someone is working there". Under a shared app
  the instance not yet promoted to would report drift permanently, and the
  visibility page from decision 11 would be mostly false alarms.
- **A tab is not self-contained.** `link` nodes cross tabs, subflows are shared,
  and this estate uses both — `srem-prod` has 8 link nodes and 18 subflow
  instances. So promotion moves a dependency closure, and reports a link whose
  partner stays behind. Testing one tab alone still does not test the whole
  instance, and nothing here pretends otherwise.

A promoted tab's state follows the direction of the promotion, not the source:
onto the workbench it arrives disabled, into prod it arrives enabled. A prod tab
is running when you take it, and a copy that keeps running would publish the
same messages from a second runtime — the failure that has no symptom other
than duplicated data. Nothing arrives stopped in production either, which would
be a silent outage.

This narrows decision 14 rather than reopening it: two instances still never
share an `apps/` directory, so there is still nothing to render. Config nodes
carry the environment instead, one copy per app, and promotion never overwrites
an existing one — which is what keeps a workbench's broker out of prod, without
a substitution layer.
