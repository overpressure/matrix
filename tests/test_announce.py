# SPDX-License-Identifier: Unlicense
"""The announcer against a fake GitHub and a fake Matrix server: what it posts, once."""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path
from typing import Any

from matrix_space.announce import MARK, fill, message, plan
from matrix_space.config import load, load_announcements
from tests.fake import FakeServer

CONFIG = Path(__file__).resolve().parent.parent / "config"
ME = "@bot:matrix.org"


class FakeGitHub:
    """An organization's repositories, their headset.json and their releases."""

    def __init__(self) -> None:
        self.repos: list[dict[str, Any]] = []
        self.files: dict[str, dict[str, Any]] = {}
        self.released: dict[str, list[dict[str, Any]]] = {}

    def add_repo(self, name: str, created: str, headset: dict[str, Any] | None = None, **extra: object) -> None:
        full = f"overpressure/{name}"
        self.repos.append({"full_name": full, "html_url": f"https://github.com/{full}", "created_at": created, **extra})
        if headset is not None:
            self.files[full] = headset

    def release(self, repo: str, tag: str, published: str, **extra: object) -> None:
        self.released.setdefault(repo, []).insert(
            0,
            {
                "tag_name": tag,
                "name": f"{repo.split('/')[1]} {tag}",
                "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
                "published_at": published,
                "draft": False,
                "prerelease": False,
                **extra,
            },
        )

    def repositories(self, organization: str) -> list[dict[str, Any]]:
        assert organization == "overpressure"
        return list(self.repos)

    def json_file(self, repo: str, path: str) -> dict[str, Any] | None:
        assert path == "headset.json"
        return self.files.get(repo)

    def releases(self, repo: str) -> list[dict[str, Any]]:
        return list(self.released.get(repo, []))


class Announcing(unittest.TestCase):
    def setUp(self):
        self.config = load(CONFIG)
        self.settings = dataclasses.replace(load_announcements(CONFIG, self.config), max_per_run=10)
        self.server = FakeServer(ME)
        self.room = self.server.add_room("Announcements", alias="#overpressure-announcements:matrix.org")
        self.github = FakeGitHub()
        self.github.add_repo("overpressure", "2026-11-11T21:15:19Z")
        self.github.add_repo("phoenix", "2026-11-14T12:45:34Z", {"codename": "phoenix", "name": "PICO 4"})
        self.github.add_repo(
            "headset-template", "2026-11-17T11:22:37Z", {"codename": "x", "name": "X"}, is_template=True
        )

    def run_once(self, settings: object = None) -> list[str]:
        """Plan and post, as --apply does; return what was posted."""
        _, now, _ = plan(self.server, self.github, self.config, settings or self.settings)  # ty: ignore[invalid-argument-type]  # Announcements
        for post in now:
            self.server.send(self.room, message(post))
        return [p.mark for p in now]

    def test_a_release_is_posted_once(self):
        self.github.release("overpressure/overpressure", "2026.12.0", "2026-12-01T10:00:00Z")
        self.assertEqual(self.run_once(), ["release:overpressure/overpressure:2026.12.0"])
        self.assertEqual(self.run_once(), [])

    def test_the_post_names_the_release_and_links_it(self):
        self.github.release("overpressure/overpressure", "2026.12.0b1", "2026-12-01T10:00:00Z", prerelease=True)
        self.run_once()
        body = self.server.messages(self.room, 1)[0]["content"]
        self.assertEqual(body[MARK], "release:overpressure/overpressure:2026.12.0b1")
        self.assertIn("a pre-release of overpressure", body["body"])
        self.assertIn("https://github.com/overpressure/overpressure/releases/tag/2026.12.0b1", body["formatted_body"])

    def test_nothing_before_since_drafts_or_unwanted_pre_releases(self):
        self.github.release("overpressure/overpressure", "old", "2026-11-18T10:00:00Z")
        self.github.release("overpressure/overpressure", "draft", "2026-12-01T10:00:00Z", draft=True)
        self.github.release("overpressure/overpressure", "beta", "2026-12-02T10:00:00Z", prerelease=True)
        self.assertEqual(self.run_once(dataclasses.replace(self.settings, prereleases=False)), [])

    def test_a_headsets_releases_are_announced_without_naming_its_repository(self):
        self.github.release("overpressure/phoenix", "2026.12.0", "2026-12-01T10:00:00Z")
        self.run_once()
        body = self.server.messages(self.room, 1)[0]["content"]["body"]
        self.assertIn("a release of PICO 4 (phoenix)", body)

    def test_a_new_headset_is_announced_with_its_room(self):
        self.github.add_repo("hollywood", "2026-11-25T07:18:09Z", {"codename": "hollywood", "name": "Meta Quest 2"})
        self.assertEqual(self.run_once(), ["headset:hollywood"])
        body = self.server.messages(self.room, 1)[0]["content"]["body"]
        self.assertIn("A new headset joins overpressure: the Meta Quest 2 (hollywood).", body)
        self.assertIn("Its room: #overpressure-hollywood:matrix.org", body)
        self.assertEqual(self.run_once(), [])

    def test_a_headset_without_a_room_leaves_that_line_out(self):
        self.github.add_repo("eureka", "2026-12-01T00:00:00Z", {"codename": "eureka", "name": "Quest 3"})
        self.run_once()
        self.assertNotIn("Its room", self.server.messages(self.room, 1)[0]["content"]["body"])

    def test_old_headsets_templates_and_archived_repositories_are_not_announced(self):
        self.github.add_repo("old", "2026-12-01T00:00:00Z", {"codename": "old", "name": "Old"}, archived=True)
        self.assertEqual(self.run_once(), [])

    def test_at_most_max_per_run_oldest_first(self):
        for day in (3, 1, 2):
            self.github.release("overpressure/overpressure", f"2026.12.{day}", f"2026-12-0{day}T10:00:00Z")
        settings = dataclasses.replace(self.settings, max_per_run=2)
        self.assertEqual(
            self.run_once(settings),
            ["release:overpressure/overpressure:2026.12.1", "release:overpressure/overpressure:2026.12.2"],
        )
        self.assertEqual(self.run_once(settings), ["release:overpressure/overpressure:2026.12.3"])

    def test_a_dry_run_posts_nothing(self):
        self.github.release("overpressure/overpressure", "2026.12.0", "2026-12-01T10:00:00Z")
        plan(self.server, self.github, self.config, self.settings)
        self.assertEqual(self.server.calls, [])


class Filling(unittest.TestCase):
    def test_placeholders_are_filled_and_empty_ones_drop_their_line(self):
        self.assertEqual(fill("Hi $name.\nRoom: $room\n\nBye", {"name": "you", "room": ""}), "Hi you.\n\nBye")


if __name__ == "__main__":
    unittest.main()
