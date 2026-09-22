# dap-node-red

Git holds the flows for 14 Node-RED instances, CI deploys them. Nothing is
edited on a production instance by hand.

Two transports. **Flow logic** goes through the Admin API and restarts nothing
but the tabs that changed. **Palette modules** need a rebuilt image and a
container recreate.

A `*-prod` instance runs the real thing. Its `*-test` twin is a workbench,
empty unless something is being tested. One tab moves between them at a time.

Not sure which of those you are doing? Let it ask:

```bash
python3 scripts/nr.py            # pick a task, then walk it step by step
python3 scripts/guide.py --list  # the tasks, without picking one
```

It prints the whole loop first, fills in your instance and tab names, and asks
before every command. Commits, pushes and Jenkins runs stay yours: the guide
tells you to make them, it does not make them for you.

## Change a tab prod already runs

```bash
python3 scripts/nr.py check wfm-prod                              # must be clean
python3 scripts/nr.py promote wfm-prod wfm-test "Flow 1" --copy   # arrives DISABLED
git commit -am "promote(wfm-test): Flow 1 onto the workbench" && git push

python3 scripts/nr.py edit wfm-test        # enable that one tab, change it, Deploy, Ctrl-C
python3 scripts/normalize.py --write apps/wfm-test/flows.json
git diff apps/wfm-test/flows.json
git commit -am "flows(wfm-test): ..." && git push
```

Deploy `wfm-test`, try it, then ship it:

```bash
python3 scripts/nr.py promote wfm-test wfm-prod "Flow 1" --move
git commit -am "promote(wfm-prod): ship Flow 1" && git push
```

Deploy **`wfm-test` first, then `wfm-prod`**. That order is what keeps one tab
from running in two places.

## New tab

Same loop without the first promotion.

```bash
python3 scripts/nr.py edit wfm-test        # add a tab, Deploy, Ctrl-C
python3 scripts/normalize.py --write apps/wfm-test/flows.json
git commit -am "flows(wfm-test): add <tab>" && git push
# deploy wfm-test, try it
python3 scripts/nr.py promote wfm-test wfm-prod "<tab>" --move
git commit -am "promote(wfm-prod): ship <tab>" && git push
# deploy wfm-test, then wfm-prod
```

## Deploy

Jenkins writes. Two runs, and the second is pinned to what the first showed you.

| | `INSTANCE` | `DRY_RUN` | `EXPECT_REV` |
|---|---|---|---|
| look | the instance | `true` | empty |
| write | the instance | `false` | the `rev` the dry run printed |

Without `EXPECT_REV` the deploy overwrites whatever it finds, and says so. With
it, anything that changed the instance in between stops the write. Add
`DEPLOY_PALETTE=true` only when `package.json` changed; that recreates the
container.

## Rules that bite

- **A `409` is not a failure to route around.** Someone edited in the browser.
  `nr.py capture <inst>`, commit, deploy again. There is no `--force`.
- **The editor opens every tab disabled.** Enable the one you work on. What you
  enable runs for real, against real systems.
- **`--copy` down, `--move` up.** A tab left enabled on the workbench publishes
  alongside prod, and the only symptom is data arriving twice.
- **Image tags are exact.** `latest` fails validation. A palette change raises
  the build suffix, and CI refuses a palette change without it.
- **Secrets stay in Jenkins.** Never in a commit, a log, or a chat. Some
  nodes keep a password in the flow instead of the credential store —
  `secrets-to-env.py` finds those.
- **Name the compose service.** `docker compose up -d node-red-prod`. That file
  holds other people's services too.

## Commands

| | |
|---|---|
| `nr.py` | Front door. Pick a task and be walked through it, or pick a single action. |
| `guide.py --list` | The five task loops. `--print <task>` shows one without running anything. |
| `nr.py status` | Every instance: does it still match Git? |
| `nr.py check <inst>` | Same for one, with the diff. |
| `nr.py edit <inst>` | Local editor on a copy. `--baked` for palette nodes (pull the image once, see below), `--isolated` for no network. |
| `nr.py capture <inst>` | Read a running flow back into `apps/`. |
| `nr.py deploy <inst>` | Dry run only. Real deploys go through Jenkins. |
| `nr.py promote <a> <b> <tab>` | Move one tab and its dependencies. `--copy` or `--move`. |
| `cutover-plan.py <flow>` | Which tabs can move to another runtime alone, and which are linked together. |
| `secrets-to-env.py <flow>` | Finds plaintext passwords in a flow; `--write` moves them to environment variables. |
| `normalize.py --write <flow>` | Canonicalize a flow so it diffs readably. Before every commit. |
| `validate-registry.py` | Registry against the schema and the rules around it. |
| `drift-check.py --all --json <out>` | Read-only fleet sweep. |
| `bump-node-red.py --to <version>` | Move instances to another Node-RED version. |
| `gen-image-pipeline.py` | Regenerate the build jobs after adding an instance. |
| `collect-inventory.py` | Re-read the hosts over SSH. Reports that a secret exists, never its value. |

Setup. Open the repo in the dev container and `.devcontainer/setup.sh` does it
for you: dependencies, `nr.local.json` from the example, the registry check,
every suite. Without the container, run that script yourself. Either way, fill
in the URLs in `nr.local.json` before `check`, `capture` or `deploy`. A blank
password is prompted for and not stored.

The dev container mounts the engine's socket so `nr.py edit` can start the
editor. That is full control of that engine, with no narrower scope available —
worth knowing, though it reaches only your own workstation: no host key and no
Jenkins credential lives in the container. Every other command needs nothing but
Python, so the mount can be dropped if you would rather run `edit` outside.

`--baked` runs the app's own Harbor image. The login belongs to the client, the
image to the engine both share — so pull it once on the workstation
(`podman pull <image_tag>`, per app) and every later run finds it. From a dev
container `nr.py` stops with that exact command instead of failing in compose.

## Where things live

| | |
|---|---|
| `registry.yml` | What runs where. Every tool reads it; nothing hard-codes an instance. |
| `apps/<app>/` | `flows.json` as deployed, `package.json` as the palette, its `Dockerfile`. |
| `scripts/nodered.py` | The shared library: instance list, addresses, Admin API, image tags. |
| `docs/inbetriebnahme.md` | Alle Server auf den Repo-Stand bringen — die Reihenfolge zum Livegang, inklusive FlowFuse. |
| `docs/runbook.md` | Operating it: backup gate, deploys, `409` recovery, moving an instance. |
| `docs/architecture.md` | The system and the measured facts about the estate. |
| `docs/decisions.md` | Why it is built this way. Read before proposing otherwise. |
| `docs/go-live-plan.md` | What is left before the whole fleet runs through this. |
| `docs/open-questions.md` | What is still unknown, and the command that answers it. |
