# SPDX-License-Identifier: Unlicense
"""The planner against a fake server: what it finds different, and that a second run changes nothing."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path

from matrix_space.config import Config, load
from matrix_space.icons import Icons
from matrix_space.plan import PIN_KEY, Change, Options, PlanError, Planner
from tests.fake import FakeServer

CONFIG = Path(__file__).resolve().parent.parent / "config"
ME = "@bot:matrix.org"
CREATE = Options(create=True)


def apply(changes: list[Change]) -> None:
    """Make the changes, as --apply does."""
    for change in changes:
        if change.run:
            change.run()


class Planning(unittest.TestCase):
    def setUp(self):
        self.config = load(CONFIG)
        self.server = FakeServer(ME)
        self.space = self.server.add_room("overpressure", alias="#overpressure:matrix.org", space=True)
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        for icon in [self.config.space.icon, *(r.icon for r in self.config.rooms)]:
            (self.folder / f"{icon}.png").write_bytes(f"png of {icon}".encode())

    def plan(self, config: Config | None = None, options: Options | None = None) -> list[Change]:
        icons = Icons("https://unused.example/", self.folder)  # a fresh cache, as every run has
        return Planner(self.server, config or self.config, icons, options or Options(), ME).plan()

    def settle(self) -> None:
        """Run the planner until the server matches: create, then configure."""
        for _ in range(3):
            apply(self.plan(options=CREATE))

    def room(self, key: str) -> str:
        alias = next(r.alias for r in self.config.rooms if r.key == key)
        room_id = self.server.room_id(alias)
        assert room_id is not None
        return room_id

    def with_room(self, key: str, **changes: object) -> Config:
        rooms = [dataclasses.replace(r, **changes) if r.key == key else r for r in self.config.rooms]
        return dataclasses.replace(self.config, rooms=rooms)

    # Rooms.

    def test_an_empty_space_gets_its_rooms_and_a_second_run_changes_nothing(self):
        first = self.plan(options=CREATE)
        self.assertEqual(sum(1 for c in first if c.kind == "create"), len(self.config.rooms))
        self.settle()
        self.assertEqual(self.plan(), [])

    def test_a_room_it_cant_find_is_only_a_note_without_create(self):
        changes = self.plan()
        missing = [c for c in changes if "not found by" in c.what]
        self.assertEqual(len(missing), len(self.config.rooms))
        self.assertTrue(all(c.run is None for c in missing))
        apply(changes)
        self.assertNotIn("create", " ".join(self.server.calls))

    def test_a_dry_run_changes_nothing(self):
        self.plan(options=CREATE)
        self.assertEqual(self.server.calls, [])

    def test_the_rooms_end_up_as_the_settings_say(self):
        self.settle()
        help_room = self.room("help")
        self.assertEqual(self.server.content(help_room, "m.room.topic")["topic"], self.config.rooms[2].topic)
        self.assertEqual(
            self.server.content(help_room, "m.room.history_visibility"), {"history_visibility": "world_readable"}
        )
        announcements = self.server.content(self.room("announcements"), "m.room.power_levels")
        self.assertEqual(announcements["events_default"], 25)
        self.assertEqual(announcements["users"], {ME: 100})
        child = self.server.content(self.space, "m.space.child", self.room("hollywood"))
        self.assertEqual(child["order"], "05")

    def test_an_existing_room_is_found_by_its_name_and_gets_its_address(self):
        room_id = self.server.add_room("Quest 2")
        changes = self.plan()
        self.assertIn(f"Meta Quest 2 ({room_id})", {c.room for c in changes})
        self.assertIn("address #overpressure-hollywood:matrix.org: create", [c.what for c in changes])

    def test_a_changed_topic_is_set_again(self):
        self.settle()
        self.server.put_state(self.room("dev"), "m.room.topic", "", {"topic": "old"})
        self.assertEqual([c.what[:9] for c in self.plan()], ["topic old"])

    def test_keep_names_leaves_a_name_alone(self):
        self.settle()
        self.server.put_state(self.room("help"), "m.room.name", "", {"name": "Support"})
        self.assertEqual(self.plan(options=Options(keep_names=True)), [])

    def test_an_address_another_room_holds_is_left_alone(self):
        other = self.server.add_room("Something else", alias="#overpressure-dev:matrix.org")
        changes = self.plan(options=Options(rooms={"dev": self.server.add_room("Development")}))
        self.assertTrue(any(c.run is None and f"belongs to {other}" in c.what for c in changes))

    def test_a_space_that_doesnt_resolve(self):
        del self.server.aliases["#overpressure:matrix.org"]
        with self.assertRaises(PlanError):
            self.plan()

    # People.

    def set_users(self, room_id: str, users: dict[str, int]) -> None:
        levels = self.server.content(room_id, "m.room.power_levels")
        levels["users"] = users
        self.server.put_state(room_id, "m.room.power_levels", "", levels)

    def test_someone_unlisted_is_only_noted_by_default(self):
        self.settle()
        self.set_users(self.room("help"), {ME: 100, "@mod:matrix.org": 50})
        changes = self.plan()
        self.assertEqual(
            [c.what for c in changes], ["@mod:matrix.org is at 50, not in the settings: left (lower_unlisted is off)"]
        )
        self.assertIsNone(changes[0].run)

    def test_lower_unlisted_sets_them_back_and_notes_an_equal(self):
        self.settle()
        room_id = self.room("help")
        self.set_users(room_id, {ME: 100, "@mod:matrix.org": 50, "@admin:matrix.org": 100})
        changes = self.plan(dataclasses.replace(self.config, lower_unlisted=True))
        self.assertTrue(any(c.run is None and "@admin:matrix.org is at 100" in c.what for c in changes))
        apply(changes)
        users = self.server.content(room_id, "m.room.power_levels")["users"]
        self.assertEqual(users, {ME: 100, "@admin:matrix.org": 100})

    def test_the_peoples_levels_are_set_in_every_room_and_the_space(self):
        self.settle()
        config = dataclasses.replace(
            self.config,
            people={"@mod:matrix.org": 50},
            rooms=[dataclasses.replace(r, people={"@mod:matrix.org": 50}) for r in self.config.rooms],
        )
        apply(self.plan(config))
        for room_id in [self.space, self.room("general"), self.room("offtopic")]:
            self.assertEqual(self.server.content(room_id, "m.room.power_levels")["users"]["@mod:matrix.org"], 50)

    def test_a_rooms_creator_isnt_listed_from_version_12_on(self):
        room_id = self.server.add_room("Off-topic", alias="#overpressure-offtopic:matrix.org", version="12", levels={})
        self.settle()
        self.assertNotIn(ME, self.server.content(room_id, "m.room.power_levels").get("users", {}))

    # Pinned texts.

    def pins(self, key: str) -> list[str]:
        return self.server.content(self.room(key), "m.room.pinned_events")["pinned"]

    def test_a_changed_text_is_edited_in_place(self):
        self.settle()
        before = self.pins("general")
        changes = self.plan(self.with_room("general", pin="New **rules**."))
        self.assertEqual([c.what for c in changes], ["edit the pinned text in place: New **rules**...."])
        apply(changes)
        self.assertEqual(self.pins("general"), before)
        shown = self.server.event(self.room("general"), before[0])["unsigned"]["m.relations"]["m.replace"]
        self.assertEqual(shown["content"]["m.new_content"]["formatted_body"], "<p>New <strong>rules</strong>.</p>")
        self.assertEqual(self.plan(self.with_room("general", pin="New **rules**.")), [])

    def test_a_text_another_account_posted_is_posted_anew_in_its_place(self):
        room_id = self.server.add_room("General", alias="#overpressure-general:matrix.org")
        manual = self.server.send(room_id, {"body": "mine"})
        theirs = self.server.send(room_id, {"body": "old", PIN_KEY: "general"}, sender="@owner:matrix.org")
        self.server.put_state(room_id, "m.room.pinned_events", "", {"pinned": [manual, theirs]})
        self.settle()
        pinned = self.pins("general")
        self.assertEqual(len(pinned), 2)
        self.assertEqual(pinned[0], manual)
        self.assertNotEqual(pinned[1], theirs)
        self.assertEqual(self.server.event(room_id, pinned[1])["sender"], ME)

    def test_copies_of_the_text_are_unpinned_and_other_pins_stay(self):
        self.settle()
        room_id = self.room("help")
        mine = self.pins("help")[0]
        manual = self.server.send(room_id, {"body": "someone's pin"})
        copy = self.server.send(room_id, {"body": "old copy", PIN_KEY: "help"})
        self.server.put_state(room_id, "m.room.pinned_events", "", {"pinned": [manual, mine, copy]})
        changes = self.plan()
        self.assertEqual([c.what for c in changes], ["unpin 1 other copy/copies of the pinned text"])
        apply(changes)
        self.assertEqual(self.pins("help"), [manual, mine])

    def test_an_unpinned_text_is_pinned_again(self):
        self.settle()
        self.server.put_state(self.room("dev"), "m.room.pinned_events", "", {"pinned": []})
        self.assertEqual([c.what[:13] for c in self.plan()], ["post and pin:"])

    # Icons.

    def test_a_missing_icon_is_a_note(self):
        self.settle()
        changes = self.plan(self.with_room("dev", icon="nothing"))
        self.assertTrue(any(c.run is None and "nothing.png not found" in c.what for c in changes))

    def test_an_icon_is_uploaded_once(self):
        self.settle()
        uploads = len(self.server.uploads)
        self.settle()
        self.assertEqual(len(self.server.uploads), uploads)

    def test_a_new_icon_under_the_same_name_is_uploaded(self):
        self.settle()
        (self.folder / "help.png").write_bytes(b"a new drawing")
        self.assertEqual([c.what for c in self.plan()], ["replace the icon: help.png"])

    def test_only_icons_leaves_everything_else_alone(self):
        self.settle()
        self.server.put_state(self.room("dev"), "m.room.topic", "", {"topic": "old"})
        (self.folder / "dev.png").write_bytes(b"a new drawing")
        self.assertEqual([c.what for c in self.plan(options=Options(only_icons=True))], ["replace the icon: dev.png"])


if __name__ == "__main__":
    unittest.main()
