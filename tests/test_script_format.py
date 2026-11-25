import unittest
from pathlib import Path

from podcaster import paths
from podcaster.script_format import (
    ScriptError,
    parse_script,
    read_script,
    sources_from_meta,
    strip_frontmatter,
    word_count,
)

FIXTURE = str(paths.FIXTURE)


class ParseScript(unittest.TestCase):
    def test_fixture_parses_six_turns_alternating(self):
        meta, turns = read_script(FIXTURE)
        self.assertEqual(meta["episode"], "fixture")
        self.assertEqual(len(turns), 6)
        self.assertEqual([t.speaker for t in turns], ["S1", "S2"] * 3)
        self.assertTrue(turns[0].text.startswith("The Little Pell"))

    def test_untagged_line_reports_its_number(self):
        text = "[S1] fine\n[S2] fine\nthis line has no tag\n"
        with self.assertRaises(ScriptError) as ctx:
            parse_script(text)
        self.assertIn("line 3", str(ctx.exception))

    def test_frontmatter_offsets_line_numbers(self):
        text = "---\ndate: x\n---\n[S1] fine\nbad\n"
        with self.assertRaises(ScriptError) as ctx:
            parse_script(text)
        self.assertIn("line 5", str(ctx.exception))

    def test_blank_and_comment_lines_are_skipped(self):
        turns = parse_script("\n# note\n[S1] one\n\n[S2] two\n")
        self.assertEqual(len(turns), 2)

    def test_unclosed_frontmatter_is_an_error(self):
        with self.assertRaises(ScriptError):
            strip_frontmatter("---\ndate: x\n[S1] never closed\n")

    def test_empty_script_is_an_error(self):
        with self.assertRaises(ScriptError):
            parse_script("\n\n")

    def test_word_count(self):
        turns = parse_script("[S1] one two three\n[S2] four five\n")
        self.assertEqual(word_count(turns), 5)


class Sources(unittest.TestCase):
    def test_comma_separated_sources_become_paths_in_order(self):
        meta = {"sources": "/a/one.md, /a/two.md ,, /a/three.md"}
        self.assertEqual(
            sources_from_meta(meta),
            [Path("/a/one.md"), Path("/a/two.md"), Path("/a/three.md")],
        )

    def test_absent_or_empty_sources_gives_an_empty_list(self):
        self.assertEqual(sources_from_meta({}), [])
        self.assertEqual(sources_from_meta({"sources": "  "}), [])


class ArgvSafety(unittest.TestCase):
    """A turn reaches an engine as an argv element, and `say` reads a leading dash as an option."""

    def test_a_turn_starting_with_a_dash_is_refused(self):
        with self.assertRaises(ScriptError) as ctx:
            parse_script("[S1] -o /tmp/anything\n")
        self.assertIn("option", str(ctx.exception))

    def test_the_line_number_is_named(self):
        with self.assertRaises(ScriptError) as ctx:
            parse_script("[S1] a normal turn of words here.\n[S2] --version\n")
        self.assertIn("line 2", str(ctx.exception))

    def test_a_dash_inside_a_turn_is_fine(self):
        turns = parse_script("[S1] a spaced - hyphen mid sentence is normal prose.\n")
        self.assertEqual(len(turns), 1)


if __name__ == "__main__":
    unittest.main()
