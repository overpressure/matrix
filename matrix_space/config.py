# SPDX-License-Identifier: Unlicense
"""The settings, read from config/: space.yaml, defaults.yaml, people.yaml, rooms/<key>.yaml, announcements.yaml.

Every file is checked strictly: an unknown key, a missing room or a wrong type is an error naming its file,
so that a typo can't quietly leave a setting at what the server has.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

#: The join rules, history visibilities and guest access values the tool sets.
JOINS = ("public", "space")
HISTORIES = ("world_readable", "shared", "invited", "joined")
GUESTS = ("forbidden", "can_join")
#: An account: @localpart:server.
ACCOUNT = re.compile(r"^@[^:\s]+:\S+$")
#: A room's or the space's address: #localpart:server.
ALIAS = re.compile(r"^#[^:\s]+:\S+$")


class ConfigError(Exception):
    """A settings file is missing, isn't valid YAML, or holds something the tool doesn't know."""


@dataclass(frozen=True)
class RoomSettings:
    """What a room shares with the others unless its own file says otherwise."""

    join: str
    history_visibility: str
    guest_access: str
    listed: bool
    suggested: bool


@dataclass(frozen=True)
class Room:
    """One room: rooms/<key>.yaml over defaults.yaml."""

    key: str
    name: str
    alias: str
    topic: str
    icon: str | None
    settings: RoomSettings
    #: The power levels: defaults.yaml's, this room's merged over them, without the people.
    power_levels: dict[str, Any]
    #: The people's levels in this room: people.yaml's, this room's own added.
    people: dict[str, int]
    #: Other names the room may have been created with, letters and digits only.
    also: tuple[str, ...] = ()
    #: The text pinned in the room (Markdown), if any.
    pin: str | None = None


@dataclass(frozen=True)
class Space:
    """The space itself."""

    alias: str
    name: str
    topic: str
    icon: str | None
    join: str
    history_visibility: str
    guest_access: str
    listed: bool


@dataclass(frozen=True)
class Config:
    """Everything the tool makes the server match."""

    server: str
    homeserver: str
    icons: str
    space: Space
    #: The rooms in their order in the space.
    rooms: list[Room] = field(default_factory=list)
    #: The people's levels in the space (people.yaml).
    people: dict[str, int] = field(default_factory=dict)
    #: Set back to the default whoever is above it without being in the settings (people.yaml).
    lower_unlisted: bool = False


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        message = f"{path}: missing"
        raise ConfigError(message)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        message = f"{path}: not valid YAML: {e}"
        raise ConfigError(message) from None
    if not isinstance(data, dict):
        message = f"{path}: must be a mapping of settings"
        raise ConfigError(message)
    return data


class _Fields:
    """Read a file's settings one by one, then refuse whatever is left over."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = dict(data)

    def fail(self, key: str, what: str) -> ConfigError:
        return ConfigError(f"{self.path}: {key}: {what}")

    def take(self, key: str, kind: type | tuple[type, ...], default: object = ...) -> Any:  # noqa: ANN401  # the YAML value
        if key not in self.data:
            if default is ...:
                raise self.fail(key, "missing")
            return default
        value = self.data.pop(key)
        if not isinstance(value, kind):
            names = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
            raise self.fail(key, f"must be {names}, not {type(value).__name__}")
        return value

    def choice(self, key: str, choices: tuple[str, ...], default: object = ...) -> str:
        value = self.take(key, str, default)
        if value not in choices:
            raise self.fail(key, f"{value!r} isn't one of {', '.join(choices)}")
        return value

    def pattern(self, key: str, regex: re.Pattern[str], what: str) -> str:
        value = self.take(key, str)
        if not regex.match(value):
            raise self.fail(key, f"{value!r} isn't {what}")
        return value

    def done(self) -> None:
        if self.data:
            message = f"{self.path}: unknown setting(s): {', '.join(sorted(map(str, self.data)))}"
            raise ConfigError(message)


def _levels(path: Path, key: str, value: object) -> dict[str, Any]:
    """Check a power_levels mapping: numbers, and mappings of numbers for events and notifications."""
    if not isinstance(value, dict):
        message = f"{path}: {key}: must be a mapping"
        raise ConfigError(message)
    for name, level in value.items():
        if name in ("events", "notifications"):
            if not isinstance(level, dict) or not all(_is_level(v) for v in level.values()):
                message = f"{path}: {key}.{name}: must map names to levels (whole numbers)"
                raise ConfigError(message)
        elif name == "users":
            message = f"{path}: {key}.users: the people's levels go into people.yaml or the room's people:"
            raise ConfigError(message)
        elif not _is_level(level):
            message = f"{path}: {key}.{name}: must be a level (a whole number), not {level!r}"
            raise ConfigError(message)
    return value


def _is_level(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _people(path: Path, value: object) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        message = f"{path}: people: must map accounts to levels"
        raise ConfigError(message)
    for account, level in value.items():
        if not isinstance(account, str) or not ACCOUNT.match(account):
            message = f"{path}: people: {account!r} isn't an account (@name:server)"
            raise ConfigError(message)
        if not _is_level(level) or not 0 <= level <= 100:  # noqa: PLR2004  # Matrix's admin level
            message = f"{path}: people: {account}: {level!r} isn't a level from 0 to 100"
            raise ConfigError(message)
    return dict(value)


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Return @p base with @p over merged in, mappings key by key."""
    merged = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _settings(fields: _Fields, defaults: RoomSettings | None) -> RoomSettings:
    def pick(key: str, kind: type) -> Any:  # noqa: ANN401  # one setting
        fallback = ... if defaults is None else getattr(defaults, key)
        if kind is str:
            choices = {"join": JOINS, "history_visibility": HISTORIES, "guest_access": GUESTS}[key]
            return fields.choice(key, choices, fallback)
        return fields.take(key, kind, fallback)

    return RoomSettings(
        join=pick("join", str),
        history_visibility=pick("history_visibility", str),
        guest_access=pick("guest_access", str),
        listed=pick("listed", bool),
        suggested=pick("suggested", bool),
    )


def _room(folder: Path, key: str, defaults: RoomSettings, levels: dict[str, Any], people: dict[str, int]) -> Room:
    path = folder / "rooms" / f"{key}.yaml"
    fields = _Fields(path, _read(path))
    name = fields.take("name", str)
    alias = fields.pattern("alias", ALIAS, "an address (#name:server)")
    topic = fields.take("topic", str).strip()
    icon = fields.take("icon", str, None)
    also = fields.take("also", list, [])
    if not all(isinstance(a, str) and re.fullmatch(r"[a-z0-9]+", a) for a in also):
        what = "must list names in lower-case letters and digits"
        error = fields.fail("also", what)
        raise error
    pin_file = fields.take("pin", str, None)
    pin = None
    if pin_file is not None:
        pin_path = folder / pin_file
        if not pin_path.is_file():
            what = f"{pin_path} doesn't exist"
            error = fields.fail("pin", what)
            raise error
        pin = pin_path.read_text(encoding="utf-8").strip()
    room_levels = _levels(path, "power_levels", fields.take("power_levels", dict, {}))
    room_people = _people(path, fields.take("people", dict, None))
    settings = _settings(fields, defaults)
    fields.done()
    return Room(
        key=key,
        name=name,
        alias=alias,
        topic=topic,
        icon=icon,
        settings=settings,
        power_levels=merge(levels, room_levels),
        people={**people, **room_people},
        also=tuple(also),
        pin=pin,
    )


def load(folder: Path) -> Config:
    """Read and check every settings file under @p folder (config/)."""
    space_path = folder / "space.yaml"
    fields = _Fields(space_path, _read(space_path))
    server = fields.take("server", str)
    homeserver = fields.take("homeserver", str)
    icons = fields.take("icons", str)
    order = fields.take("rooms", list)
    space_fields = _Fields(Path(f"{space_path} (space)"), fields.take("space", dict))
    space = Space(
        alias=space_fields.pattern("alias", ALIAS, "an address (#name:server)"),
        name=space_fields.take("name", str),
        topic=space_fields.take("topic", str).strip(),
        icon=space_fields.take("icon", str, None),
        join=space_fields.choice("join", ("public",), "public"),
        history_visibility=space_fields.choice("history_visibility", HISTORIES, "world_readable"),
        guest_access=space_fields.choice("guest_access", GUESTS, "forbidden"),
        listed=space_fields.take("listed", bool, False),  # noqa: FBT003  # the default
    )
    space_fields.done()
    fields.done()

    defaults_path = folder / "defaults.yaml"
    defaults_fields = _Fields(defaults_path, _read(defaults_path))
    levels = _levels(defaults_path, "power_levels", defaults_fields.take("power_levels", dict))
    defaults = _settings(defaults_fields, None)
    defaults_fields.done()

    people_path = folder / "people.yaml"
    people_fields = _Fields(people_path, _read(people_path))
    people = _people(people_path, people_fields.take("people", dict, None))
    lower_unlisted = people_fields.take("lower_unlisted", bool, False)  # noqa: FBT003  # the default
    people_fields.done()

    if not all(isinstance(k, str) for k in order) or len(set(order)) != len(order):
        message = f"{space_path}: rooms: must list each room's key once"
        raise ConfigError(message)
    files = {p.stem for p in (folder / "rooms").glob("*.yaml")}
    if files - set(order):
        message = f"{space_path}: rooms: doesn't list {', '.join(sorted(files - set(order)))} (rooms/ has them)"
        raise ConfigError(message)
    rooms = [_room(folder, key, defaults, levels, people) for key in order]
    aliases = [r.alias for r in rooms] + [space.alias]
    if len(set(aliases)) != len(aliases):
        message = f"{folder}: two rooms (or a room and the space) share an address"
        raise ConfigError(message)
    return Config(
        server=server,
        homeserver=homeserver,
        icons=icons,
        space=space,
        rooms=rooms,
        people=people,
        lower_unlisted=lower_unlisted,
    )


@dataclass(frozen=True)
class ReleaseSource:
    """A repository whose releases are announced, and how a post names it."""

    repo: str
    name: str


@dataclass(frozen=True)
class Announcements:
    """What the bot posts by itself: config/announcements.yaml."""

    room: Room
    since: datetime
    max_per_run: int
    repositories: list[ReleaseSource]
    #: Also the releases of every headset's repository.
    headset_releases: bool
    prereleases: bool
    organization: str
    announce_headsets: bool
    #: The posts' texts, with $placeholders.
    release_text: str
    headset_text: str


def load_announcements(folder: Path, config: Config) -> Announcements:
    """Read and check config/announcements.yaml against the rooms of @p config."""
    path = folder / "announcements.yaml"
    fields = _Fields(path, _read(path))
    rooms = {r.key: r for r in config.rooms}
    key = fields.take("room", str)
    if key not in rooms:
        error = fields.fail("room", f"{key!r} isn't a room in rooms/")
        raise error
    try:
        since = datetime.fromisoformat(fields.take("since", str))
    except ValueError:
        error = fields.fail("since", "must be a time like 2026-11-25T00:00:00Z")
        raise error from None
    if since.tzinfo is None:
        error = fields.fail("since", "needs its time zone, such as Z for UTC")
        raise error
    max_per_run = fields.take("max_per_run", int)
    if isinstance(max_per_run, bool) or max_per_run < 1:
        error = fields.fail("max_per_run", "must be 1 or more")
        raise error

    releases = _Fields(Path(f"{path} (releases)"), fields.take("releases", dict))
    sources = []
    for item in releases.take("repositories", list, []):
        source = _Fields(Path(f"{path} (releases.repositories)"), item if isinstance(item, dict) else {"": item})
        repo = source.pattern("repo", re.compile(r"^[\w.-]+/[\w.-]+$"), "owner/name")
        sources.append(ReleaseSource(repo=repo, name=source.take("name", str)))
        source.done()
    headset_releases = releases.take("headsets", bool, True)  # noqa: FBT003  # the default
    prereleases = releases.take("prereleases", bool, True)  # noqa: FBT003  # the default
    releases.done()

    headsets = _Fields(Path(f"{path} (headsets)"), fields.take("headsets", dict))
    organization = headsets.take("organization", str)
    announce_headsets = headsets.take("announce", bool, True)  # noqa: FBT003  # the default
    headsets.done()

    texts = _Fields(Path(f"{path} (texts)"), fields.take("texts", dict))
    found = {}
    for name in ("release", "headset"):
        text_path = folder / texts.take(name, str)
        if not text_path.is_file():
            error = texts.fail(name, f"{text_path} doesn't exist")
            raise error
        found[name] = text_path.read_text(encoding="utf-8").strip()
    texts.done()
    fields.done()
    return Announcements(
        room=rooms[key],
        since=since,
        max_per_run=max_per_run,
        repositories=sources,
        headset_releases=headset_releases,
        prereleases=prereleases,
        organization=organization,
        announce_headsets=announce_headsets,
        release_text=found["release"],
        headset_text=found["headset"],
    )
