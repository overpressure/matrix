# SPDX-License-Identifier: Unlicense
"""The settings in config/ load, and a mistake in any file is an error naming it."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from matrix_space.config import ConfigError, load, load_announcements

CONFIG = Path(__file__).resolve().parent.parent / "config"


class TheSettings(unittest.TestCase):
    def test_every_room_loads_in_the_spaces_order(self):
        config = load(CONFIG)
        self.assertEqual(
            [r.key for r in config.rooms],
            ["general", "announcements", "help", "phoenix", "hollywood", "dev", "offtopic"],
        )
        self.assertEqual(config.space.alias, "#overpressure:matrix.org")

    def test_a_rooms_power_levels_are_merged_over_the_defaults(self):
        rooms = {r.key: r for r in load(CONFIG).rooms}
        announcements = rooms["announcements"].power_levels
        self.assertEqual(announcements["events_default"], 25)
        self.assertEqual(announcements["notifications"], {"room": 25})
        self.assertEqual(announcements["events"]["m.room.power_levels"], 100)
        self.assertEqual(rooms["help"].power_levels["events_default"], 0)

    def test_the_pinned_texts_are_read_from_their_files(self):
        rooms = {r.key: r for r in load(CONFIG).rooms}
        assert rooms["general"].pin is not None
        self.assertTrue(rooms["general"].pin.startswith("Welcome to overpressure!"))
        self.assertIsNone(rooms["phoenix"].pin)

    def test_the_announcements_load(self):
        config = load(CONFIG)
        settings = load_announcements(CONFIG, config)
        self.assertEqual(settings.room.key, "announcements")
        self.assertEqual([s.repo for s in settings.repositories], ["overpressure/overpressure"])
        self.assertIn("$title", settings.release_text)

    def test_unlisted_people_are_not_lowered_by_default(self):
        self.assertFalse(load(CONFIG).lower_unlisted)

    def test_a_room_takes_the_defaults_it_doesnt_set(self):
        rooms = {r.key: r for r in load(CONFIG).rooms}
        self.assertTrue(rooms["general"].settings.listed)
        self.assertFalse(rooms["hollywood"].settings.listed)
        self.assertEqual(rooms["hollywood"].settings.history_visibility, "world_readable")


class Mistakes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        shutil.copytree(CONFIG, self.tmp / "config")
        self.config = self.tmp / "config"

    def write(self, name: str, text: str) -> None:
        (self.config / name).write_text(text, encoding="utf-8")

    def append(self, name: str, text: str) -> None:
        path = self.config / name
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def assert_error(self, *words: str) -> None:
        with self.assertRaises(ConfigError) as caught:
            load(self.config)
        for word in words:
            self.assertIn(word, str(caught.exception))

    def test_an_unknown_setting(self):
        self.append("rooms/phoenix.yaml", "topik: typo\n")
        self.assert_error("phoenix.yaml", "unknown setting", "topik")

    def test_a_room_file_the_space_doesnt_list(self):
        self.write("rooms/extra.yaml", "name: Extra\nalias: '#extra:matrix.org'\ntopic: x\n")
        self.assert_error("space.yaml", "doesn't list extra")

    def test_a_listed_room_without_its_file(self):
        (self.config / "rooms/offtopic.yaml").unlink()
        self.assert_error("offtopic.yaml", "missing")

    def test_a_join_rule_it_doesnt_know(self):
        self.append("rooms/help.yaml", "join: invite\n")
        self.assert_error("help.yaml", "join", "'invite'")

    def test_a_pinned_text_that_doesnt_exist(self):
        self.append("rooms/offtopic.yaml", "pin: texts/nothing.md\n")
        self.assert_error("offtopic.yaml", "pin")

    def test_an_account_that_isnt_one(self):
        self.write("people.yaml", "people:\n  someone: 50\n")
        self.assert_error("people.yaml", "'someone'")

    def test_lower_unlisted_that_isnt_true_or_false(self):
        self.write("people.yaml", "lower_unlisted: sometimes\npeople: {}\n")
        self.assert_error("people.yaml", "lower_unlisted")

    def test_a_level_above_admin(self):
        self.write("people.yaml", "people:\n  '@a:matrix.org': 150\n")
        self.assert_error("people.yaml", "150")

    def test_people_in_the_power_levels(self):
        self.append("rooms/dev.yaml", "power_levels:\n  users:\n    '@a:matrix.org': 50\n")
        self.assert_error("dev.yaml", "people")

    def test_a_level_that_isnt_a_number(self):
        self.append("rooms/dev.yaml", "power_levels:\n  kick: high\n")
        self.assert_error("dev.yaml", "kick")

    def test_two_rooms_with_one_address(self):
        text = (self.config / "rooms/dev.yaml").read_text(encoding="utf-8")
        self.write("rooms/dev.yaml", text.replace("#overpressure-dev:", "#overpressure-help:"))
        self.assert_error("share an address")

    def assert_announcements_error(self, *words: str) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_announcements(self.config, load(self.config))
        for word in words:
            self.assertIn(word, str(caught.exception))

    def test_announcements_to_a_room_that_doesnt_exist(self):
        self.append("announcements.yaml", "")
        text = (self.config / "announcements.yaml").read_text(encoding="utf-8")
        self.write("announcements.yaml", text.replace("room: announcements", "room: news"))
        self.assert_announcements_error("announcements.yaml", "'news'")

    def test_announcements_since_without_a_time_zone(self):
        text = (self.config / "announcements.yaml").read_text(encoding="utf-8")
        self.write("announcements.yaml", text.replace('"2026-11-25T00:00:00Z"', '"2026-11-25T00:00:00"'))
        self.assert_announcements_error("since", "time zone")

    def test_announcements_with_a_missing_text(self):
        text = (self.config / "announcements.yaml").read_text(encoding="utf-8")
        self.write("announcements.yaml", text.replace("texts/headset.md", "texts/nothing.md"))
        self.assert_announcements_error("headset", "nothing.md")

    def test_yaml_that_doesnt_parse(self):
        self.write("rooms/dev.yaml", "name: [unclosed\n")
        self.assert_error("dev.yaml", "not valid YAML")


if __name__ == "__main__":
    unittest.main()
