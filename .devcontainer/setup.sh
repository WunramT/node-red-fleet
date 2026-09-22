#!/usr/bin/env bash
# Everything a fresh container needs before someone types the first command.
# Idempotent: it runs once on create, and by hand whenever something looks off.
set -euo pipefail
cd "$(dirname "$0")/.."

# The import names, because the package names are not them.
if python3 -c "import yaml, jsonschema, paramiko" 2>/dev/null; then
    echo "  ok   dependencies already present"
else
    pip install --user --quiet --disable-pip-version-check -r scripts/requirements.txt
    echo "  ok   dependencies installed"
fi

if [ ! -f nr.local.json ]; then
    cp nr.local.example.json nr.local.json
    echo "  new  nr.local.json, from the example. Gitignored."
    echo "       Fill in the URLs before check, capture or deploy."
    echo "       edit and promote work without it."
fi

python3 scripts/validate-registry.py
for suite in scripts/test_*.py; do
    python3 "$suite" >/dev/null && echo "  ok   $(basename "$suite" .py)"
done
