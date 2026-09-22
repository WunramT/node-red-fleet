#!/usr/bin/env python3
"""Canonicalize a Node-RED flows.json so that Git diffs are reviewable.

    python3 scripts/normalize.py --check apps/*/flows.json     # CI: fail if not canonical
    python3 scripts/normalize.py --write apps/*/flows.json     # rewrite in place
    python3 scripts/normalize.py flows.json                    # print to stdout

What it does, and only this:

  * orders the nodes deterministically — tabs and subflows first in their
    existing relative order, then every other node grouped under its tab and
    sorted by id within the tab
  * gives every node the same key order
  * formats with a 2-space indent and a trailing newline

What it deliberately does NOT do is drop keys. An earlier spec called for
stripping `x`, `y` and `z` as "positional". `x` and `y` are positions; **`z` is
the id of the tab or subflow the node belongs to**, and dropping it detaches
every node from its tab. `w` and `h` size a group node, and `g` is group
membership — also structure, not decoration.

`x` and `y` stay too, for a different reason: a committed flow has to open in
the editor unchanged (decision 6), and a 205-node flow whose nodes all sit at
the origin does not. Node moves produce two changed lines each, which is a
small price for keeping the editor→Git return path usable.

The reordering is what removes the real noise: Node-RED rewrites flows.json on
every deploy and does not preserve array order, so an unnormalized file diffs
against itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Read first, in this order; everything else follows alphabetically, with the
# wiring last because it is the longest and the least read.
KEY_ORDER = ["id", "type", "z", "g", "name", "label", "info", "disabled", "env"]
KEY_LAST = ["wires"]

# Nodes that define structure rather than live in it. Their relative order is
# meaningful — the array order of `tab` nodes is the tab order in the editor,
# and there is no other field carrying it — so it is preserved, not sorted.
STRUCTURAL = ("tab", "subflow")


def order_keys(node: dict) -> dict:
    head = [k for k in KEY_ORDER if k in node]
    tail = [k for k in KEY_LAST if k in node]
    middle = sorted(k for k in node if k not in head and k not in tail)
    return {k: node[k] for k in (*head, *middle, *tail)}


def normalize(nodes: list) -> list:
    if not isinstance(nodes, list):
        raise ValueError("a flows.json is a JSON array of nodes")

    structural = [n for n in nodes if isinstance(n, dict) and n.get("type") in STRUCTURAL]
    rest = [n for n in nodes if not (isinstance(n, dict) and n.get("type") in STRUCTURAL)]

    # Tabs keep their order; every node then follows the tab it belongs to.
    # A node whose z names no tab — a config node, typically — sorts after
    # the tabs it cannot be grouped under, in a stable place of its own.
    tab_rank = {n.get("id"): i for i, n in enumerate(structural)}
    unplaced = len(tab_rank)

    def key(node):
        if not isinstance(node, dict):
            return (unplaced + 1, "", "")
        z = node.get("z")
        return (tab_rank.get(z, unplaced), str(z or ""), str(node.get("id", "")))

    ordered = structural + sorted(rest, key=key)
    return [order_keys(n) if isinstance(n, dict) else n for n in ordered]


def render(nodes: list) -> str:
    return json.dumps(nodes, indent=2, ensure_ascii=False) + "\n"


def normalize_text(text: str) -> str:
    return render(normalize(json.loads(text)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="exit 1 if any file is not already canonical; change nothing")
    mode.add_argument("--write", action="store_true",
                      help="rewrite each file in place")
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args()

    failed = []
    for path in args.files:
        try:
            original = path.read_text(encoding="utf-8")
            result = normalize_text(original)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failed.append(path)
            continue

        if args.check:
            if result != original:
                print(f"{path}: not normalized", file=sys.stderr)
                failed.append(path)
        elif args.write:
            if result != original:
                path.write_text(result, encoding="utf-8")
                print(f"{path}: normalized")
        else:
            sys.stdout.write(result)

    if failed:
        if args.check:
            print(f"\n{len(failed)} file(s) need normalizing — run:\n"
                  f"  python3 scripts/normalize.py --write {' '.join(str(f) for f in failed)}",
                  file=sys.stderr)
        return 1
    if args.check:
        print(f"{len(args.files)} file(s) normalized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
