#!/usr/bin/env python3
"""Render a drift sweep into one static HTML page.

    python3 scripts/render-drift.py inventory/drift.json -o public/index.html

The page is the visibility layer decision 11 settled on: it shows state and
offers no way to change it. No deploy button, no form, no JavaScript — a table
anyone can read, generated from the sweep's own JSON plus what registry.yml
already says.

Standard library only, like everything the pipeline ships, and it never opens a
network connection: the JSON is the whole input.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import sys
from pathlib import Path

# The states drift-check reports, in the order a reader wants them: what is
# wrong first, what is fine last.
ORDER = {"drifted": 0, "unreachable": 1, "no-app": 2, "clean": 3}

STYLE = """
  :root { color-scheme: light dark; --line:#d5d8dd; --muted:#6b7280;
          --drifted:#b45309; --unreachable:#b91c1c; --clean:#15803d; --bg:#fff; --fg:#111 }
  @media (prefers-color-scheme: dark) {
    :root { --line:#333a45; --muted:#9aa3af; --bg:#14171c; --fg:#e8eaed;
            --drifted:#fbbf24; --unreachable:#f87171; --clean:#4ade80 }
  }
  body { background:var(--bg); color:var(--fg); margin:0 auto; max-width:60rem; padding:2rem 1rem;
         font:16px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif }
  h1 { font-size:1.4rem; margin:0 0 .25rem }
  .sub { color:var(--muted); margin:0 0 1.5rem }
  table { border-collapse:collapse; width:100% }
  th, td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid var(--line);
           vertical-align:top }
  th { font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
       border-bottom-width:2px }
  td.state { font-weight:600; white-space:nowrap }
  .drifted { color:var(--drifted) } .unreachable { color:var(--unreachable) }
  .clean { color:var(--clean) } .no-app { color:var(--muted) }
  td.detail, td.tag { color:var(--muted); font-family:ui-monospace, monospace; font-size:.85rem;
                      word-break:break-all }
  .tally { margin:1.5rem 0 0; color:var(--muted); font-size:.9rem }
  footer { margin-top:2rem; color:var(--muted); font-size:.85rem; border-top:1px solid var(--line);
           padding-top:1rem }
"""


def detail(row: dict) -> str:
    """One line per instance, saying the thing a reader would ask next."""
    state = row.get("state")
    if state == "clean":
        return f"{row.get('nodes', '?')} nodes"
    if state == "drifted":
        return (f"{row.get('changed_lines', '?')} changed lines — "
                f"{row.get('nodes_running', '?')} running, {row.get('nodes_git', '?')} in Git")
    if state == "unreachable":
        return row.get("detail", "")
    return "no flow of its own"


def render(rows: list[dict], generated: str) -> str:
    rows = sorted(rows, key=lambda r: (ORDER.get(r.get("state"), 9), r.get("instance", "")))
    tally = {s: sum(1 for r in rows if r.get("state") == s) for s in ORDER}

    body = []
    for r in rows:
        state = r.get("state", "?")
        body.append(
            "      <tr>"
            f"<td>{html.escape(str(r.get('instance', '')))}</td>"
            f"<td>{html.escape(str(r.get('host', '')))}</td>"
            f'<td class="state {html.escape(state)}">{html.escape(state)}</td>'
            f'<td class="detail">{html.escape(detail(r))}</td>'
            f'<td class="tag">{html.escape(str(r.get("image_tag") or "").rsplit("/", 1)[-1])}</td>'
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Node-RED fleet — drift</title>
<style>{STYLE}</style>
</head>
<body>
  <h1>Node-RED fleet — drift</h1>
  <p class="sub">Does each instance still run what Git says? Swept {html.escape(generated)}.</p>
  <table>
    <thead>
      <tr><th>Instance</th><th>Host</th><th>State</th><th>Detail</th><th>Image</th></tr>
    </thead>
    <tbody>
{chr(10).join(body)}
    </tbody>
  </table>
  <p class="tally">{tally['clean']} clean · {tally['drifted']} drifted ·
     {tally['unreachable']} unreachable · {tally['no-app']} without an app</p>
  <footer>
    Read-only by design: drift is information, not a failure to repair. A browser edit
    is captured with <code>nr.py capture &lt;instance&gt;</code> and committed; deploys go
    through the pipeline, where they are reviewed and recorded.
  </footer>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("json", type=Path, help="the sweep's output, from drift-check.py --json")
    ap.add_argument("-o", "--out", type=Path, default=Path("public/index.html"))
    args = ap.parse_args()

    rows = json.loads(args.json.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"{args.json}: expected a list of instances, got {type(rows).__name__}")

    generated = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(rows, generated), encoding="utf-8")
    print(f"wrote {args.out} — {len(rows)} instances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
