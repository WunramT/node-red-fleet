# slu-test holds no flow yet: slu-svr-lin02's two instances are running and empty,
# and what they are for is not decided (docs/open-questions.md, question 4). The
# app exists so the image exists — a flow can then be deployed the day someone
# builds one, without a build standing between the decision and the deploy.
#
# node-red 4.0.9 is the version the instance already runs, so the pin changes
# nothing but the drift. No palette: an empty flow needs none, and a module is
# added when a node needs it (runbook, "Palette change").
FROM docker.io/nodered/node-red:4.0.9

# The palette for this app is apps/slu-test/package.json, the one place a palette
# version is edited (runbook, "Palette change"). It is read here rather than
# copied over /usr/src/node-red/package.json, because that file declares
# node-red itself: replacing it makes npm prune the runtime out of node_modules
# and the container dies on
#     Cannot find module '/usr/src/node-red/node_modules/node-red/red.js'
#
# The modules land in the application directory because /data is bind-mounted at
# runtime — a package.json under /data would be shadowed by the mount.
#
# The path is repo-root relative because the buildah component builds with the
# repository as the context and passes only --file. Build it by hand the same
# way: docker build -f apps/slu-test/Dockerfile .
COPY --chown=node-red:node-red apps/slu-test/package.json /tmp/palette.json
RUN set -eu; \
    modules="$(node -p 'Object.entries(require("/tmp/palette.json").dependencies||{}).map(([n,v])=>n+"@"+v).join(" ")')"; \
    if [ -n "$modules" ]; then \
        npm install --no-audit --no-fund --no-update-notifier --omit=dev $modules; \
    else \
        echo "no palette modules for this app"; \
    fi; \
    rm /tmp/palette.json
