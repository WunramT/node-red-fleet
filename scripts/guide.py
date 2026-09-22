#!/usr/bin/env python3
"""Guided walker over this project's day-to-day task loops.

    python3 scripts/guide.py                  # list the tasks
    python3 scripts/guide.py --list            # same, exits 0
    python3 scripts/guide.py --print change-tab   # render the steps, run nothing
    python3 scripts/guide.py change-tab            # walk it

Each task is a fixed sequence of steps: `ask` a question and store the answer
in a slot, `run` a command with slots filled in (only after the human
confirms it), or `do` something outside this script (a commit, a push, a
Jenkins run) and wait for Enter. Nothing here writes to an instance, and
nothing here runs a git write or a Jenkins call: those stay in the human's
hands as `do` steps.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import nodered  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

Step = tuple

ALLOWED_PROGRAMS = {"nr.py", "normalize.py", "git"}
SOURCES = {"prod", "test", "any", "text"}

TASKS: dict[str, dict] = {
    "change-tab": {
        "title": "Change a tab prod already runs",
        "when": "prod already runs this tab and you need to edit it",
        "steps": [
            ("ask", "prod", "Which prod instance is this tab already running on?", "prod"),
            ("ask", "test", "Which test instance is its workbench?", "test"),
            ("ask", "tab", "Name of the tab to change?", "text"),
            ("run", ["nr.py", "check", "{prod}"],
             "must be clean before anything is promoted off it"),
            ("run", ["nr.py", "promote", "{prod}", "{test}", "{tab}", "--copy"],
             "copies the tab onto the workbench; it arrives DISABLED"),
            ("do", 'commit and push: git commit -am "promote({test}): {tab} onto the workbench" && git push'),
            ("run", ["nr.py", "edit", "{test}"],
             "enable {tab}, change it, Deploy, then Ctrl-C"),
            ("run", ["normalize.py", "--write", "apps/{test_app}/flows.json"],
             "canonicalize so the diff reads cleanly"),
            ("run", ["git", "diff", "apps/{test_app}/flows.json"],
             "review what the editor wrote"),
            ("do", 'commit and push: git commit -am "flows({test}): {tab}" && git push'),
            ("do", "deploy {test} in Jenkins, try it, then ship it"),
            ("run", ["nr.py", "promote", "{test}", "{prod}", "{tab}", "--move"],
             "ships {tab} back to prod; left enabled on the workbench it would run twice"),
            ("do", 'commit and push: git commit -am "promote({prod}): ship {tab}" && git push'),
            ("do", "deploy {test} in Jenkins, then {prod}: that order keeps {tab} from "
                   "running in two places at once"),
        ],
    },
    "new-tab": {
        "title": "Add a new tab",
        "when": "the tab does not exist on prod yet",
        "steps": [
            ("ask", "test", "Which test instance is the workbench for this?", "test"),
            ("ask", "prod", "Which prod instance will run it once it ships?", "prod"),
            ("ask", "tab", "Name of the new tab?", "text"),
            ("run", ["nr.py", "edit", "{test}"],
             "add {tab}, Deploy, then Ctrl-C"),
            ("run", ["normalize.py", "--write", "apps/{test_app}/flows.json"],
             "canonicalize so the diff reads cleanly"),
            ("do", 'commit and push: git commit -am "flows({test}): add {tab}" && git push'),
            ("do", "deploy {test} in Jenkins, then try it"),
            ("run", ["nr.py", "promote", "{test}", "{prod}", "{tab}", "--move"],
             "ships {tab} to prod"),
            ("do", 'commit and push: git commit -am "promote({prod}): ship {tab}" && git push'),
            ("do", "deploy {test} in Jenkins, then {prod}: that order keeps {tab} from "
                   "running in two places at once"),
        ],
    },
    "fleet": {
        "title": "Fleet drift sweep",
        "when": "checking whether every instance still matches Git",
        "steps": [
            ("run", ["nr.py", "status"], "every instance at once, against Git"),
            ("ask", "inst", "Which instance showed drift in that table?", "any"),
            ("run", ["nr.py", "check", "{inst}"], "the diff for that one instance"),
        ],
    },
    "recover": {
        "title": "Recover from a 409",
        "when": "a promote or deploy came back 409: someone edited in the browser",
        "steps": [
            ("ask", "inst", "Which instance answered with the 409?", "any"),
            ("run", ["nr.py", "capture", "{inst}"],
             "reads the running flow back into apps/, recovering the edit instead of discarding it"),
            ("run", ["git", "diff", "apps/{inst_app}/flows.json"], "review what came back"),
            ("do", 'commit it, or discard it deliberately: '
                   'git commit -am "capture({inst}): recover browser edit" && git push'),
            ("do", "deploy {inst} again in Jenkins"),
        ],
    },
    "palette": {
        "title": "Add a palette module",
        "when": "a flow needs a node that is not in the image yet",
        "steps": [
            ("ask", "test", "Which test instance gets the new palette module?", "test"),
            ("run", ["nr.py", "edit", "{test}"],
             'install the module through "Manage palette"; the session carries it into '
             "apps/{test_app}/package.json for you"),
            ("run", ["git", "diff", "apps/{test_app}/package.json", "registry.yml"],
             "review the pinned version and the raised palette-build suffix the session wrote"),
            ("do", 'commit both: git commit -am "palette({test}): add <module>" && git push'),
            ("do", "Jenkins with DEPLOY_PALETTE=true, DRY_RUN=false: it deploys the flow "
                   "first, then recreates the container"),
        ],
    },
}


def fill(text: str, slots: dict[str, str]) -> str:
    """Substitute known {slot} placeholders in text, leaving the rest untouched."""
    for key, value in slots.items():
        text = text.replace("{" + key + "}", value)
    return text


def display_command(parts: list[str]) -> str:
    """The command as a human would type it, for printing only."""
    program, rest = parts[0], parts[1:]
    if program == "git":
        return " ".join(["git", *rest])
    return " ".join(["python3", f"scripts/{program}", *rest])


def to_argv(parts: list[str]) -> list[str]:
    """The real subprocess argv for a filled run step."""
    program, rest = parts[0], parts[1:]
    if program == "git":
        return ["git", *rest]
    return [PY, f"scripts/{program}", *rest]


def pool(source: str) -> list[tuple[str, str]]:
    """The instances an ask step offers: every one with an app, or one stage of them."""
    return [(i["name"], f"{i['host']:<16} app: {i['app']}")
            for i in nodered.instances()
            if i.get("app") and (source == "any" or i["name"].endswith(f"-{source}"))]


def choose(prompt: str, options: list[tuple[str, str]]) -> str:
    """Pick one option by number or by name. Also nr.py's menu picker."""
    print(f"\n{prompt}")
    for n, (key, label) in enumerate(options, 1):
        print(f"  {n:2}  {key:<12} {label}")
    while True:
        raw = input("\n> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        if raw in {k for k, _ in options}:
            return raw
        print("Pick a number from the list, or type the name.")


def print_steps(steps: list[Step], slots: dict[str, str] | None = None) -> None:
    """Render a task's steps, filling slots already known and leaving the rest as {placeholders}."""
    slots = slots or {}
    for n, step in enumerate(steps, 1):
        kind = step[0]
        if kind == "ask":
            _, slot, question, source = step
            derived = "" if source == "text" else f" and {{{slot}_app}}"
            print(f"  {n:2}. ask   {question} ({source} -> {{{slot}}}{derived})")
        elif kind == "run":
            _, parts, why = step
            filled = [fill(p, slots) for p in parts]
            print(f"  {n:2}. run   {display_command(filled)}")
            print(f"       {fill(why, slots)}")
        elif kind == "do":
            _, text = step
            print(f"  {n:2}. do    {fill(text, slots)}")


def show(key: str) -> dict:
    """Print a task's title and its whole step list. Returns the task."""
    if key not in TASKS:
        sys.exit(f"unknown task '{key}'. One of: {', '.join(TASKS)}")
    task = TASKS[key]
    print(f"{task['title']}\n{task['when']}\n")
    print_steps(task["steps"])
    return task


def walk(key: str) -> int:
    """Walk a task, confirming before every command."""
    task = show(key)

    slots: dict[str, str] = {}
    for step in task["steps"]:
        kind = step[0]
        if kind == "ask":
            _, slot, question, source = step
            if source == "text":
                slots[slot] = input(f"\n{question} ").strip()
            else:
                name = choose(question, pool(source))
                slots[slot] = name
                slots[f"{slot}_app"] = nodered.find(name)["app"]
        elif kind == "run":
            _, parts, why = step
            filled = [fill(p, slots) for p in parts]
            print(f"\n$ {display_command(filled)}")
            print(f"  {fill(why, slots)}")
            choice = input("[Enter] run · s skip · q quit ").strip().lower()
            if choice == "q":
                return 130
            if choice == "s":
                continue
            code = subprocess.run(to_argv(filled), cwd=ROOT).returncode
            print(f"exit {code}")
            if code != 0:
                if input("that failed. continue anyway? [y/N] ").strip().lower() != "y":
                    return code
        elif kind == "do":
            _, text = step
            input(f"\n{fill(text, slots)}\n[Enter] done ")
    return 0


def list_tasks() -> None:
    for key, task in TASKS.items():
        print(f"{key:<12} {task['title']}")


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--help"]:
        print(__doc__.strip())
        print()
    if not args or args[0] in ("--list", "--help"):
        list_tasks()
        return 0

    if args[0] == "--print":
        if len(args) < 2:
            sys.exit("usage: guide.py --print <task>")
        show(args[1])
        return 0

    return walk(args[0])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        # Ctrl-C and Ctrl-D both mean "not now", and neither is a crash.
        print()
        sys.exit(130)
