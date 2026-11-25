# SPDX-License-Identifier: Unlicense
"""Make overpressure's Matrix space match config/.

    python -m matrix_space --check            the settings only: every file valid, every icon there (no token)
    python -m matrix_space.announce           what to announce (releases, new headsets): see that module
    python -m matrix_space                    dry run: what differs on the server, nothing changed
    python -m matrix_space --apply            change it

It finds the rooms that already exist (by address, else by name among the space's rooms and the rooms the
account is in) and changes only what differs: name, topic, icon, address, join rule, history, guest access,
power levels and the people's levels, the space's order of rooms, and the pinned texts. A room it can't find
is created. Running it again changes nothing once everything matches.

The access token is read from MATRIX_ACCESS_TOKEN (the workflow's secret) and never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from matrix_space.api import Api, MatrixError
from matrix_space.config import ConfigError, load, load_announcements
from matrix_space.icons import Icons
from matrix_space.plan import Change, Options, PlanError, Planner

#: The settings, next to this package.
CONFIG = Path(__file__).resolve().parent.parent / "config"
#: Exit codes besides 0: a failure, no token, and a dry run that found differences (--fail-on-drift).
FAILED, NO_TOKEN, DRIFT = 1, 2, 3


def parse(argv: list[str] | None) -> argparse.Namespace:
    """Return the command line's options."""
    parser = argparse.ArgumentParser(prog="python -m matrix_space", description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: only list them)")
    parser.add_argument("--check", action="store_true", help="only check the settings and the icons; needs no token")
    parser.add_argument("--config", type=Path, default=CONFIG, metavar="DIR", help="the settings (default: config/)")
    parser.add_argument("--icons", type=Path, metavar="DIR", help="read the icons from DIR, a brand checkout's matrix/")
    parser.add_argument("--keep-names", action="store_true", help="leave the rooms' and the space's names alone")
    parser.add_argument("--skip-icons", action="store_true", help="leave the rooms' and the space's icons alone")
    parser.add_argument(
        "--directory", action="store_true", help="also the server's public room directory (matrix.org refuses most)"
    )
    parser.add_argument(
        "--room", action="append", default=[], metavar="KEY=ID", help="which existing room a room of the settings is"
    )
    parser.add_argument(
        "--create", action="store_true", help="create the rooms it can't find (default: list them as notes)"
    )
    parser.add_argument("--only-icons", action="store_true", help="only the icons: list or change nothing else")
    parser.add_argument("--fail-on-drift", action="store_true", help="a dry run that finds differences exits with 3")
    parser.add_argument("--summary", type=Path, metavar="FILE", help="also write the result as Markdown to FILE")
    return parser.parse_args(argv)


def check(config_dir: Path, icons_dir: Path | None) -> int:
    """Check the settings and that every icon they name exists."""
    try:
        config = load(config_dir)
        load_announcements(config_dir, config)
        icons = Icons(config.icons, icons_dir)
        missing = [
            icons.where(i) for i in [config.space.icon, *(r.icon for r in config.rooms)] if i and icons.get(i) is None
        ]
    except (ConfigError, MatrixError) as e:
        print(f"error: {e}", file=sys.stderr)
        return FAILED
    for where in missing:
        print(f"error: icon {where} doesn't exist", file=sys.stderr)
    print(
        f"{len(config.rooms)} rooms, the space and the announcements: settings valid, "
        f"{'icons missing' if missing else 'icons there'}"
    )
    return FAILED if missing else 0


def summary(changes: list[Change], *, applied: bool) -> str:
    """Return the changes as Markdown, for the workflow's summary."""
    if not changes:
        return "Everything matches the settings.\n"
    head = "Changed" if applied else "Differs (dry run: nothing changed)"
    lines = [f"### {head}", "", "| Room | Change |", "| --- | --- |"]
    lines += [f"| {c.room} | {'' if c.run else 'note: '}{c.what.replace('|', '/')} |" for c in changes]
    return "\n".join(lines) + "\n"


def run(changes: list[Change]) -> int:
    """Make the changes; one refused doesn't stop the others. Return how many failed."""
    failed = 0
    for change in changes:
        if change.run is None:
            continue
        try:
            change.run()
        except MatrixError as e:
            failed += 1
            print(f"FAILED: {change.room}: {change.what[:60]}: {e}")
        else:
            print(f"done: {change.room}: {change.what[:80]}")
    return failed


def main(argv: list[str] | None = None) -> int:
    """Check, list or make the changes."""
    args = parse(argv)
    # Room names may hold emoji, which a Windows console's code page can't print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # ty: ignore[unresolved-attribute]  # a TextIOWrapper
    if args.check:
        return check(args.config, args.icons)
    token = os.environ.get("MATRIX_ACCESS_TOKEN", "").strip()
    if not token:
        print("MATRIX_ACCESS_TOKEN isn't set", file=sys.stderr)
        return NO_TOKEN
    try:
        config = load(args.config)
        api = Api(os.environ.get("MATRIX_HOMESERVER", config.homeserver), token)
        me = api.whoami()
        print(f"signed in as {me}")
        options = Options(
            keep_names=args.keep_names,
            skip_icons=args.skip_icons,
            directory=args.directory,
            rooms=dict(item.split("=", 1) for item in args.room),
            create=args.create,
            only_icons=args.only_icons,
        )
        icons = Icons(config.icons, args.icons)
        changes = Planner(api, config, icons, options, me).plan()
        for change in changes:
            print(f"{'  ' if change.run else '! '}{change.room}: {change.what}")
        if not changes:
            print("everything matches the settings")
        failed = run(changes) if args.apply else 0
        if args.apply and not failed and any(c.kind == "create" and c.run for c in changes):
            print("\nconfiguring the new room(s):")
            more = Planner(api, config, icons, options, me).plan()
            failed = run(more)
            changes += more
    except (ConfigError, MatrixError, PlanError) as e:
        print(f"error: {e}", file=sys.stderr)
        return FAILED
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(summary(changes, applied=args.apply))
    pending = sum(1 for c in changes if c.run)
    if failed:
        print(f"\n{failed} change(s) failed; the others are done. The next run retries only what differs.")
        return FAILED
    if not args.apply and pending:
        print(f"\n{pending} change(s); nothing changed (dry run). --apply makes them.")
        return DRIFT if args.fail_on_drift else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
