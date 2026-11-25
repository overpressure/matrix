# SPDX-License-Identifier: Unlicense
"""The few Matrix Client-Server API calls the tool needs, with rate limits retried.

The tests stand in for this class with a fake server (tests/fake.py) that has the same methods.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: HTTP status codes the client handles itself.
NOT_FOUND = 404
RATE_LIMITED = 429


class MatrixError(Exception):
    """An API call failed."""


def q(text: str) -> str:
    """Quote @p text for a URL path segment."""
    return urllib.parse.quote(text, safe="")


class Api:
    """A client for one account on one homeserver."""

    def __init__(self, base: str, token: str) -> None:
        """Talk to the client API at @p base as the account whose access token is @p token."""
        self.base = base.rstrip("/")
        self._token = token
        self.txn = int(time.time() * 1000)

    def call(  # noqa: PLR0913  # one request's parts
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        missing_ok: bool = False,
        raw: bytes | None = None,
        content_type: str = "application/json",
        prefix: str = "/_matrix/client/v3",
    ) -> Any:  # noqa: ANN401  # the endpoint's JSON: an object, or a list for a room's state
        """Make one request and return its JSON answer; {} for a 404 when @p missing_ok."""
        url = self.base + prefix + path
        data = raw if raw is not None else None if body is None else json.dumps(body).encode()
        for _ in range(10):
            request = urllib.request.Request(url, data=data, method=method)  # noqa: S310  # the configured https homeserver
            request.add_header("Authorization", "Bearer " + self._token)
            if data is not None:
                request.add_header("Content-Type", content_type)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # as above
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as e:
                payload = e.read()
                detail: dict[str, Any]
                try:
                    detail = json.loads(payload)
                except ValueError:
                    detail = {"error": payload[:200].decode(errors="replace")}
                if e.code == RATE_LIMITED:
                    time.sleep(detail.get("retry_after_ms", 2000) / 1000)
                    continue
                if e.code == NOT_FOUND and missing_ok:
                    return {}
                message = f"{method} {path}: {e.code} {detail.get('errcode', '')} {detail.get('error', '')}"
                raise MatrixError(message) from None
            except (urllib.error.URLError, OSError) as e:  # no network, DNS, TLS, timeout
                message = f"{method} {path}: {getattr(e, 'reason', e)}"
                raise MatrixError(message) from None
        message = f"{method} {path}: still rate-limited"
        raise MatrixError(message)

    def whoami(self) -> str:
        """Return the account's user ID."""
        return self.call("GET", "/account/whoami")["user_id"]

    def room_id(self, alias: str) -> str | None:
        """Return the room an address points to, or None."""
        return self.call("GET", "/directory/room/" + q(alias), missing_ok=True).get("room_id")

    def set_alias(self, alias: str, room_id: str) -> None:
        """Point the address @p alias at @p room_id."""
        self.call("PUT", "/directory/room/" + q(alias), {"room_id": room_id})

    def joined_rooms(self) -> list[str]:
        """Return the rooms the account is in."""
        return self.call("GET", "/joined_rooms").get("joined_rooms", [])

    def state(self, room_id: str) -> dict[tuple[str, str], dict[str, Any]]:
        """Return a room's state events by (type, state key)."""
        events = self.call("GET", f"/rooms/{q(room_id)}/state")
        return {(e["type"], e.get("state_key", "")): e for e in events}

    def put_state(self, room_id: str, event_type: str, state_key: str, content: dict[str, Any]) -> None:
        """Set one state event."""
        self.call("PUT", f"/rooms/{q(room_id)}/state/{q(event_type)}/{q(state_key)}", content)

    def create_room(self, body: dict[str, Any]) -> str:
        """Create a room and return its ID."""
        return self.call("POST", "/createRoom", body)["room_id"]

    def upload(self, data: bytes, filename: str) -> str:
        """Upload a PNG to the media repository and return its mxc:// address."""
        path = "/upload?filename=" + q(filename)
        return self.call("POST", path, raw=data, content_type="image/png", prefix="/_matrix/media/v3")["content_uri"]

    def send(self, room_id: str, content: dict[str, Any]) -> str:
        """Post a message and return its event ID."""
        self.txn += 1
        return self.call("PUT", f"/rooms/{q(room_id)}/send/m.room.message/{self.txn}", content)["event_id"]

    def event(self, room_id: str, event_id: str) -> dict[str, Any]:
        """Return one event, or {} when it's gone."""
        return self.call("GET", f"/rooms/{q(room_id)}/event/{q(event_id)}", missing_ok=True)

    def messages(self, room_id: str, limit: int) -> list[dict[str, Any]]:
        """Return up to @p limit of the room's latest messages, newest first."""
        found: list[dict[str, Any]] = []
        start = None
        while len(found) < limit:
            path = f"/rooms/{q(room_id)}/messages?dir=b&limit={min(100, limit - len(found))}"
            if start:
                path += "&from=" + q(start)
            page = self.call("GET", path)
            found += page.get("chunk", [])
            start = page.get("end")
            if not page.get("chunk") or not start:
                break
        return found

    def directory(self, room_id: str) -> str | None:
        """Return whether a room is in the server's public directory: "public" or "private"."""
        return self.call("GET", f"/directory/list/room/{q(room_id)}").get("visibility")

    def set_directory(self, room_id: str, visibility: str) -> None:
        """List a room in the server's public directory ("public") or take it out ("private")."""
        self.call("PUT", f"/directory/list/room/{q(room_id)}", {"visibility": visibility})
