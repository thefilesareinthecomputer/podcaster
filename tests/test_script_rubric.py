import unittest

from podcaster import paths
from podcaster.script_rubric import Finding, check, check_address, check_turn_length, format_report, turns_with_lines

FIXTURE = str(paths.FIXTURE)

FRONTMATTER = "---\ndate: 2026-09-19\nepisode: rubric-test\nwords: 120\n---\n"
SPINE = "# spine: when the volunteer gauge and the county sensor disagree, which number does the town act on?\n"
# The frontmatter is five lines and the spine comment is the sixth, so turn N sits on line N + 6.
FIRST_TURN_LINE = 7

# 29 words, inside the band, no pronouns at all.
CLEAN_A = (
    "The Little Pell volunteer station has logged rain by hand since nineteen sixty-one. "
    "The county sensor, four hundred feet away, has logged it by wire since two thousand nine."
)
# 30 words, inside the band.
CLEAN_B = (
    "Last month the two instruments disagreed by one and three tenths inches. "
    "Four hundred feet apart is nothing in a rainstorm, so one of those instruments must have been broken."
)
# 12 words, under the band.
SHORT_TURN = "The gauge sat on the fence post for four decades without complaint."
# 60 words, over the band.
LONG_TURN = (
    "The tipping bucket on the county unit had a wasp nest packed into the funnel for two "
    "full seasons, and every reading it sent to the state carried a quality flag that nobody "
    "in the office opened, so the town argued for two budget cycles about a retired "
    "schoolteacher and her graduated cylinder while the wired instrument quietly reported nothing."
)


def build(turns, spine=True):
    head = FRONTMATTER + (SPINE if spine else "")
    body = "".join(f"[S{1 + i % 2}] {t}\n" for i, t in enumerate(turns))
    return head + body


class WordCounts(unittest.TestCase):
    """The fixtures above only mean anything if their lengths are what the comments claim."""

    def test_fixture_turn_lengths(self):
        self.assertEqual(len(CLEAN_A.split()), 29)
        self.assertEqual(len(CLEAN_B.split()), 30)
        self.assertEqual(len(SHORT_TURN.split()), 12)
        self.assertEqual(len(LONG_TURN.split()), 60)


class Spine(unittest.TestCase):
    def test_spine_present_is_not_flagged(self):
        self.assertEqual([f for f in check(build([CLEAN_A, CLEAN_B])) if f.axis == "spine"], [])

    def test_missing_spine_is_flagged_on_the_line_after_the_frontmatter(self):
        findings = [f for f in check(build([CLEAN_A, CLEAN_B], spine=False)) if f.axis == "spine"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, 6)
        self.assertIn("no # spine: line", findings[0].message)

    def test_spine_after_the_first_turn_does_not_count(self):
        text = FRONTMATTER + f"[S1] {CLEAN_A}\n" + SPINE + f"[S2] {CLEAN_B}\n"
        self.assertEqual([f.axis for f in check(text)], ["spine"])

    def test_spine_without_frontmatter_is_flagged_on_line_one(self):
        findings = [f for f in check(f"[S1] {CLEAN_A}\n[S2] {CLEAN_B}\n") if f.axis == "spine"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, 1)


class TurnLength(unittest.TestCase):
    def test_short_turn_is_flagged_with_its_line_and_count(self):
        findings = [f for f in check(build([SHORT_TURN, CLEAN_B])) if f.axis == "turn_length"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, FIRST_TURN_LINE)
        self.assertIn("12 words", findings[0].message)
        self.assertIn("20 to 45", findings[0].message)

    def test_long_turn_is_flagged_with_its_line_and_count(self):
        findings = [f for f in check(build([CLEAN_A, LONG_TURN])) if f.axis == "turn_length"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, FIRST_TURN_LINE + 1)
        self.assertIn("60 words", findings[0].message)

    def test_thirty_word_turn_is_not_flagged(self):
        self.assertEqual([f for f in check(build([CLEAN_B])) if f.axis == "turn_length"], [])

    def test_bounds_are_inclusive(self):
        findings = check_turn_length(turns_with_lines(build([CLEAN_B])), low=30, high=30)
        self.assertEqual(findings, [])


class Address(unittest.TestCase):
    MID_SENTENCE = (
        "The record is plain enough. You said the gauge was wrong and he never checked it. "
        "The county filed that reading as fact and nobody walked out to the fence post."
    )
    YOU_TURN = (
        "You logged one and three tenths inches on Tuesday morning, in pencil, in a log book "
        "that has held the same column headings since the Eisenhower administration."
    )
    HE_TURN = (
        "He wrote that the tipping bucket looked clean from the ground. From the ground it "
        "always does, which is the whole trouble with a funnel mounted ten feet up a pole."
    )
    CO_HOST_YOU = (
        "You're reading that as the wasp nest talking, and I am reading it as two seasons of "
        "nobody opening the quality flags. Both of those can be true at the same time."
    )

    def test_mid_sentence_switch_is_flagged_with_its_line(self):
        findings = [f for f in check(build([self.MID_SENTENCE])) if f.axis == "address"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, FIRST_TURN_LINE)
        self.assertIn("You said the gauge was wrong and he never checked it.", findings[0].message)

    def test_switch_at_a_turn_boundary_is_not_flagged(self):
        self.assertEqual([f for f in check(build([self.YOU_TURN, self.HE_TURN])) if f.axis == "address"], [])

    def test_two_modes_in_one_turn_at_sentence_boundaries_is_not_flagged(self):
        text = build([f"{self.YOU_TURN} {self.HE_TURN}"])
        self.assertEqual([f for f in check(text, {"turn_length": 0.0}) if f.axis == "address"], [])

    def test_you_to_the_co_host_without_he_is_not_flagged(self):
        self.assertEqual([f for f in check(build([self.CO_HOST_YOU])) if f.axis == "address"], [])

    def test_she_and_they_do_not_trip_the_heuristic(self):
        turn = (
            "You can see it in her monthly totals. She matched the state model within a tenth "
            "of an inch, and they filed her sheet without so much as a phone call about it."
        )
        self.assertEqual(check_address(turns_with_lines(build([turn]))), [])

    def test_his_inside_a_longer_word_is_not_a_pronoun(self):
        turn = (
            "You would think this history settles it, but the whisper in the budget meeting was "
            "that the volunteer sheet is a hobby and the wired sensor is an instrument of record."
        )
        self.assertEqual(check_address(turns_with_lines(build([turn]))), [])


class Weights(unittest.TestCase):
    def test_zero_weight_skips_turn_length(self):
        text = build([CLEAN_A, LONG_TURN])
        self.assertTrue([f for f in check(text) if f.axis == "turn_length"])
        self.assertEqual(check(text, {"turn_length": 0.0}), [])

    def test_zero_weight_skips_address(self):
        text = build([Address.MID_SENTENCE])
        self.assertTrue([f for f in check(text) if f.axis == "address"])
        self.assertEqual(check(text, {"address": 0.0}), [])

    def test_zero_weight_skips_spine(self):
        text = build([CLEAN_A, CLEAN_B], spine=False)
        self.assertTrue([f for f in check(text) if f.axis == "spine"])
        self.assertEqual(check(text, {"spine": 0.0}), [])

    def test_a_partial_weights_dict_leaves_the_other_axes_on(self):
        text = build([SHORT_TURN], spine=False)
        self.assertEqual({f.axis for f in check(text, {"address": 0.0})}, {"spine", "turn_length"})


class CleanScripts(unittest.TestCase):
    def test_clean_synthetic_script_has_no_findings(self):
        self.assertEqual(check(build([CLEAN_A, CLEAN_B, Address.YOU_TURN, Address.HE_TURN])), [])

    def test_repo_fixture_passes_every_axis(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            self.assertEqual(check(fh.read()), [])


class Reporting(unittest.TestCase):
    def test_finding_as_dict_matches_the_slop_linter_shape(self):
        self.assertEqual(
            Finding(line=7, axis="turn_length", message="turn is 12 words").as_dict(),
            {"line": 7, "category": "turn_length", "message": "turn is 12 words"},
        )

    def test_report_counts_each_axis_and_lists_every_finding(self):
        findings = check(build([SHORT_TURN, Address.MID_SENTENCE], spine=False))
        report = format_report(findings).splitlines()
        self.assertEqual(len(report), 4)
        self.assertEqual(report[0], "rubric: 3 finding(s); spine 1, turn_length 1, address 1")
        # Without the spine line the first turn is line 6, so spine and turn_length share it
        # and the sort puts the axes in rubric order within one line.
        self.assertTrue(report[1].startswith("  6 [spine] no # spine: line"))
        self.assertTrue(report[2].startswith("  6 [turn_length] turn is 12 words"))
        self.assertTrue(report[3].startswith("  7 [address] address switches mid-sentence"))

    def test_report_of_no_findings_is_the_header_alone(self):
        self.assertEqual(format_report([]), "rubric: 0 finding(s); spine 0, turn_length 0, address 0")


class LineNumbers(unittest.TestCase):
    def test_turns_carry_the_file_line_including_frontmatter(self):
        pairs = turns_with_lines(build([CLEAN_A, CLEAN_B]))
        self.assertEqual([line for line, _ in pairs], [FIRST_TURN_LINE, FIRST_TURN_LINE + 1])
        self.assertEqual([t.speaker for _, t in pairs], ["S1", "S2"])

    def test_turns_without_frontmatter_start_at_line_one(self):
        pairs = turns_with_lines(f"[S1] {CLEAN_A}\n[S2] {CLEAN_B}\n")
        self.assertEqual([line for line, _ in pairs], [1, 2])


if __name__ == "__main__":
    unittest.main()
