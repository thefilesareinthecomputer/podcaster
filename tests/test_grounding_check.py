import unittest
from pathlib import Path

from podcaster.grounding_check import (
    any_source,
    extract_facts,
    format_report,
    normalize,
    source_text_from_input,
    spelled_number,
)
from podcaster import paths
from podcaster.modes import grounding
from podcaster.pipeline import build_writer_input, resolve_sources

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPT = paths.FIXTURE
SOURCE = FIXTURES / "short-source.md"

FRONTMATTER = "---\ndate: 2026-09-19\nepisode: grounding-test\nwords: 120\n---\n"
SPINE = "# spine: which number does the town act on?\n"
# The frontmatter is five lines and the spine comment is the sixth, so turn N sits on line N + 6.
FIRST_TURN_LINE = 7

# The accented form a pasted source can carry, written by code point so this file holds
# only keyboard characters. The script spells it plain, the way prompts/briefs/briefing.md asks.
ACCENTED_NAME = "Jos" + chr(0xE9) + " Ortiz-Vega"


def build(turns):
    body = "".join(f"[S{1 + i % 2}] {t}\n" for i, t in enumerate(turns))
    return FRONTMATTER + SPINE + body


def marked(text, name="source.md"):
    """A writer input without touching the disk, in build_writer_input's marker format."""
    return f"<<< source: {name} >>>\n\n{text}"


class SpelledNumbers(unittest.TestCase):
    def test_cardinal_forms(self):
        self.assertEqual(spelled_number("ninety two"), 92)
        self.assertEqual(spelled_number("four hundred"), 400)
        self.assertEqual(spelled_number("one thousand two hundred"), 1200)
        self.assertEqual(spelled_number("four hundred and ninety two"), 492)

    def test_year_forms_are_two_groups_read_in_sequence(self):
        self.assertEqual(spelled_number("nineteen sixty one"), 1961)
        self.assertEqual(spelled_number("twenty twenty six"), 2026)

    def test_the_cardinal_reading_wins_where_both_apply(self):
        self.assertEqual(spelled_number("twenty six"), 26)

    def test_a_run_that_is_not_a_number_returns_none(self):
        self.assertIsNone(spelled_number("three three"))
        self.assertIsNone(spelled_number("the county sensor"))
        self.assertIsNone(spelled_number(""))

    def test_hyphens_and_case_do_not_matter(self):
        self.assertEqual(spelled_number("Ninety-One"), 91)
        self.assertEqual(spelled_number("nineteen sixty-one"), 1961)


class ExtractFacts(unittest.TestCase):
    TURN = (
        "The Little Pell station logged 1.3 inches in nineteen sixty-one, and the county "
        "unit four hundred feet away sent a Q C flag the QC desk never opened, one of two."
    )

    def test_a_sample_turn_gives_numbers_acronyms_and_names(self):
        self.assertEqual(
            extract_facts(self.TURN),
            {"1.3", "1961", "400", "qc", "little pell"},
        )

    def test_both_acronym_forms_normalize_to_the_compact_one(self):
        self.assertIn("qc", extract_facts("a Q C flag"))
        self.assertIn("qc", extract_facts("a QC flag"))

    def test_numbers_under_ten_are_dropped_unless_they_carry_a_decimal(self):
        self.assertEqual(extract_facts("one of those two instruments is broken"), set())
        self.assertEqual(extract_facts("the gauge read 4 inches"), set())
        self.assertEqual(extract_facts("the gauge read 4.5 inches"), {"4.5"})

    def test_a_sentence_break_ends_a_run_of_number_words(self):
        # A writer's first draft read this as the single number 1480022.
        self.assertEqual(
            extract_facts("one million four hundred eighty thousand. Twenty-two percent less"),
            {"1480000", "22"},
        )

    def test_a_comma_list_of_numbers_is_not_read_as_a_year(self):
        # A writer's first draft read the first two of these as the year 1723.
        self.assertEqual(
            extract_facts("seventeen, twenty-three, and twenty-nine percent"),
            {"17", "23", "29"},
        )

    def test_the_year_form_still_reads_as_one_number(self):
        self.assertEqual(extract_facts("the flood of nineteen sixty one"), {"1961"})
        self.assertEqual(extract_facts("the budget for twenty twenty six"), {"2026"})

    def test_a_comma_inside_one_number_keeps_it_whole(self):
        self.assertEqual(extract_facts("the model covers one thousand two hundred stations"), {"1200"})
        self.assertEqual(extract_facts("the model covers one thousand, two hundred stations"), {"1200"})

    def test_number_words_under_ten_are_still_dropped_across_a_break(self):
        self.assertEqual(extract_facts("three dogs and four cats"), set())

    def test_digit_tokens_lose_their_commas_and_percent_signs(self):
        self.assertEqual(extract_facts("the model covers 1,200 stations at 91%"), {"1200", "91"})

    def test_a_single_capitalized_word_opening_a_sentence_is_not_a_name(self):
        self.assertEqual(extract_facts("Wait. Then the town has its answer."), set())

    def test_a_two_word_run_opening_a_sentence_is_a_name(self):
        self.assertEqual(extract_facts("Ashford County filed the reading."), {"ashford county"})

    def test_a_leading_article_is_not_part_of_the_name(self):
        self.assertEqual(extract_facts("The Ashford County board met."), {"ashford county"})

    def test_a_single_capitalized_word_inside_a_sentence_is_a_name(self):
        self.assertEqual(extract_facts("the report filed by Ashford that spring"), {"ashford"})

    def test_a_possessive_names_the_same_thing_as_the_bare_form(self):
        self.assertEqual(extract_facts("the report filed by Ashford's board"), {"ashford"})
        self.assertEqual(extract_facts("the inspection of Marrow Falls' funnel"), {"marrow falls"})

    def test_weekday_and_month_names_are_prose_not_facts(self):
        self.assertEqual(extract_facts("She closed it out by Thursday and has since August."), set())
        self.assertEqual(extract_facts("The board met on Wednesday, March fourth, in Ashford."), {"ashford"})

    def test_pronoun_contractions_are_not_names(self):
        self.assertEqual(extract_facts("But then I'll take the deal, I've said, and I'm sure."), set())
        self.assertEqual(extract_facts("I think Denise'll read it, I'd guess."), {"denise"})


class Normalization(unittest.TestCase):
    def test_accents_and_hyphens_are_read_the_same_on_both_sides(self):
        source = marked(f"The inspection was signed by {ACCENTED_NAME} in the spring.")
        script = build(
            [
                "The inspection report carries a signature. Jose Ortiz Vega walked the fence "
                "line that spring and wrote down what he found in the funnel of the unit."
            ]
        )
        self.assertEqual(any_source(script, source), [])

    def test_normalize_strips_accents_and_reads_a_hyphen_as_a_space(self):
        self.assertEqual(normalize(ACCENTED_NAME), "jose ortiz vega")

    def test_the_spelled_form_matches_a_source_written_in_digits(self):
        source = marked("The volunteer totals agree with the model 92 percent of the time. QC flags were ignored.")
        script = build(
            [
                "Ninety-two percent of the volunteer's monthly totals line up with the model, "
                "and every reading the county unit sent carried a Q C flag that nobody opened."
            ]
        )
        self.assertEqual(any_source(script, source), [])


class AnySource(unittest.TestCase):
    SOURCE = marked(
        "The Little Pell volunteer station has been read by hand since 1961. The county "
        "sensor sits 400 feet away and reported nothing that morning."
    )

    def test_an_ungrounded_turn_is_flagged_with_its_line_and_its_facts(self):
        script = build(
            [
                "The Ashford County board voted forty seven to twelve on the matter, which "
                "settled the funding question for the next year without anybody walking out."
            ]
        )
        findings = any_source(script, self.SOURCE)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, FIRST_TURN_LINE)
        self.assertEqual(findings[0].axis, "grounding")
        self.assertIn("'12'", findings[0].message)
        self.assertIn("'47'", findings[0].message)
        self.assertIn("'ashford county'", findings[0].message)

    def test_a_grounded_turn_produces_no_finding(self):
        script = build(
            [
                "The Little Pell station has logged rain by hand since nineteen sixty-one, "
                "and the county sensor four hundred feet away recorded nothing at all."
            ]
        )
        self.assertEqual(any_source(script, self.SOURCE), [])

    def test_a_reaction_turn_carrying_no_fact_is_never_flagged(self):
        script = build(["that is the funniest thing on the board, and it is not even close."])
        self.assertEqual(any_source(script, self.SOURCE), [])

    def test_only_the_ungrounded_turn_of_a_pair_is_flagged(self):
        script = build(
            [
                "The Little Pell station has logged rain by hand since nineteen sixty-one.",
                "The Ashford County board voted forty seven to twelve.",
            ]
        )
        findings = any_source(script, self.SOURCE)
        self.assertEqual([f.line for f in findings], [FIRST_TURN_LINE + 1])

    def test_one_finding_per_turn_however_many_facts_miss(self):
        script = build(["Ashford County voted forty seven to twelve on the Brightwater plan."])
        self.assertEqual(len(any_source(script, self.SOURCE)), 1)


class WriterInput(unittest.TestCase):
    def test_marker_lines_are_not_part_of_the_source_text(self):
        body = source_text_from_input("<<< source: short.md >>>\n\nthe gauge and the sensor\n")
        self.assertNotIn("<<< source:", body)
        self.assertIn("the gauge and the sensor", body)

    def test_a_source_name_is_not_itself_a_grounding_match(self):
        # The marker names the file, so leaving it in would ground a claim about "Ashford".
        source = marked("the county sensor reported nothing", name="Ashford County.md")
        script = build(["Ashford County filed the reading as fact and nobody walked the fence line."])
        self.assertEqual(len(any_source(script, source)), 1)

    def test_the_repo_fixture_is_fully_grounded_in_its_source(self):
        writer_input = build_writer_input(resolve_sources([SOURCE]))
        self.assertEqual(any_source(SCRIPT.read_text(encoding="utf-8"), writer_input), [])

    def test_the_repo_fixture_against_an_unrelated_source_is_flagged(self):
        source = marked("Nothing in this document concerns a rain gauge or a county sensor.")
        self.assertTrue(any_source(SCRIPT.read_text(encoding="utf-8"), source))


class Reporting(unittest.TestCase):
    def test_report_of_no_findings_is_the_header_alone(self):
        self.assertEqual(format_report([]), "grounding: 0 finding(s)")

    def test_report_lists_each_finding_under_a_counted_header(self):
        script = build(["The Ashford County board voted forty seven to twelve on the plan."])
        report = format_report(any_source(script, AnySource.SOURCE)).splitlines()
        self.assertEqual(len(report), 2)
        self.assertEqual(report[0], "grounding: 1 finding(s)")
        self.assertTrue(report[1].startswith(f"  {FIRST_TURN_LINE} [grounding] not in any source:"))

    def test_findings_carry_the_slop_linter_shape(self):
        script = build(["The Ashford County board voted forty seven to twelve on the plan."])
        finding = any_source(script, AnySource.SOURCE)[0].as_dict()
        self.assertEqual(finding["line"], FIRST_TURN_LINE)
        self.assertEqual(finding["category"], "grounding")


class StrategyTable(unittest.TestCase):
    def test_the_table_still_iterates_as_names(self):
        self.assertEqual(list(grounding.STRATEGIES), ["none", "any_source", "named_source"])

    def test_none_checks_nothing(self):
        self.assertIsNone(grounding.checker("none"))

    def test_any_source_resolves_to_the_checker(self):
        script = build(["The Ashford County board voted forty seven to twelve on the plan."])
        self.assertEqual(
            grounding.checker("any_source")(script, AnySource.SOURCE),
            any_source(script, AnySource.SOURCE),
        )

    def test_named_source_says_where_it_is_designed(self):
        with self.assertRaises(NotImplementedError) as ctx:
            grounding.checker("named_source")("script", "source")
        self.assertIn("STORY-000026", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
