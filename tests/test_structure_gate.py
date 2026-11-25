import unittest

from podcaster import writers
from podcaster.script_format import Turn, outline, parse_script, structure_check
from podcaster.writers import mlx_lm

GOOD = """---
date: 2026-01-01
episode: T
---
# spine: does the plan survive the record?
# q: what did the plan say
# q: what did the record show
# q: what changes now
[S1] The plan said three a week.
[S2] The record shows one.
[S1] So what changes now?
[S2] The plan, not the record.
"""


def turns(*pairs: tuple[str, str]) -> list[Turn]:
    return [Turn(s, t) for s, t in pairs]


class Outline(unittest.TestCase):
    def test_reads_q_lines_in_order(self):
        self.assertEqual(outline(GOOD), ["what did the plan say", "what did the record show", "what changes now"])

    def test_no_q_lines_is_empty(self):
        self.assertEqual(outline("# spine: x\n[S1] a\n[S2] b\n"), [])

    def test_q_lines_are_not_turns(self):
        self.assertEqual(len(parse_script(GOOD)), 4)


class StructureCheck(unittest.TestCase):
    def test_good_script_passes(self):
        self.assertEqual(structure_check(parse_script(GOOD), outline(GOOD), {}), [])

    def test_missing_outline_fails_unless_disabled(self):
        t = parse_script(GOOD)
        self.assertIn("no outline", structure_check(t, [], {})[0])
        self.assertEqual(structure_check(t, [], {"require_outline": False}), [])

    def test_missing_speaker_and_imbalance(self):
        t = turns(("S1", "a b"), ("S1", "c d"), ("S1", "e f"))
        fails = structure_check(t, ["q"], {})
        self.assertTrue(any("S2 never speaks" in f for f in fails))
        t = turns(*([("S1", "a")] * 9), ("S2", "b"))
        fails = structure_check(t, ["q"], {"min_turn_share": 0.3})
        self.assertTrue(any("S2 has 10%" in f for f in fails), fails)

    def test_long_turn_names_its_number(self):
        t = turns(("S1", "w " * 130), ("S2", "short"))
        fails = structure_check(t, ["q"], {"max_turn_words": 120})
        self.assertEqual(len(fails), 1)
        self.assertIn("turn 1 (S1) is 130 words", fails[0])

    def test_banned_artifact_is_case_insensitive(self):
        t = turns(("S1", "As an AI I cannot."), ("S2", "ok"))
        self.assertTrue(any("'as an ai'" in f for f in structure_check(t, ["q"], {})))


class Brief(unittest.TestCase):
    def test_renders_budget_structure_focus_and_outline_rule(self):
        text = writers.brief({"structure": ["open", "close"], "focus": "dwell on the third source"}, 1500)
        self.assertIn("about 1500", text)
        self.assertIn("  1. open\n  2. close", text)
        self.assertIn("Focus for this episode: dwell on the third source", text)
        self.assertIn("`# q:`", text)

    def test_omits_empty_structure_and_focus(self):
        text = writers.brief({}, 100)
        self.assertNotIn("Structure", text)
        self.assertNotIn("Focus", text)


class LocalWriterHeader(unittest.TestCase):
    def test_header_keeps_spine_and_q_lines_and_drops_the_rest(self):
        raw = "<think>hm</think>\n# spine: the question?\n# q: first\nchatter\n# q: second\n"
        self.assertEqual(mlx_lm.clean_header(raw), "# spine: the question?\n# q: first\n# q: second")

    def test_header_without_q_lines_is_just_the_spine(self):
        self.assertEqual(mlx_lm.clean_header("just a question"), "# spine: just a question")


if __name__ == "__main__":
    unittest.main()
