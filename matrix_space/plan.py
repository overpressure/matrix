# SPDX-License-Identifier: Unlicense
"""Compare the server with the settings and list the changes that make it match.

Each change carries the call that makes it; a change without one is a note: something the tool can't or
won't change (an address another room holds, an account above its own level), for a person to look at.
Nothing is changed while the changes are listed: the caller runs them (--apply).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from matrix_space import markdown

if TYPE_CHECKING:
    from collections.abc import Callable

    from matrix_space.config import Config, Room, Space
    from matrix_space.icons import Icons

#: Marks the messages the tool pinned (the value is the room's key), so that it finds them again.
PIN_KEY = "org.overpressure.pin"
#: The SHA-256 of the icon a room's avatar was set from, so that an unchanged icon isn't uploaded again.
ICON_KEY = "org.overpressure.icon_sha256"
#: The admin level, and the room version from which a room's creators outrank it.
ADMIN = 100
CREATORS_OUTRANK_FROM = 12

State = dict[tuple[str, str], dict[str, Any]]


class Server(Protocol):
    """What the tool asks of the server: matrix_space.api.Api, or the tests' fake."""

    def room_id(self, alias: str) -> str | None: ...  # noqa: D102
    def set_alias(self, alias: str, room_id: str) -> None: ...  # noqa: D102
    def joined_rooms(self) -> list[str]: ...  # noqa: D102
    def state(self, room_id: str) -> State: ...  # noqa: D102
    def put_state(self, room_id: str, event_type: str, state_key: str, content: dict[str, Any]) -> None: ...  # noqa: D102
    def create_room(self, body: dict[str, Any]) -> str: ...  # noqa: D102
    def upload(self, data: bytes, filename: str) -> str: ...  # noqa: D102
    def send(self, room_id: str, content: dict[str, Any]) -> str: ...  # noqa: D102
    def event(self, room_id: str, event_id: str) -> dict[str, Any]: ...  # noqa: D102
    def directory(self, room_id: str) -> str | None: ...  # noqa: D102
    def set_directory(self, room_id: str, visibility: str) -> None: ...  # noqa: D102


@dataclass
class Change:
    """One difference: what it changes, in which room, and the call that changes it (None: a note)."""

    room: str
    what: str
    run: Callable[[], object] | None = field(repr=False, default=None)
    #: "icon" for an icon (--only-icons makes those alone), "create" for a new room, "setting" for the rest.
    kind: str = "setting"


@dataclass(frozen=True)
class Options:
    """How far the tool goes."""

    keep_names: bool = False
    skip_icons: bool = False
    #: Also the server's public room directory (matrix.org refuses most accounts).
    directory: bool = False
    #: Which existing room a room is, by key, where its address and name don't find it.
    rooms: dict[str, str] = field(default_factory=dict)
    #: Create the rooms it can't find (else they are notes: a room made by hand may be there under another name).
    create: bool = False
    #: Only the icons: nothing else is listed or changed.
    only_icons: bool = False


class PlanError(Exception):
    """The server's state doesn't allow a plan: the space is missing, or a room is ambiguous."""


def content_of(state: State, event_type: str, state_key: str = "") -> dict[str, Any]:
    """Return a state event's content, {} when the room has none."""
    event = state.get((event_type, state_key))
    return dict(event["content"]) if event else {}


def norm(text: str) -> str:
    """Return @p text in lower-case letters and digits only, to compare room names."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def creators(state: State) -> set[str]:
    """Return the room's creators where they outrank every level (room version 12 and later), else none.

    From version 12 on, whoever created the room (and its additional_creators) has a power above 100 that no
    one can take away, and must not be listed in the power levels' users.
    """
    create = state.get(("m.room.create", ""))
    if create is None:
        return set()
    version = create["content"].get("room_version", "1")
    if version.isdigit() and int(version) < CREATORS_OUTRANK_FROM:
        return set()
    return {create.get("sender", ""), *create["content"].get("additional_creators", [])}


def level_of(state: State, account: str) -> float:
    """Return @p account's power level in the room: infinite for a creator from version 12 on."""
    if account in creators(state):
        return float("inf")
    levels = content_of(state, "m.room.power_levels")
    return levels.get("users", {}).get(account, levels.get("users_default", 0))


def describe(old: object, new: object) -> str:
    """Return "old -> new", shortened."""

    def short(value: object) -> str:
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        return text if len(text) <= 70 else text[:67] + "..."  # noqa: PLR2004  # a line's width

    return f"{short(old) if old not in (None, '', {}) else '(none)'} -> {short(new)}"


def level_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Return what differs between two power levels, one entry per key."""
    parts = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) or isinstance(b, dict):
            a, b = a or {}, b or {}
            subs = sorted(set(a) | set(b))
            parts += [f"{key}.{sub} {a.get(sub)} -> {b.get(sub)}" for sub in subs if a.get(sub) != b.get(sub)]
        elif a != b:
            parts.append(f"{key} {a} -> {b}")
    return parts


def wanted_levels(  # noqa: PLR0913  # the room's levels, the settings' and who I am
    current: dict[str, Any],
    levels: dict[str, Any],
    people: dict[str, int],
    state: State,
    me: str,
    *,
    lower_unlisted: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Return the room's power levels as the settings want them, and what isn't made so.

    The settings' levels are merged over the room's current ones (keys the settings don't name stay). The
    people listed get their levels. Anyone above the default who isn't listed is only noted, unless
    @p lower_unlisted: then they go back to the default, except those at or above my own level, which Matrix
    doesn't let me change. I stay at my own level.
    """
    new = copy.deepcopy(current)
    for key, value in levels.items():
        new[key] = {**new.get(key, {}), **value} if isinstance(value, dict) else value
    mine = level_of(state, me)
    outranking = creators(state)
    default = new.get("users_default", 0)
    users = dict(current.get("users", {}))
    notes = _unlisted(users, people, me, mine, default, lower=lower_unlisted)
    notes += _set_listed(users, people, outranking | {me}, mine, default)
    if me not in outranking:
        users[me] = max(users.get(me, default), ADMIN)
    new["users"] = users
    return new, notes


def _unlisted(  # noqa: PLR0913  # the levels, the settings' people and who I am
    users: dict[str, int], people: dict[str, int], me: str, mine: float, default: int, *, lower: bool
) -> list[str]:
    """Note everyone above the default who isn't in @p people; with @p lower, set back those below me."""
    notes = []
    for account, level in sorted(users.items()):
        if account == me or account in people or level <= default:
            continue
        if not lower:
            notes.append(f"{account} is at {level}, not in the settings: left (lower_unlisted is off)")
        elif level >= mine:
            notes.append(f"{account} is at {level}, not in the settings: can't lower it from {mine}")
        else:
            del users[account]
    return notes


def _set_listed(users: dict[str, int], people: dict[str, int], skip: set[str], mine: float, default: int) -> list[str]:
    """Give everyone in @p people their level where Matrix lets me; note where it doesn't."""
    notes = []
    for account, level in sorted(people.items()):
        have = users.get(account, default)
        if account in skip or have == level:
            continue
        if have >= mine:
            notes.append(f"{account} is at {have}, the settings say {level}: can't change it from {mine}")
        elif level > mine:
            notes.append(f"{account} should be at {level}, above my own {mine}: left at {have}")
        elif level == default:
            users.pop(account, None)
        else:
            users[account] = level
    return notes


class Planner:
    """Lists the changes that make the server match the settings."""

    def __init__(self, server: Server, config: Config, icons: Icons, options: Options, me: str) -> None:
        """Plan for the account @p me on @p server."""
        self.server = server
        self.config = config
        self.icons = icons
        self.options = options
        self.me = me
        self.changes: list[Change] = []

    def add(self, room: str, what: str, run: Callable[[], object] | None = None, kind: str = "setting") -> None:
        """Note one change."""
        self.changes.append(Change(room, what, run, kind))

    def plan(self) -> list[Change]:
        """Return every change, the rooms' first and the space's last."""
        space_id = self.server.room_id(self.config.space.alias)
        if space_id is None:
            message = f"{self.config.space.alias} doesn't resolve: is it the space's address?"
            raise PlanError(message)
        space_state = self.server.state(space_id)
        if content_of(space_state, "m.room.create").get("type") != "m.space":
            message = f"{self.config.space.alias} is a room, not a space"
            raise PlanError(message)
        candidates = self._candidates(space_id, space_state)
        found = self._find(candidates)
        for index, room in enumerate(self.config.rooms):
            room_id = found.get(room.key)
            if room_id is None:
                self._create(room, found)
                continue
            self._room(room, room_id, candidates[room_id], space_id=space_id, space_state=space_state, index=index)
        self._space(self.config.space, space_id, space_state, set(found.values()), candidates)
        if self.options.only_icons:
            return [c for c in self.changes if c.kind == "icon"]
        return self.changes

    def _candidates(self, space_id: str, space_state: State) -> dict[str, State]:
        """Return the rooms that may be the settings' ones: the space's children and the rooms I'm in."""
        children = [k for (t, k), e in space_state.items() if t == "m.space.child" and e["content"].get("via")]
        candidates: dict[str, State] = {}
        for room_id in dict.fromkeys(children + self.server.joined_rooms()):
            if room_id == space_id:
                continue
            try:
                state = self.server.state(room_id)
            except Exception:  # noqa: BLE001, S112  # a child I haven't joined: its state isn't readable
                continue
            if content_of(state, "m.room.create").get("type") != "m.space":
                candidates[room_id] = state
        return candidates

    def _find(self, candidates: dict[str, State]) -> dict[str, str]:
        """Find each room: --room, else its address, else its name or another it may have had."""
        found: dict[str, str] = {}
        for room in self.config.rooms:
            room_id = self.options.rooms.get(room.key) or self.server.room_id(room.alias)
            if room_id is None:
                names = {norm(room.name), norm(room.key), *room.also}
                hits = [
                    r
                    for r, s in candidates.items()
                    if norm(content_of(s, "m.room.name").get("name", "")) in names and r not in found.values()
                ]
                if len(hits) > 1:
                    message = (
                        f"{room.name}: several rooms match ({', '.join(hits)}); say which with --room {room.key}=ID"
                    )
                    raise PlanError(message)
                room_id = hits[0] if hits else None
            if room_id is not None:
                found[room.key] = room_id
                if room_id not in candidates:
                    candidates[room_id] = self.server.state(room_id)
        return found

    def _create(self, room: Room, found: dict[str, str]) -> None:
        if not self.options.create:
            self.add(
                room.name,
                f"not found by {room.alias}, its name or also: left alone. If it really doesn't exist, --create "
                "(the Space workflow run by hand, create ticked) creates it",
            )
            return
        body = {
            "name": room.name,
            "topic": room.topic,
            "room_alias_name": room.alias[1:].split(":")[0],
            "preset": "public_chat",
            "visibility": "public" if room.settings.listed else "private",
        }

        def create() -> None:
            found[room.key] = self.server.create_room(body)

        self.add(room.name, "create the room (not found by address or name); then set it up", create, kind="create")

    def _set(self, label: str, room_id: str, event_type: str, content: dict[str, Any], what: str) -> None:
        self.add(label, what, lambda: self.server.put_state(room_id, event_type, "", content))

    def _room(  # noqa: PLR0913  # the room, where it is, and its place in the space
        self, room: Room, room_id: str, state: State, *, space_id: str, space_state: State, index: int
    ) -> None:
        label = f"{room.name} ({room_id})"
        name = content_of(state, "m.room.name").get("name", "")
        if not self.options.keep_names and name != room.name:
            self._set(label, room_id, "m.room.name", {"name": room.name}, "name " + describe(name, room.name))
        topic = content_of(state, "m.room.topic").get("topic", "")
        if topic != room.topic:
            self._set(label, room_id, "m.room.topic", {"topic": room.topic}, "topic " + describe(topic, room.topic))
        if room.icon and not self.options.skip_icons:
            self._icon(label, room_id, state, room.icon)
        self._alias(label, room_id, state, room.alias)
        self._join(label, room_id, state, room.settings.join, space_id)
        self._simple(label, room_id, state, room.settings.history_visibility, room.settings.guest_access)
        if ("m.room.encryption", "") in state:
            self.add(label, "encrypted: Matrix can't turn that off; recreate the room if it matters")
        self._levels(label, room_id, state, room.power_levels, room.people)
        parent = content_of(state, "m.space.parent", space_id)
        if not parent.get("via") or not parent.get("canonical"):
            content = {"via": [self.config.server], "canonical": True}
            self.add(
                label, "parent space: set", lambda: self.server.put_state(room_id, "m.space.parent", space_id, content)
            )
        child = content_of(space_state, "m.space.child", room_id)
        want = {
            **child,
            "via": child.get("via") or [self.config.server],
            "order": f"{index + 1:02d}",
            "suggested": room.settings.suggested,
        }
        if child != want:
            self.add(
                label,
                "in the space: " + describe(child, want),
                lambda: self.server.put_state(space_id, "m.space.child", room_id, want),
            )
        if self.options.directory:
            self._directory(label, room_id, listed=room.settings.listed)
        if room.pin:
            self._pin(label, room_id, state, room)

    def _icon(self, label: str, room_id: str, state: State, icon: str) -> None:
        data = self.icons.get(icon)
        if data is None:
            self.add(label, f"icon {self.icons.where(icon)} not found: left alone", kind="icon")
            return
        digest = hashlib.sha256(data).hexdigest()
        current = content_of(state, "m.room.avatar")
        if current.get(ICON_KEY) == digest:
            return

        def run() -> None:
            url = self.server.upload(data, f"overpressure-{icon}.png")
            info = {"mimetype": "image/png", "w": 512, "h": 512, "size": len(data)}
            self.server.put_state(room_id, "m.room.avatar", "", {"url": url, "info": info, ICON_KEY: digest})

        self.add(label, f"{'replace' if current.get('url') else 'set'} the icon: {icon}.png", run, kind="icon")

    def _alias(self, label: str, room_id: str, state: State, alias: str) -> None:
        owner = self.server.room_id(alias)
        if owner is None:
            self.add(label, f"address {alias}: create", lambda: self.server.set_alias(alias, room_id))
        elif owner != room_id:
            self.add(label, f"address {alias} belongs to {owner}: left alone (fix by hand)")
            return
        current = content_of(state, "m.room.canonical_alias")
        if current.get("alias") != alias:
            content = {**current, "alias": alias}
            self._set(
                label,
                room_id,
                "m.room.canonical_alias",
                content,
                "main address " + describe(current.get("alias"), alias),
            )

    def _join(self, label: str, room_id: str, state: State, join: str, space_id: str) -> None:
        version = content_of(state, "m.room.create").get("room_version", "1")
        restricted_from = 8  # the room version that knows the restricted join rule
        if join == "space" and version.isdigit() and int(version) >= restricted_from:
            want: dict[str, Any] = {
                "join_rule": "restricted",
                "allow": [{"type": "m.room_membership", "room_id": space_id}],
            }
        else:
            want = {"join_rule": "public"}
            if join == "space":
                self.add(label, f"room version {version} can't limit joining to the space: public")
        current = content_of(state, "m.room.join_rules")
        if current != want:
            self._set(label, room_id, "m.room.join_rules", want, "join rule " + describe(current, want))

    def _simple(self, label: str, room_id: str, state: State, history: str, guests: str) -> None:
        for event_type, key, value in (
            ("m.room.history_visibility", "history_visibility", history),
            ("m.room.guest_access", "guest_access", guests),
        ):
            current = content_of(state, event_type).get(key)
            if current != value:
                self._set(label, room_id, event_type, {key: value}, f"{key} " + describe(current, value))

    def _levels(self, label: str, room_id: str, state: State, levels: dict[str, Any], people: dict[str, int]) -> None:
        if level_of(state, self.me) < ADMIN:
            self.add(label, "I'm not at level 100 here: power levels left alone")
            return
        current = content_of(state, "m.room.power_levels")
        new, notes = wanted_levels(current, levels, people, state, self.me, lower_unlisted=self.config.lower_unlisted)
        for note in notes:
            self.add(label, note)
        diff = level_diff(current, new)
        if diff:
            self._set(label, room_id, "m.room.power_levels", new, "power levels: " + ", ".join(diff))

    def _directory(self, label: str, room_id: str, *, listed: bool) -> None:
        want = "public" if listed else "private"
        current = self.server.directory(room_id)
        if current != want:
            self.add(label, f"room directory {current} -> {want}", lambda: self.server.set_directory(room_id, want))

    def _shown(self, room_id: str, event: dict[str, Any]) -> str | None:
        """Return a message's HTML as clients show it: its latest edit's, else its own."""
        edit = event.get("unsigned", {}).get("m.relations", {}).get("m.replace")
        if not edit:
            return event.get("content", {}).get("formatted_body")
        if "content" not in edit:  # a server that bundles only the edit's ID
            edit = self.server.event(room_id, edit["event_id"])
        return edit.get("content", {}).get("m.new_content", {}).get("formatted_body")

    def _pin(self, label: str, room_id: str, state: State, room: Room) -> None:
        """Keep the room's text pinned exactly once: posted, edited in place when it changed, copies unpinned.

        The tool's messages carry PIN_KEY with the room's key; pins without it (pinned by people) stay, in
        their order. Matrix only honours an edit by the message's own sender, so a text another account posted
        (by hand, or before the tool ran as its own account) is posted anew in its place.
        """
        assert room.pin is not None  # noqa: S101  # the caller checked
        text = {
            "msgtype": "m.text",
            "body": markdown.to_plain(room.pin),
            "format": "org.matrix.custom.html",
            "formatted_body": markdown.to_html(room.pin),
        }
        pinned = content_of(state, "m.room.pinned_events").get("pinned", [])
        events = {e: self.server.event(room_id, e) for e in pinned}
        ours = [e for e in pinned if events[e].get("content", {}).get(PIN_KEY) == room.key]
        keep = next((e for e in ours if events[e].get("sender") == self.me), None)
        unpin = [e for e in ours if e != keep]
        first = room.pin.split("\n", 1)[0][:60]
        if keep is not None:
            if self._shown(room_id, events[keep]) != text["formatted_body"]:
                edit = {
                    **text,
                    "body": "* " + text["body"],
                    "formatted_body": "* " + text["formatted_body"],
                    "m.new_content": text,
                    "m.relates_to": {"rel_type": "m.replace", "event_id": keep},
                }
                self.add(label, f"edit the pinned text in place: {first}...", lambda: self.server.send(room_id, edit))
            if unpin:
                rest = [e for e in pinned if e not in unpin]
                self.add(
                    label,
                    f"unpin {len(unpin)} other copy/copies of the pinned text",
                    lambda: self.server.put_state(room_id, "m.room.pinned_events", "", {"pinned": rest}),
                )
            return
        at = pinned.index(ours[0]) if ours else 0

        def post_and_pin() -> None:
            event_id = self.server.send(room_id, {**text, PIN_KEY: room.key})
            rest = [e for e in pinned if e not in unpin]
            rest.insert(min(at, len(rest)), event_id)
            self.server.put_state(room_id, "m.room.pinned_events", "", {"pinned": rest})

        what = "post the pinned text anew in place of another account's" if ours else "post and pin"
        self.add(label, f"{what}: {first}...", post_and_pin)

    def _space(
        self, space: Space, space_id: str, state: State, planned: set[str], candidates: dict[str, State]
    ) -> None:
        for event_type, key, value in (
            ("m.room.name", "name", space.name),
            ("m.room.topic", "topic", space.topic),
            ("m.room.history_visibility", "history_visibility", space.history_visibility),
            ("m.room.guest_access", "guest_access", space.guest_access),
        ):
            current = content_of(state, event_type).get(key)
            if current != value and not (key == "name" and self.options.keep_names):
                self._set("space", space_id, event_type, {key: value}, f"{key} " + describe(current, value))
        if space.icon and not self.options.skip_icons:
            self._icon("space", space_id, state, space.icon)
        if content_of(state, "m.room.join_rules").get("join_rule") != space.join:
            self._set("space", space_id, "m.room.join_rules", {"join_rule": space.join}, f"join rule -> {space.join}")
        self._levels("space", space_id, state, {}, self.config.people)
        if self.options.directory:
            self._directory("space", space_id, listed=space.listed)
        for room_id in [k for (t, k), e in state.items() if t == "m.space.child" and e["content"].get("via")]:
            if room_id not in planned:
                name = content_of(candidates.get(room_id, {}), "m.room.name").get("name", "?")
                self.add("space", f"room {name} ({room_id}) isn't in the settings: left alone")
