# The workbench for dpn-prod, so it carries prod's version and prod's palette:
# a tab promoted down has to open with its nodes, not as "unknown". Both move
# together — bump-node-red.py and a palette change take the pair.
FROM docker.io/nodered/node-red:4.0.9

# The palette for this app is apps/dpn-test/package.json, the one place a palette
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
# way: docker build -f apps/dpn-test/Dockerfile .
COPY --chown=node-red:node-red apps/dpn-test/package.json /tmp/palette.json
RUN set -eu; \
    modules="$(node -p 'Object.entries(require("/tmp/palette.json").dependencies||{}).map(([n,v])=>n+"@"+v).join(" ")')"; \
    if [ -n "$modules" ]; then \
        npm install --no-audit --no-fund --no-update-notifier --omit=dev $modules; \
    else \
        echo "no palette modules for this app"; \
    fi; \
    rm /tmp/palette.json
