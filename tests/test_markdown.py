# SPDX-License-Identifier: Unlicense
"""The pinned texts' Markdown becomes Matrix's HTML."""

from __future__ import annotations

import unittest

from matrix_space.markdown import to_html, to_plain


class Markdown(unittest.TestCase):
    def test_paragraphs_lists_bold_and_links(self):
        text = "Hi **there**.\n\n- one\n- see https://example.org/a.\n\n1. first\n2. second"
        self.assertEqual(
            to_html(text),
            "<p>Hi <strong>there</strong>.</p>"
            '<ul><li>one</li><li>see <a href="https://example.org/a">https://example.org/a</a>.</li></ul>'
            "<ol><li>first</li><li>second</li></ol>",
        )

    def test_html_in_the_text_is_escaped(self):
        self.assertEqual(to_html("a <b> & c"), "<p>a &lt;b&gt; &amp; c</p>")

    def test_plain_text_drops_the_bold_marks(self):
        self.assertEqual(to_plain("**Rules** apply"), "Rules apply")


if __name__ == "__main__":
    unittest.main()
