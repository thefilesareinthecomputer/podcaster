#!/usr/bin/env python3
"""The podgen command: one entry point for writing, rendering, the combined run, and shows.

    podgen write --source notes/ --episode A     # print what to write and where, then stop
    podgen render --script ~/podcasts/default/scripts/2026-09-17-A.md --engine apple
    podgen run --source notes/ --episode A       # locate the script, gate it, render it
    podgen init ~/podcasts/history               # scaffold a show folder from the template
    podgen shows ~/podcasts                      # list show folders under a parent

A show is a folder holding config.toml, in/, prompts/, voices/, scripts/ and out/. Paths
inside a show's config resolve against that folder, so one install drives many podcasts.
Without --show the show is $PODGEN_SHOW, else ~/podcasts/default, created on first use.

write, render and run take the flags of the command they wrap; `podgen render --help` prints
render's own help.

Exit codes: 0 ok, 1 bad script or bad config, 2 engine unavailable on this machine, 3 slop
gate failed, 4 no usable source, 5 script missing (write it), 6 rubric gate failed,
7 grounding gate failed, 8 not enough free memory for this render, 9 this engine has no
recorded successful render yet, 10 the mode cannot run, 11 another run holds the machine
lock, 12 structure gate failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from podcaster import pipeline, render
from podcaster.paths import PROMPT_FILES, init_show  # noqa: F401  (part of the CLI's surface)

PASS_THROUGH = {
    "write": "print the brief, the sources, the target path and the word budget, then stop",
    "render": "render a script you already have to wav and mp3",
    "run": "locate the day's script, gate it and render it in one command",
}


def list_shows(parent: Path) -> list[Path]:
    return sorted(p.parent for p in parent.glob("*/config.toml"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="podgen",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command", metavar="{write,render,run,init,shows}")
    for name, help_text in PASS_THROUGH.items():
        # add_help=False and no arguments of its own: everything after the subcommand is
        # handed to the wrapped command untouched, including --help, so `podgen render --help`
        # prints render's own help rather than a copy of its flags maintained here.
        sub.add_parser(name, help=help_text, add_help=False)
    init = sub.add_parser("init", help="scaffold a show folder")
    init.add_argument("folder", type=Path, help="the show folder to create")
    shows = sub.add_parser("shows", help="list show folders under a parent")
    shows.add_argument("parent", type=Path, nargs="?", default=None, help="default: the current folder")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args, rest = ap.parse_known_args(argv)
    if not args.command:
        ap.print_help()
        return 2
    if args.command == "render":
        return render.main(rest)
    if args.command == "write":
        return pipeline.main(rest + ["--write-only"])
    if args.command == "run":
        return pipeline.main(rest)
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.command == "init":
        for line in init_show(args.folder.expanduser()):
            print(line)
        return 0
    parent = args.parent.expanduser() if args.parent else Path.cwd()
    for show in list_shows(parent):
        print(show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
