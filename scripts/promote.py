#!/usr/bin/env python3
"""Move one tab between two apps, with everything it depends on.

    python3 scripts/promote.py --from wfm-prod --to wfm-test --tab "Extruder abfrage"
    python3 scripts/promote.py --from wfm-test --to wfm-prod --tab "Extruder abfrage"

A test instance here is a workbench, not a copy of prod (decision 15). Work
starts by bringing a tab onto it and ends by shipping that tab back, so this
runs in both directions and the second direction is the interesting one.

Whether the source keeps the tab has to be said out loud, because both
mistakes are expensive and neither announces itself:

  --copy   for prod -> workbench, at the start of the work. Prod must keep
           running the tab while you change it, and the arriving copy is
           DISABLED: a tab that starts by itself on the workbench is a second
           publisher on the same topic, and the only symptom is data arriving
           twice. Enable it on the workbench when you want it to run.
  --move   for workbench -> prod, at the end. It arrives ENABLED, because prod
           is where it is meant to run, and the workbench loses it — leaving it
           behind is the same second publisher, just in the other direction.

A default either stops production or double-publishes, depending on which way
you were going, so there is none.

A tab alone is not self-contained, so the move takes its dependency closure:
the nodes on it, the subflows they instantiate, and the config nodes they
reference. Two things in the destination are never overwritten, because they
are what makes the two instances differ in the first place:

  config nodes   The workbench's broker is not prod's broker. An existing one
                 stays; a missing one is created from the source and reported,
                 because then a human has to set its values before deploying.
  subflows       A subflow in the destination may be used by other tabs there,
                 so replacing it would change flows nobody asked about.

Ids are preserved, so promoting the same tab again replaces it rather than
adding a second copy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import normalize, render  # noqa: E402


def app_file(app: str) -> Path:
    path = ROOT / "apps" / app / "flows.json"
    if not path.exists():
        raise SystemExit(f"no such app: apps/{app}/flows.json")
    return path


def find_tab(flows: list[dict], wanted: str) -> dict:
    tabs = [n for n in flows if n.get("type") == "tab"]
    hits = [t for t in tabs if t.get("label") == wanted or t["id"] == wanted]
    if not hits:
        raise SystemExit(f"no tab {wanted!r}. Present: "
                         + ", ".join(repr(t.get('label')) for t in tabs))
    if len(hits) > 1:
        raise SystemExit(f"{wanted!r} matches {len(hits)} tabs — use the id instead: "
                         + ", ".join(t["id"] for t in hits))
    return hits[0]


def globals_of(flows: list[dict]) -> dict[str, dict]:
    """Config nodes: no z, and not a tab or a subflow definition."""
    return {n["id"]: n for n in flows
            if "z" not in n and n.get("type") not in ("tab", "subflow")}


def closure(flows: list[dict], tab: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """The tab's nodes, the subflow definitions they use, the config nodes they name."""
    by_id = {n["id"]: n for n in flows}
    config = globals_of(flows)

    nodes, subflows, seen_z = [], [], set()

    def collect(z: str) -> None:
        if z in seen_z:
            return
        seen_z.add(z)
        for node in flows:
            if node.get("z") != z:
                continue
            nodes.append(node)
            # A subflow instance's type is "subflow:<definition id>".
            if str(node.get("type", "")).startswith("subflow:"):
                sid = node["type"].split(":", 1)[1]
                definition = by_id.get(sid)
                if definition is not None and definition not in subflows:
                    subflows.append(definition)
                    collect(sid)          # the definition's own nodes

    collect(tab["id"])

    # A config node is referenced by its id sitting in some property value.
    text = json.dumps(nodes)
    used = [config[cid] for cid in config if f'"{cid}"' in text]
    return nodes, subflows, used


def loose_links(nodes: list[dict]) -> list[str]:
    """link nodes pointing outside the closure — the tab is not self-contained."""
    inside = {n["id"] for n in nodes}
    loose = []
    for node in nodes:
        if str(node.get("type", "")).startswith("link "):
            for target in node.get("links", []):
                if target not in inside:
                    loose.append(f"{node.get('type')} {node.get('name') or node['id']} -> {target}")
    return loose


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="source", required=True)
    ap.add_argument("--to", dest="target", required=True)
    ap.add_argument("--tab", required=True, help="label or id")
    keep = ap.add_mutually_exclusive_group(required=True)
    keep.add_argument("--copy", action="store_true",
                      help="leave the tab in the source — for prod -> workbench")
    keep.add_argument("--move", action="store_true",
                      help="remove it from the source — for workbench -> prod")
    ap.add_argument("--dry-run", action="store_true", help="report and write nothing")
    args = ap.parse_args()

    if args.source == args.target:
        raise SystemExit("--from and --to are the same app")

    src_path, dst_path = app_file(args.source), app_file(args.target)
    source = json.loads(src_path.read_text(encoding="utf-8"))
    target = json.loads(dst_path.read_text(encoding="utf-8"))

    tab = find_tab(source, args.tab)
    nodes, subflows, config = closure(source, tab)
    have = {n["id"] for n in target}

    # The arriving state follows the direction, not the source: nothing starts
    # by itself on a workbench, and nothing arrives stopped in production.
    previous = next((n.get("disabled") for n in target
                     if n["id"] == tab["id"] and n.get("type") == "tab"), None)
    tab = dict(tab, disabled=bool(args.copy))

    created = [c for c in config if c["id"] not in have]
    kept_config = [c for c in config if c["id"] in have]
    kept_subflows = [s for s in subflows if s["id"] in have]
    new_subflows = [s for s in subflows if s["id"] not in have]

    print(f"{args.source} -> {args.target}: tab {tab.get('label')!r} ({tab['id']})")
    print(f"  {len(nodes)} node(s)"
          f"{', ' + str(len(subflows)) + ' subflow definition(s)' if subflows else ''}"
          f"{', ' + str(len(config)) + ' config node(s)' if config else ''}")
    if tab["id"] in have:
        print(f"  replaces the tab of the same id already in {args.target}")
    print(f"  arrives {'DISABLED' if tab['disabled'] else 'ENABLED'} in {args.target}"
          f"{' — enable it there when you want it to run' if tab['disabled'] else ''}")
    if previous is True and not tab["disabled"]:
        print(f"  NOTE {args.target} had this tab disabled and it arrives enabled — "
              f"confirm that is intended")
    for c in created:
        print(f"  CREATES config node {c.get('name') or c['id']!r} ({c.get('type')}) "
              f"with {args.source}'s values — set them for {args.target} before deploying")
    for c in kept_config:
        print(f"  keeps {args.target}'s own config node {c.get('name') or c['id']!r}")
    for s in kept_subflows:
        print(f"  keeps {args.target}'s own subflow {s.get('name') or s['id']!r} — "
              f"compare it by hand if the tab needs a newer one")
    for link in loose_links(nodes):
        print(f"  LOOSE LINK {link} — its partner stays behind, so the tab is not "
              f"self-contained in {args.target}")

    # The tab and its own nodes are replaced wholesale; shared things are not.
    moving_ids = {tab["id"]} | {n["id"] for n in nodes}
    rebuilt = [n for n in target
               if n["id"] not in moving_ids and n.get("z") != tab["id"]]
    rebuilt += [tab] + nodes + new_subflows + created

    if args.copy:
        remaining = source
        print(f"  --copy: stays in {args.source}, which keeps running it. Deploy "
              f"{args.target} when you are ready to work on it.")
    else:
        remaining = [n for n in source if n["id"] != tab["id"] and n.get("z") != tab["id"]]
        print(f"  --move: gone from {args.source}. Deploy {args.source} first so it "
              f"stops there, then {args.target} so it starts there.")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    dst_path.write_text(render(normalize(rebuilt)), encoding="utf-8")
    src_path.write_text(render(normalize(remaining)), encoding="utf-8")
    print(f"\nwrote apps/{args.target}/flows.json and apps/{args.source}/flows.json")
    print(f"  git diff apps/{args.target}/flows.json apps/{args.source}/flows.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
