# SPDX-License-Identifier: Unlicense
"""The rooms' and the space's icons: overpressure/brand's matrix/<icon>.png, from GitHub or a checkout."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from matrix_space.api import NOT_FOUND, MatrixError

if TYPE_CHECKING:
    from pathlib import Path


class Icons:
    """Where the icons come from: a base address (config/space.yaml's icons) or a folder (--icons DIR)."""

    def __init__(self, base: str, folder: Path | None = None) -> None:
        """Read <base><icon>.png, or <folder>/<icon>.png when @p folder is given."""
        self.base = base
        self.folder = folder
        self._cache: dict[str, bytes | None] = {}

    def where(self, icon: str) -> str:
        """Return where @p icon is read from."""
        return str(self.folder / f"{icon}.png") if self.folder else f"{self.base}{icon}.png"

    def get(self, icon: str) -> bytes | None:
        """Return the PNG of @p icon, or None when there is none."""
        if icon not in self._cache:
            self._cache[icon] = self._read(icon)
        return self._cache[icon]

    def _read(self, icon: str) -> bytes | None:
        if self.folder is not None:
            path = self.folder / f"{icon}.png"
            return path.read_bytes() if path.is_file() else None
        url = self.where(icon)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310  # space.yaml's https address
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == NOT_FOUND:
                return None
            message = f"GET {url}: {e.code}"
            raise MatrixError(message) from None
        except (urllib.error.URLError, OSError) as e:
            message = f"GET {url}: {getattr(e, 'reason', e)}"
            raise MatrixError(message) from None
