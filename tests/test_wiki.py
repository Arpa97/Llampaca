"""
Tests for the personal wiki: core file logic (llampaca/wiki.py) and the
agent tools built on top of it (llampaca/tools/wiki.py). Everything here
is offline — no llama-server involved. Every call passes an explicit
tmp-dir wiki_dir override, so the user's real ~/.llampaca/wiki is never
touched (same pattern as the db_path override in the database tests).
"""

import json
import tempfile
import unittest
from pathlib import Path

from llampaca.wiki import (
    INDEX_DESCRIPTION_CHARS,
    MAX_INDEX_PAGES,
    MAX_PAGE_CHARS,
    list_pages,
    page_path,
    read_page,
    render_index,
    slugify,
    write_page,
)
from llampaca.tools import ToolRegistry
from llampaca.tools.wiki import register_wiki_tools


class WikiDirTestCase(unittest.TestCase):
    """Base: a fresh temporary wiki directory per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class TestSlugify(unittest.TestCase):
    def test_normalizes_case_spaces_and_extension(self):
        self.assertEqual(slugify("User Preferences"), "user-preferences")
        self.assertEqual(slugify("  notes.md "), "notes")
        self.assertEqual(slugify("a_b__c"), "a-b-c")

    def test_traversal_degrades_to_a_safe_name(self):
        # The output alphabet is [a-z0-9-]: separators and dots cannot
        # survive, so any traversal attempt becomes a plain wiki name.
        self.assertEqual(slugify("../../etc/passwd"), "etc-passwd")
        self.assertEqual(slugify("/absolute/path"), "absolute-path")

    def test_rejects_names_with_nothing_usable(self):
        for bad in ("", "   ", "..", "///", "!!!"):
            with self.assertRaises(ValueError):
                slugify(bad)

    def test_page_path_stays_inside_the_wiki_dir(self):
        # resolve() both sides: on macOS /tmp itself is a symlink.
        wiki_dir = Path("/tmp/wiki")
        resolved = page_path("../../etc/passwd", wiki_dir).resolve()
        self.assertTrue(resolved.is_relative_to(wiki_dir.resolve()))


class TestPageCrud(WikiDirTestCase):
    def test_write_then_read_roundtrip(self):
        written = write_page("User Preferences", "# Prefs\n\nUses conda.", self.wiki_dir)
        self.assertEqual(written, "user-preferences")
        self.assertEqual(
            read_page("user-preferences", self.wiki_dir),
            "# Prefs\n\nUses conda.",
        )
        # The original (un-slugified) name reads the same page.
        self.assertEqual(
            read_page("User Preferences", self.wiki_dir),
            "# Prefs\n\nUses conda.",
        )

    def test_write_overwrites_whole_page(self):
        write_page("notes", "old content", self.wiki_dir)
        write_page("notes", "new content", self.wiki_dir)
        self.assertEqual(read_page("notes", self.wiki_dir), "new content")

    def test_read_missing_page_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_page("nope", self.wiki_dir)

    def test_write_rejects_empty_content(self):
        with self.assertRaises(ValueError):
            write_page("notes", "   \n  ", self.wiki_dir)

    def test_write_rejects_oversized_content(self):
        with self.assertRaises(ValueError):
            write_page("big", "x" * (MAX_PAGE_CHARS + 1), self.wiki_dir)
        # At exactly the cap it must still work.
        write_page("big", "x" * MAX_PAGE_CHARS, self.wiki_dir)

    def test_list_pages_sorted_with_descriptions(self):
        write_page("zeta", "plain first line\nmore", self.wiki_dir)
        write_page("alpha", "## Heading description\n\nbody", self.wiki_dir)
        pages = list_pages(self.wiki_dir)
        self.assertEqual(
            pages,
            [("alpha", "Heading description"), ("zeta", "plain first line")],
        )

    def test_list_pages_truncates_long_descriptions(self):
        write_page("long", "# " + "d" * 300, self.wiki_dir)
        [(_, description)] = list_pages(self.wiki_dir)
        self.assertEqual(len(description), INDEX_DESCRIPTION_CHARS)
        self.assertTrue(description.endswith("…"))

    def test_list_pages_missing_dir_is_empty(self):
        self.assertEqual(list_pages(self.wiki_dir / "missing"), [])


class TestRenderIndex(WikiDirTestCase):
    def test_empty_wiki_says_so(self):
        index = render_index(self.wiki_dir)
        self.assertIn("empty", index)
        self.assertIn("update_wiki_page", index)

    def test_lists_pages_one_per_line(self):
        write_page("prefs", "# User preferences", self.wiki_dir)
        write_page("projects", "# Ongoing projects", self.wiki_dir)
        index = render_index(self.wiki_dir)
        self.assertIn("- prefs: User preferences", index)
        self.assertIn("- projects: Ongoing projects", index)
        self.assertNotIn("empty", index)

    def test_caps_the_number_of_listed_pages(self):
        for i in range(MAX_INDEX_PAGES + 5):
            write_page(f"page-{i:03d}", f"# Page {i}", self.wiki_dir)
        index = render_index(self.wiki_dir)
        listed = [l for l in index.splitlines() if l.startswith("- ")]
        self.assertEqual(len(listed), MAX_INDEX_PAGES)
        self.assertIn("5 more pages", index)


class TestWikiTools(WikiDirTestCase):
    def setUp(self):
        super().setUp()
        self.registry = ToolRegistry()
        register_wiki_tools(self.registry, wiki_dir=self.wiki_dir)

    def test_registration_and_confirmation_flags(self):
        self.assertEqual(
            set(self.registry.names()),
            {"read_wiki_page", "update_wiki_page"},
        )
        # Reading memory is safe; writing it is confirmation-gated like
        # every other write in the codebase.
        self.assertFalse(self.registry.get("read_wiki_page").requires_confirmation)
        self.assertTrue(self.registry.get("update_wiki_page").requires_confirmation)

    def test_update_then_read_through_the_registry(self):
        result = self.registry.execute(
            "update_wiki_page",
            json.dumps({"name": "User Prefs", "content": "# Prefs\nConda."}),
        )
        # The result echoes the slugified name the model must reuse.
        self.assertIn("user-prefs", result)
        read_back = self.registry.execute(
            "read_wiki_page", json.dumps({"name": "user-prefs"})
        )
        self.assertEqual(read_back, "# Prefs\nConda.")

    def test_read_missing_page_lists_existing_ones(self):
        write_page("prefs", "# Prefs", self.wiki_dir)
        result = self.registry.execute(
            "read_wiki_page", json.dumps({"name": "nope"})
        )
        self.assertIn("Error", result)
        self.assertIn("prefs", result)

    def test_read_missing_page_on_empty_wiki(self):
        result = self.registry.execute(
            "read_wiki_page", json.dumps({"name": "nope"})
        )
        self.assertIn("no pages yet", result)

    def test_errors_come_back_as_strings(self):
        # Invalid name, empty content, oversized content: all must return
        # a readable error string, never raise out of the registry.
        for args in (
            {"name": "!!!", "content": "x"},
            {"name": "ok", "content": "  "},
            {"name": "ok", "content": "x" * (MAX_PAGE_CHARS + 1)},
        ):
            result = self.registry.execute("update_wiki_page", json.dumps(args))
            self.assertIn("Error", result)
        self.assertEqual(list_pages(self.wiki_dir), [])


if __name__ == "__main__":
    unittest.main()
