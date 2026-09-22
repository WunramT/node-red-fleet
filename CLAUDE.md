# CLAUDE.md

Node-RED multi-instance deployment, **live**. Git holds the flows, CI deploys them, and every one of the 18 instances across 10 servers runs that way — including the two that came off FlowFuse in September 2026. No two instances share a flow: every one is its own application.

Start with [`README.md`](README.md), which is the working cheatsheet. Then, for
anything non-obvious:

- [`docs/architecture.md`](docs/architecture.md) is the system and the measured facts about the estate.
- [`docs/decisions.md`](docs/decisions.md) holds closed decisions. Read it before proposing a different approach.
- [`docs/open-questions.md`](docs/open-questions.md) names what is unknown and the command that answers it.
- [`docs/registry.md`](docs/registry.md) is the `registry.yml` field reference.
- [`docs/betrieb.md`](docs/betrieb.md) is what can be done with the running system, in German — the day-to-day view, and the procedure for taking on a new instance.
- [`docs/runbook.md`](docs/runbook.md) is how it is operated.

## Constraints

These hold across every task in this repo. Each traces to a decision in `docs/decisions.md`.

1. **A `409` from `POST /flows` fails the pipeline.** Someone edited in the browser; the recovery is to capture that edit, not to overwrite it. There is no `--force` path — not a flag, not a fallback, not a prompt.
2. **A committed `flows.json` opens in the Node-RED editor unchanged.** Rendering happens in CI, on a copy. This is what keeps the editor→Git return path alive.
3. **Every image reference is an exact tag.** `latest` and other floating tags fail validation.
4. **Secrets come from Jenkins credentials, and stay there.** Report that a `credentialSecret` exists; never its value. This holds for repo content, pipeline logs and tool output alike.
5. **Compose calls name their service** — `docker compose up -d node-red-prod`. The shared `base_container/docker-compose.yml` also holds NATS.

## Working here

Flow deploys are daily and must not restart a container. Palette deploys are
rare and may. Any design that restarts a container to change flow logic is the
wrong design.

`scripts/nodered.py` is the shared library: the instance list, where an
instance answers, the Admin API, image tags. Jenkins ships it and `deploy.py`
to the target host, so both are standard library only. Everything that needs to
know an instance goes through it rather than reading `registry.yml` again.

`scripts/scaffold-apps.py` built `apps/` once from `samples/` and now leaves
existing apps alone. `samples/` is the capture from before the project and does
not move.
