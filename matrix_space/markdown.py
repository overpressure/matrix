# SPDX-License-Identifier: Unlicense
"""The small Markdown the pinned texts use (paragraphs, lists, bold, links), as Matrix's HTML and plain text."""

from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"(https://[^\s<]+[^\s<.,;:])", r'<a href="\1">\1</a>', text)


def to_html(text: str) -> str:
    """Render @p text: blocks split by blank lines, a block of "- " lines a list, of "1. " lines a numbered one."""
    out = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        if all(line.startswith("- ") for line in lines):
            out.append("<ul>" + "".join(f"<li>{_inline(line[2:])}</li>" for line in lines) + "</ul>")
        elif all(re.match(r"\d+\. ", line) for line in lines):
            items = "".join(f"<li>{_inline(line.split('. ', 1)[1])}</li>" for line in lines)
            out.append("<ol>" + items + "</ol>")
        else:
            out.append("<p>" + "<br>".join(_inline(line) for line in lines) + "</p>")
    return "".join(out)


def to_plain(text: str) -> str:
    """Return @p text without its bold marks, for clients that show no HTML."""
    return text.replace("**", "")
