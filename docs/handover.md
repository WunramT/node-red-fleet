# Node-RED fleet — what it does and how to run it

The page to read first, and the one to copy into Confluence. Everything below is
true of the system as it runs today; the detail lives in this repository, next
to the code it describes, and is linked rather than repeated.

## What this is

18 Node-RED instances across 10 servers, each one its own application — no two
share a flow. **Git holds the flows, CI builds the images, Jenkins deploys.**
Nothing is edited on a production instance by hand, and every change is a commit
somebody can read, review and revert.

| | |
|---|---|
| Instances | 18, of which 16 carry a flow (`slu-prod` and `slu-test` are empty but ready) |
| Servers | 10 |
| Source of truth | `apps/<app>/flows.json`, normalized, opens unchanged in the editor |
| Images | Harbor, `dap-node-red/<app>:<node-red-version>-<palette build>`, exact tags only |
| What runs where | `registry.yml` — every tool reads it, nothing hard-codes an instance |
| Repository | `node-red-fleet` |
| Pipelines | `Jenkinsfile` (deploy) and `Jenkinsfile.drift` (daily check), `.gitlab-ci.yml` (validate, build, sign) |

## Two transports, and what each one costs

This is the distinction the whole design rests on:

| What changes | How it travels | What stops |
|---|---|---|
| **Flow logic** | Admin API, `POST /flows` | only the tabs whose content changed. Same container, same uptime, same connections |
| **Palette** (npm modules) | rebuilt image, `docker compose up -d <service>` | the whole container, for as long as a restart takes |
| **`settings.js`** | by hand on the host | the whole container. The pipeline never touches this file |

Flow deploys are daily and must not interrupt ingest. Palette deploys are rare,
so a restart is acceptable there. **Any design that restarts a container to
change flow logic is the wrong design.**

## What you can do with it

| Task | How | Interruption |
|---|---|---|
| See whether an instance still matches Git | `nr.py check <inst>`, all of them: `nr.py status` | none, read-only |
| Bring a browser edit back into Git | `nr.py capture <inst>`, then commit | none |
| Change a tab production already runs | `promote --copy` to the workbench, `nr.py edit`, `promote --move` back | that tab only |
| Build a new tab | same loop without the first promotion | that tab only |
| Deploy a flow | Jenkins, two runs (below) | changed tabs only |
| Add a palette module | `apps/<app>/package.json`, raise the tag's build suffix, `DEPLOY_PALETTE=true` | container restart |
| Move to another Node-RED version | `bump-node-red.py --to <version> --instance <inst>` | container restart |
| See which tabs must move together | `cutover-plan.py apps/<app>/flows.json` | none |
| Find plaintext secrets in a flow | `secrets-to-env.py apps/<app>/flows.json` | none |
| Open a local editor on a copy | `nr.py edit <inst>`, `--baked` for palette nodes | none, the instance is untouched |
| Take on a new instance | seven steps, in `runbook.md` | one restart |

Not sure which of those you are doing? `python3 scripts/nr.py` asks, then walks
you through it and shows every command before running it.

## The two change loops

**A tab production already runs.** The workbench gets a copy, the change is made
there, and the finished tab moves back:

```bash
python3 scripts/nr.py check wfm-prod                              # must be clean
python3 scripts/nr.py promote wfm-prod wfm-test "Flow 1" --copy   # arrives DISABLED
git commit -am "promote(wfm-test): Flow 1 onto the workbench" && git push

python3 scripts/nr.py edit wfm-test        # enable that one tab, change it, Deploy, Ctrl-C
python3 scripts/normalize.py --write apps/wfm-test/flows.json
git diff apps/wfm-test/flows.json
git commit -am "flows(wfm-test): ..." && git push
# deploy wfm-test, try it on the instance

python3 scripts/nr.py promote wfm-test wfm-prod "Flow 1" --move
git commit -am "promote(wfm-prod): ship Flow 1" && git push
# deploy wfm-test FIRST, then wfm-prod
```

**A new tab** is the same loop without the first promotion.

That deploy order is not cosmetic: `--move` takes the tab off the workbench, so
deploying the workbench first is what keeps one tab from running in two places.

## Deploying

Jenkins writes. Two runs, and the second is pinned to what the first showed you:

| | `INSTANCE` | `DRY_RUN` | `EXPECT_REV` | `DEPLOY_PALETTE` |
|---|---|---|---|---|
| look | the instance | `true` | empty | `false` |
| write | the instance | `false` | the `rev` the dry run printed | `false` |

Without `EXPECT_REV` the deploy overwrites whatever it finds, and says so. With
it, anything that changed the instance in between stops the write.

## Watching it

`Jenkinsfile.drift` runs every morning: one SSH session per host, `drift-check`
on each, the results merged into one `drift.json` and rendered into a static
page. Both are kept as build artifacts, and the result is POSTed to a Node-RED
endpoint where one flow decides what to do with it — today, a message when the
picture changes.

The job writes nothing that outlives it: no file on any host, no change to any
instance. Drift does not make it red, because drift is somebody's browser edit
waiting to be captured, not a failure. A host it could not reach does.

## Rules that bite

- **A `409` is not a failure to route around.** It means someone edited in the
  browser. `nr.py capture <inst>`, commit, deploy again. **There is no
  `--force`** — not a flag, not a fallback, not a prompt.
- **The local editor opens every tab disabled.** Enable the one you work on:
  what you enable runs for real, against real systems.
- **`--copy` down, `--move` up.** A tab left enabled on the workbench publishes
  alongside production, and the only symptom is data arriving twice.
- **Image tags are exact.** `latest` fails validation. A palette change raises
  the build suffix, and CI refuses a palette change without it.
- **Secrets come from Jenkins credentials and the host's environment**, never
  from the repository — not in a commit, not in a log, not in a chat.
- **Name the compose service**: `docker compose up -d node-red-prod`. Those
  files hold other teams' services too.
- **Some flows read their database password from the environment.** The variable
  has to be in the compose service *before* the flow is deployed, or Node-RED
  connects with an empty password and the deploy still looks successful.

## Where things live

| | |
|---|---|
| `registry.yml` | what runs where — the only place that knows |
| `apps/<app>/` | `flows.json` as deployed, `package.json` as the palette, its `Dockerfile` |
| `scripts/` | the tools; `nr.py` is the front door |
| `Jenkinsfile` | deploy, one instance at a time |
| `Jenkinsfile.drift` | the daily check |
| `.gitlab-ci.yml` | validation, image build, signing |

## What you need access to

| | For |
|---|---|
| Jenkins credentials `<host>_pw` | the SSH login per server, 9 of them |
| `nodered-<instance>-auth` | that instance's Admin API login, one per instance |
| `nodered-<instance>-credsecret` | that instance's `credentialSecret`, one per instance |
| Harbor | pulling the images on the hosts |
| The repository | everything else |

`slu-prod` and `slu-test` still owe their four credential ids; a fleet run
reaches them now that they carry an app.

## What is still open

Three things, none of which stop anything running — the current list is in
[`open-questions.md`](open-questions.md):

1. **Eight database passwords are switched to environment variables but not
   rotated.** The values are out of the flows and in the hosts, but the old ones
   are still live.
2. **The drift page has no URL.** It is a build artifact per run, so reading it
   means opening Jenkins. Giving it an address is deliberately not done by
   writing onto a host.
3. **The palette path has never run end to end.** Flow deploys are proven daily;
   the image rebuild plus restart has not been exercised once.

## The rest of the documentation

In the repository, because it has to change when the code does:

| | |
|---|---|
| [`../README.md`](../README.md) | the working cheatsheet, the two loops, every command |
| [`runbook.md`](runbook.md) | the procedures: backup gate, deploys, `409` recovery, moving an instance, upgrades, the drift job |
| [`architecture.md`](architecture.md) | the system and the measured facts about the estate |
| [`decisions.md`](decisions.md) | why it is built this way. Read before proposing otherwise |
| [`registry.md`](registry.md) | the `registry.yml` field reference |
| [`open-questions.md`](open-questions.md) | what is unknown, and the command that answers it |
