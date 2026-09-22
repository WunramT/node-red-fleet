#!/usr/bin/env python3
"""Which tabs of a flow can move on their own, and which have to move together.

    python3 scripts/cutover-plan.py apps/pod-prod/flows.json

Moving an instance tab by tab — enable it on the new runtime, disable it on the
old one — is the safe way to cut over, because the risk is one tab instead of a
whole instance. It only works where the tabs are actually independent.

`link in` / `link out` nodes are the thing that breaks. They pass messages
in-process, so a link that crosses a tab boundary stops delivering the moment
its two ends run in different runtimes, and nothing reports it: the sending side
still fires. Tabs joined by a link therefore move as one group.

MQTT, NATS and HTTP do not create such a group — those go through a broker or a
socket and work across runtimes. Shared config nodes do not either, but they are
listed, because a device that accepts one connection at a time (Modbus, serial,
a machine's TCP port) is contended while both runtimes hold it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promote import closure, globals_of  # noqa: E402


def tabs_of(flows: list[dict]) -> list[dict]:
    return [n for n in flows if n.get("type") == "tab"]


def link_edges(flows: list[dict]) -> set[tuple[str, str]]:
    """Tab pairs joined by a link node, as ids."""
    home = {n["id"]: n.get("z") for n in flows if "z" in n}
    edges = set()
    for node in flows:
        if not str(node.get("type", "")).startswith("link "):
            continue
        here = node.get("z")
        for target in node.get("links", []):
            there = home.get(target)
            if there and there != here:
                edges.add((min(here, there), max(here, there)))
    return edges


def groups(flows: list[dict]) -> list[list[dict]]:
    """Tabs that must move together, largest group first."""
    tabs = tabs_of(flows)
    parent = {t["id"]: t["id"] for t in tabs}

    def root(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    for a, b in link_edges(flows):
        if a in parent and b in parent:
            parent[root(a)] = root(b)

    buckets: dict[str, list[dict]] = {}
    for tab in tabs:
        buckets.setdefault(root(tab["id"]), []).append(tab)
    return sorted(buckets.values(), key=len)


def report(flows: list[dict]) -> str:
    tabs = tabs_of(flows)
    found = groups(flows)
    config = globals_of(flows)
    lines = [f"{len(tabs)} tab(s) in {len(found)} independently movable group(s)",
             "" if found else "nothing to move"]

    used_by: dict[str, set[int]] = {}
    for number, group in enumerate(found, 1):
        counts, config_here = [], set()
        for tab in group:
            nodes, _, cfg = closure(flows, tab)
            counts.append((tab.get("label") or tab["id"], len(nodes)))
            config_here |= {c["id"] for c in cfg}
        for cid in config_here:
            used_by.setdefault(cid, set()).add(number)
        total = sum(n for _, n in counts)
        together = " — move as one" if len(group) > 1 else ""
        lines.append(f"Group {number}{together}  ({total} nodes)")
        for label, count in sorted(counts, key=lambda c: -c[1]):
            lines.append(f"    {label}  ({count} nodes)")
        lines.append("")

    shared = {cid: sorted(where) for cid, where in used_by.items() if len(where) > 1}
    if shared:
        lines.append("Config nodes used by more than one group. These are fine across")
        lines.append("runtimes unless the thing behind them takes one connection at a")
        lines.append("time — a Modbus device, a serial port, a machine's TCP port:")
        for cid, where in sorted(shared.items(), key=lambda s: s[1]):
            node = config[cid]
            name = node.get("name") or cid
            lines.append(f"    {node.get('type')}  {name}  -> groups {', '.join(map(str, where))}")
    else:
        lines.append("No config node is shared between groups.")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__.split("\n\n")[1].strip())
    path = Path(sys.argv[1])
    flows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(flows, list):
        sys.exit(f"{path}: a flows.json is a JSON array of nodes")
    print(report(flows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
