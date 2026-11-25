import sys
import unittest

from podcaster.writers import mlx_lm


def host(tag: str, words_per_turn: int, log: list):
    n = 0

    def speak(incoming: str) -> str:
        nonlocal n
        n += 1
        log.append((tag, incoming))
        if "Write the spine" in incoming:
            return "# spine: what does the record actually say?"
        return " ".join(f"{tag[1:3].lower()}{n}w{i}" for i in range(words_per_turn))

    return speak


class Dialogue(unittest.TestCase):
    def test_alternates_tags_meets_budget_and_closes_once_each(self):
        log = []
        spine, lines = mlx_lm.dialogue(host("[S1]", 10, log), host("[S2]", 10, log), "SOURCES", word_budget=50)
        self.assertEqual(spine, "# spine: what does the record actually say?")
        self.assertEqual([ln[:4] for ln in lines], ["[S1]", "[S2]"] * 4)
        # 60 words hit the budget of 50 after turn 6; then one closing turn each.
        self.assertEqual(mlx_lm.word_count(lines), 80)
        s1_inputs = [inc for tag, inc in log if tag == "[S1]"]
        self.assertIn("Close the episode", s1_inputs[-1])
        self.assertEqual(sum("Close the episode" in i for i in s1_inputs), 1)
        s2_inputs = [inc for tag, inc in log if tag == "[S2]"]
        self.assertIn("last line", s2_inputs[-1])

    def test_each_host_hears_only_the_other_hosts_last_turn(self):
        log = []
        _, lines = mlx_lm.dialogue(host("[S1]", 30, log), host("[S2]", 30, log), "SOURCES", word_budget=100)
        s2_first = [inc for tag, inc in log if tag == "[S2]"][0]
        self.assertTrue(s2_first.startswith("SOURCES"))
        self.assertTrue(s2_first.endswith(lines[0]))
        s1_second = [inc for tag, inc in log if tag == "[S1]"][2]
        self.assertEqual(s1_second, lines[1])

    def test_sources_are_sent_once_per_host(self):
        log = []
        mlx_lm.dialogue(host("[S1]", 30, log), host("[S2]", 30, log), "SOURCES", word_budget=100)
        self.assertEqual(sum("SOURCES" in inc for _, inc in log), 2)


class WordlessTurns(unittest.TestCase):
    """A host that returns nothing must not leave a bare tag in the script.

    `[S1]` with no text is not a turn, and script_format refuses it, so a run that would
    otherwise have succeeded fails at the parse instead. A real 14-turn briefing run
    produced one of these at line 22.
    """

    def test_a_wordless_turn_is_dropped_rather_than_written(self):
        log = []
        s1 = host("[S1]", 12, log)
        blanks = iter(["", ""])

        def flaky(incoming: str) -> str:
            try:
                return next(blanks)
            except StopIteration:
                return s1(incoming)

        _, lines = mlx_lm.dialogue(flaky, host("[S2]", 12, log), "SRC", 60)
        self.assertTrue(lines)
        for line in lines:
            self.assertNotIn(line.strip(), ("[S1]", "[S2]"), lines)
            self.assertTrue(mlx_lm.word_count([line]) > 0, line)

    def test_the_script_still_parses_when_a_host_goes_quiet(self):
        from podcaster.script_format import parse_script

        log = []
        blanks = iter([""])
        s1 = host("[S1]", 12, log)

        def flaky(incoming: str) -> str:
            try:
                return next(blanks)
            except StopIteration:
                return s1(incoming)

        spine, lines = mlx_lm.dialogue(flaky, host("[S2]", 12, log), "SRC", 60)
        parse_script("\n".join([spine, *lines, ""]))

    def test_three_blanks_in_a_row_still_stop_the_run(self):
        # Dropping the line must not disarm the stall guard, which counts them separately.
        with self.assertRaises(mlx_lm.Stalled):
            mlx_lm.dialogue(lambda _: "", lambda _: "", "SRC", 60)


class Bounds(unittest.TestCase):
    def test_three_wordless_turns_in_a_row_stop_the_run(self):
        calls = []

        def mute(incoming: str) -> str:
            calls.append(incoming)
            return "# spine: q" if "Write the spine" in incoming else "<think>still thinking</think>"

        with self.assertRaises(mlx_lm.Stalled) as ctx:
            mlx_lm.dialogue(mute, mute, "SOURCES", word_budget=4000)
        self.assertIn("no spoken words", str(ctx.exception))
        self.assertLessEqual(len(calls), 1 + mlx_lm.MAX_EMPTY_TURNS)

    def test_exchange_cap_stops_a_run_that_never_meets_the_budget(self):
        log = []
        with self.assertRaises(mlx_lm.Stalled) as ctx:
            mlx_lm.dialogue(host("[S1]", 1, log), host("[S2]", 1, log), "SOURCES", word_budget=4000, max_turns=5)
        self.assertIn("5 exchanges", str(ctx.exception))
        self.assertEqual(len([1 for tag, _ in log if tag == "[S1]"]), 6)  # spine plus five turns

    def test_default_exchange_cap_scales_with_the_budget(self):
        log = []
        with self.assertRaises(mlx_lm.Stalled):
            mlx_lm.dialogue(host("[S1]", 1, log), host("[S2]", 1, log), "SOURCES", word_budget=200)
        self.assertEqual(len([1 for tag, _ in log if tag == "[S2]"]), 20)

    def test_stalled_is_a_writer_unavailable(self):
        from podcaster.writers import WriterUnavailable

        self.assertTrue(issubclass(mlx_lm.Stalled, WriterUnavailable))


class Compile(unittest.TestCase):
    HEADER = "# spine: whether it holds\n# q: what the record says\n# q: what changes"

    def _hosts(self, log):
        def make(tag):
            def speak(incoming):
                log.append((tag, incoming))
                if "Write the spine" in incoming:
                    return self.HEADER
                return f"{tag} answer to {incoming.splitlines()[0][:20]} " + "w " * 30

            return speak

        return make("[S1]"), make("[S2]")

    def _editor(self, log):
        def edit(incoming):
            log.append(("editor", incoming))
            return "<think>plan</think>\n# q: what the record says\n[S1] The record says one.\n[S2] It says one, and I read that as two.\n# q: what changes\n[S1] The plan changes.\n[S2] Then we change it.\nignored trailing text\n"

        return edit

    def test_each_host_answers_every_question_then_the_editor_compiles(self):
        log = []
        s1, s2 = self._hosts(log)
        header, lines = mlx_lm.compile_episode(s1, s2, self._editor(log), "SOURCES", word_budget=20)
        self.assertEqual(header, self.HEADER)
        self.assertEqual([tag for tag, _ in log if tag != "editor"], ["[S1]", "[S1]", "[S2]", "[S1]", "[S2]"])
        editor_in = [inc for tag, inc in log if tag == "editor"][0]
        self.assertIn("# q: what the record says", editor_in)
        self.assertIn("[S1] [S1] answer to", editor_in)
        self.assertEqual([ln[:4] for ln in lines], ["# q:", "[S1]", "[S2]", "# q:", "[S1]", "[S2]"])
        self.assertNotIn("ignored trailing text", "\n".join(lines))

    def test_sources_go_to_each_host_once(self):
        log = []
        s1, s2 = self._hosts(log)
        mlx_lm.compile_episode(s1, s2, self._editor(log), "SOURCES", word_budget=20)
        self.assertEqual(sum("SOURCES" in inc for tag, inc in log if tag == "[S1]"), 2)  # spine call plus first question
        self.assertEqual(sum("SOURCES" in inc for tag, inc in log if tag == "[S2]"), 1)

    def test_no_outline_or_thin_compile_stalls(self):
        def no_outline(incoming):
            return "# spine: x"

        with self.assertRaises(mlx_lm.Stalled):
            mlx_lm.compile_episode(no_outline, no_outline, no_outline, "S", word_budget=100)
        log = []
        s1, s2 = self._hosts(log)
        with self.assertRaises(mlx_lm.Stalled) as ctx:
            mlx_lm.compile_episode(s1, s2, self._editor(log), "S", word_budget=1000)
        self.assertIn("compile pass returned", str(ctx.exception))


class Cleaning(unittest.TestCase):
    def test_strips_think_blocks_tags_and_extra_paragraphs(self):
        raw = "<think>plan\nmore</think>\n[S2] The line, then more.\nSecond line.\n\nA second paragraph that is dropped."
        self.assertEqual(mlx_lm.clean_turn(raw, "[S2]"), "[S2] The line, then more. Second line.")

    def test_retags_a_line_that_used_the_wrong_tag(self):
        self.assertEqual(mlx_lm.clean_turn("[S1] wrong tag", "[S2]"), "[S2] wrong tag")

    def test_spine_normalizes(self):
        self.assertEqual(mlx_lm.clean_spine("<think>x</think>\n`# spine: the question?`\nextra"), "# spine: the question?")
        self.assertEqual(mlx_lm.clean_spine("just a question"), "# spine: just a question")

    def test_module_imports_without_mlx(self):
        self.assertNotIn("mlx_lm", sys.modules)
        self.assertNotIn("mlx", sys.modules)


if __name__ == "__main__":
    unittest.main()
