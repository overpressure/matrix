# SPDX-License-Identifier: Unlicense
"""A Matrix server in memory, with the methods matrix_space.api.Api has: enough for the planner."""

from __future__ import annotations

import copy
import itertools
from typing import Any

from matrix_space.api import MatrixError

State = dict[tuple[str, str], dict[str, Any]]


class FakeServer:
    """Rooms, their state and messages, addresses, and what was uploaded."""

    def __init__(self, me: str = "@bot:matrix.org", server: str = "matrix.org") -> None:
        self.me = me
        self.server = server
        self.rooms: dict[str, State] = {}
        self.aliases: dict[str, str] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self.joined: list[str] = []
        self.listed: dict[str, str] = {}
        self.uploads: list[bytes] = []
        self.calls: list[str] = []
        self._ids = itertools.count(1)

    # Setting a server up.

    def add_room(  # noqa: PLR0913  # what a test sets up
        self,
        name: str,
        *,
        alias: str | None = None,
        space: bool = False,
        version: str = "11",
        creator: str | None = None,
        levels: dict[str, Any] | None = None,
    ) -> str:
        room_id = f"!{next(self._ids)}:{self.server}"
        content: dict[str, Any] = {"room_version": version}
        if space:
            content["type"] = "m.space"
        self.rooms[room_id] = {}
        self._put(room_id, "m.room.create", "", content, sender=creator or self.me)
        self._put(room_id, "m.room.name", "", {"name": name})
        self._put(
            room_id,
            "m.room.power_levels",
            "",
            {"users": {self.me: 100}, "users_default": 0} if levels is None else levels,
        )
        if alias:
            self.aliases[alias] = room_id
        self.joined.append(room_id)
        return room_id

    def _put(self, room_id: str, event_type: str, key: str, content: dict[str, Any], sender: str | None = None) -> None:
        self.rooms[room_id][(event_type, key)] = {
            "type": event_type,
            "state_key": key,
            "sender": sender or self.me,
            "content": copy.deepcopy(content),
        }

    def content(self, room_id: str, event_type: str, key: str = "") -> dict[str, Any]:
        event = self.rooms[room_id].get((event_type, key))
        return event["content"] if event else {}

    # The API.

    def whoami(self) -> str:
        return self.me

    def room_id(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    def set_alias(self, alias: str, room_id: str) -> None:
        self.calls.append(f"alias {alias}")
        self.aliases[alias] = room_id

    def joined_rooms(self) -> list[str]:
        return list(self.joined)

    def state(self, room_id: str) -> State:
        if room_id not in self.rooms:
            message = f"no room {room_id}"
            raise MatrixError(message)
        return copy.deepcopy(self.rooms[room_id])

    def put_state(self, room_id: str, event_type: str, state_key: str, content: dict[str, Any]) -> None:
        self.calls.append(f"state {room_id} {event_type} {state_key}".strip())
        self._put(room_id, event_type, state_key, content)

    def create_room(self, body: dict[str, Any]) -> str:
        self.calls.append(f"create {body['name']}")
        room_id = self.add_room(body["name"], alias=f"#{body['room_alias_name']}:{self.server}")
        self._put(room_id, "m.room.topic", "", {"topic": body["topic"]})
        return room_id

    def upload(self, data: bytes, filename: str) -> str:
        self.calls.append(f"upload {filename}")
        self.uploads.append(data)
        return f"mxc://{self.server}/{len(self.uploads)}"

    def send(self, room_id: str, content: dict[str, Any], sender: str | None = None) -> str:
        """Post a message; an edit (m.replace) is bundled with the message it edits, as servers do."""
        self.calls.append(f"send {room_id}")
        event_id = f"$e{next(self._ids)}"
        event = {
            "event_id": event_id,
            "room_id": room_id,
            "sender": sender or self.me,
            "content": copy.deepcopy(content),
        }
        self.events[event_id] = event
        relation = content.get("m.relates_to", {})
        if relation.get("rel_type") == "m.replace":
            target = self.events[relation["event_id"]]
            target.setdefault("unsigned", {}).setdefault("m.relations", {})["m.replace"] = copy.deepcopy(event)
        return event_id

    def event(self, room_id: str, event_id: str) -> dict[str, Any]:
        event = self.events.get(event_id)
        return copy.deepcopy(event) if event and event["room_id"] == room_id else {}

    def messages(self, room_id: str, limit: int) -> list[dict[str, Any]]:
        """Return the room's latest messages, newest first."""
        found = [copy.deepcopy(e) for e in self.events.values() if e["room_id"] == room_id]
        return found[::-1][:limit]

    def directory(self, room_id: str) -> str | None:
        return self.listed.get(room_id, "private")

    def set_directory(self, room_id: str, visibility: str) -> None:
        self.calls.append(f"directory {room_id} {visibility}")
        self.listed[room_id] = visibility
