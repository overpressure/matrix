# SPDX-License-Identifier: Unlicense
"""The few GitHub REST calls the announcer needs: an organization's repositories, a file, a repository's releases.

Everything it reads is public. GITHUB_TOKEN (the workflow's own) only raises the rate limit.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from matrix_space.api import NOT_FOUND

API = "https://api.github.com"


class GitHubError(Exception):
    """A GitHub API call failed."""


class GitHub:
    """A reader of public repositories."""

    def __init__(self, token: str | None = None) -> None:
        """Read anonymously, or with @p token for a higher rate limit."""
        self._token = token

    def _get(self, path: str, accept: str = "application/vnd.github+json") -> Any:  # noqa: ANN401  # the JSON
        request = urllib.request.Request(API + path)  # noqa: S310  # GitHub's https API
        request.add_header("Accept", accept)
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self._token:
            request.add_header("Authorization", "Bearer " + self._token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # as above
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            if e.code == NOT_FOUND:
                return None
            message = f"GET {path}: {e.code}"
            raise GitHubError(message) from None
        except (urllib.error.URLError, OSError) as e:
            message = f"GET {path}: {getattr(e, 'reason', e)}"
            raise GitHubError(message) from None

    def repositories(self, organization: str) -> list[dict[str, Any]]:
        """Return the organization's public repositories."""
        found: list[dict[str, Any]] = []
        for page in range(1, 11):
            batch = self._get(f"/orgs/{organization}/repos?type=public&per_page=100&page={page}") or []
            found += batch
            if len(batch) < 100:  # noqa: PLR2004  # a page's size
                break
        return found

    def json_file(self, repo: str, path: str) -> dict[str, Any] | None:
        """Return a JSON file of the repository's default branch, or None when there is none."""
        data = self._get(f"/repos/{repo}/contents/{path}", accept="application/vnd.github.raw+json")
        return data if isinstance(data, dict) else None

    def releases(self, repo: str) -> list[dict[str, Any]]:
        """Return the repository's latest releases, newest first."""
        return self._get(f"/repos/{repo}/releases?per_page=30") or []
