# SPDX-License-Identifier: Unlicense
"""Post overpressure's releases and new headsets to the announcements room, each once.

    python -m matrix_space.announce            dry run: what would be posted
    python -m matrix_space.announce --apply    post it

What it announces is config/announcements.yaml: the releases of the repositories it names and of every
headset's repository, and each new headset (a repository of the organization with a headset.json), none from
before its "since". Every post carries a mark (org.overpressure.announce) naming what it announced; the
room's latest messages are read for those marks, so nothing is posted twice and no state is kept elsewhere.
At most max_per_run posts go out per run, oldest first.

MATRIX_ACCESS_TOKEN is the bot's token; GITHUB_TOKEN, if set, only raises GitHub's rate limit.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any, Protocol

from matrix_space import markdown
from matrix_space.api import Api, MatrixError
from matrix_space.config import Announcements, Config, ConfigError, ReleaseSource, load, load_announcements
from matrix_space.github import GitHub, GitHubError

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Marks a post with what it announced: release:<owner/repo>:<tag> or headset:<codename>.
MARK = "org.overpressure.announce"
#: How many of the room's latest messages are read for marks.
HISTORY = 1000
CONFIG = Path(__file__).resolve().parent.parent / "config"
#: Exit codes besides 0.
FAILED, NO_TOKEN = 1, 2


class Source(Protocol):
    """What the announcer reads from GitHub: matrix_space.github.GitHub, or the tests' fake."""

    def repositories(self, organization: str) -> list[dict[str, Any]]: ...  # noqa: D102
    def json_file(self, repo: str, path: str) -> dict[str, Any] | None: ...  # noqa: D102
    def releases(self, repo: str) -> list[dict[str, Any]]: ...  # noqa: D102


class Room(Protocol):
    """What the announcer needs of the Matrix server: matrix_space.api.Api, or the tests' fake."""

    def room_id(self, alias: str) -> str | None: ...  # noqa: D102
    def messages(self, room_id: str, limit: int) -> list[dict[str, Any]]: ...  # noqa: D102
    def send(self, room_id: str, content: dict[str, Any]) -> str: ...  # noqa: D102


@dataclass(frozen=True)
class Headset:
    """A headset's repository, as its headset.json names it."""

    repo: str
    url: str
    created: datetime
    codename: str
    name: str


@dataclass(frozen=True)
class Post:
    """One announcement: what it marks, when it happened, a line for the log, and its text (Markdown)."""

    mark: str
    when: datetime
    what: str
    text: str


def when(value: str) -> datetime:
    """Return GitHub's time stamp (2026-11-25T07:18:09Z) as a time."""
    return datetime.fromisoformat(value)


def fill(template: str, values: dict[str, str]) -> str:
    """Return @p template with its $placeholders filled; a line whose placeholder is empty is left out."""
    lines = [
        Template(line).safe_substitute(values)
        for line in template.split("\n")
        if all(values.get(name) for name in re.findall(r"\$(\w+)", line))
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def headsets(github: Source, organization: str) -> list[Headset]:
    """Return the organization's headsets: public, unarchived repositories with a headset.json."""
    found = []
    for repo in github.repositories(organization):
        if repo.get("archived") or repo.get("is_template") or repo.get("private"):
            continue
        data = github.json_file(repo["full_name"], "headset.json") or {}
        if isinstance(data.get("codename"), str) and isinstance(data.get("name"), str):
            found.append(
                Headset(repo["full_name"], repo["html_url"], when(repo["created_at"]), data["codename"], data["name"])
            )
    return found


def release_posts(github: Source, settings: Announcements, sources: Iterable[ReleaseSource]) -> list[Post]:
    """Return a post for each release since settings.since."""
    posts = []
    for source in sources:
        for release in github.releases(source.repo):
            if release.get("draft") or (release.get("prerelease") and not settings.prereleases):
                continue
            published = release.get("published_at")
            if not published or when(published) < settings.since:
                continue
            tag = release["tag_name"]
            values = {
                "title": release.get("name") or tag,
                "tag": tag,
                "kind": "pre-release" if release.get("prerelease") else "release",
                "project": source.name,
                "url": release["html_url"],
            }
            posts.append(
                Post(
                    f"release:{source.repo}:{tag}",
                    when(published),
                    f"{source.repo} {tag}",
                    fill(settings.release_text, values),
                )
            )
    return posts


def headset_posts(found: list[Headset], settings: Announcements, config: Config) -> list[Post]:
    """Return a post for each headset whose repository appeared since settings.since."""
    rooms = {r.key: r.alias for r in config.rooms}
    posts = []
    for headset in found:
        if headset.created < settings.since:
            continue
        values = {
            "name": headset.name,
            "codename": headset.codename,
            "repository": headset.url,
            "room": rooms.get(headset.codename, ""),
        }
        text = fill(settings.headset_text, values)
        posts.append(Post(f"headset:{headset.codename}", headset.created, f"new headset {headset.codename}", text))
    return posts


def posted(server: Room, room_id: str) -> set[str]:
    """Return the marks of what the room's latest messages announced."""
    return {
        mark
        for event in server.messages(room_id, HISTORY)
        if isinstance(mark := event.get("content", {}).get(MARK), str)
    }


def plan(server: Room, github: Source, config: Config, settings: Announcements) -> tuple[str, list[Post], list[Post]]:
    """Return the room, what to post now (oldest first), and what waits for the next runs."""
    room_id = server.room_id(settings.room.alias)
    if room_id is None:
        message = f"{settings.room.alias} doesn't resolve: run the Space workflow first"
        raise MatrixError(message)
    found = headsets(github, settings.organization)
    sources = {s.repo: s for s in settings.repositories}
    if settings.headset_releases:
        for headset in found:
            sources.setdefault(headset.repo, ReleaseSource(headset.repo, f"{headset.name} ({headset.codename})"))
    posts = release_posts(github, settings, sources.values())
    if settings.announce_headsets:
        posts += headset_posts(found, settings, config)
    done = posted(server, room_id)
    new = sorted((p for p in posts if p.mark not in done), key=lambda p: p.when)
    return room_id, new[: settings.max_per_run], new[settings.max_per_run :]


def message(post: Post) -> dict[str, Any]:
    """Return the Matrix message of @p post, with its mark."""
    return {
        "msgtype": "m.text",
        "body": markdown.to_plain(post.text),
        "format": "org.matrix.custom.html",
        "formatted_body": markdown.to_html(post.text),
        MARK: post.mark,
    }


def summary(now: list[Post], later: list[Post], *, applied: bool) -> str:
    """Return the run as Markdown, for the workflow's summary."""
    if not now:
        return "Nothing new to announce.\n"
    head = "Posted" if applied else "Would post (dry run)"
    lines = [f"### {head}", "", *(f"- {p.what}" for p in now)]
    if later:
        lines += ["", f"{len(later)} more in the next runs: {', '.join(p.what for p in later)}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """List or post what is new."""
    parser = argparse.ArgumentParser(prog="python -m matrix_space.announce", description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="post (default: only list what would be posted)")
    parser.add_argument("--config", type=Path, default=CONFIG, metavar="DIR", help="the settings (default: config/)")
    parser.add_argument("--summary", type=Path, metavar="FILE", help="also write the result as Markdown to FILE")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # ty: ignore[unresolved-attribute]  # a TextIOWrapper
    token = os.environ.get("MATRIX_ACCESS_TOKEN", "").strip()
    if not token:
        print("MATRIX_ACCESS_TOKEN isn't set", file=sys.stderr)
        return NO_TOKEN
    try:
        config = load(args.config)
        settings = load_announcements(args.config, config)
        server = Api(os.environ.get("MATRIX_HOMESERVER", config.homeserver), token)
        github = GitHub(os.environ.get("GITHUB_TOKEN") or None)
        room_id, now, later = plan(server, github, config, settings)
        for post in now:
            print(f"{'post' if args.apply else 'would post'}: {post.what}")
            if args.apply:
                server.send(room_id, message(post))
        for post in later:
            print(f"next run: {post.what}")
        if not now:
            print("nothing new to announce")
    except (ConfigError, MatrixError, GitHubError) as e:
        print(f"error: {e}", file=sys.stderr)
        return FAILED
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(summary(now, later, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
