"""The rubric gate, the grounding gate and the mode resolution in render.main().

The engine, the memory measurement, the slop gate and the mp3 step are all patched, the
same way tests/test_render_memory_guard.py patches them, so nothing here loads a model or
renders audio. Every non-fixture script would otherwise stop at the fixture-first gate, so
each test pre-creates the engine's marker in the patched marker directory rather than
passing --first-render-ok: the marker is the state a proven engine is actually in, and it
keeps the flag list down to the one flag each test is about.
"""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import support
from podcaster import engines, render
from podcaster.script_format import strip_frontmatter

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = support.FIXTURE
SOURCE = support.SOURCE

# Two turns inside the 20 to 45 word band, neither mixing "you" with "he", and no
# `# spine:` line: the spine axis is the only one that fires.
NO_SPINE = """---
date: 2026-09-19
episode: gates
---
[S1] The volunteer station has logged rain by hand since nineteen sixty-one, and the county sensor four hundred feet away recorded nothing at all over the same wet month.
[S2] Four hundred feet is nothing in a rainstorm, so one of those two instruments is broken, and the cheap answer is always that the hand-written one is the broken one.
"""

# A spine, two turns in the band, and one sentence carrying both "you" and "his": the
# address axis is the only one that fires, and digest weights it 0.0. The sources line names
# the fixture source, which carries both facts these turns state, so a grounded mode stops at
# the rubric gate rather than at the grounding gate these tests are not about.
ADDRESS_SWITCH = (
    """---
date: 2026-09-19
episode: gates
mode: {mode}
sources: """
    + str(SOURCE.resolve())
    + """
---
# spine: when the volunteer gauge and the county sensor disagree, which number does the town act on?
[S1] The volunteer station has logged rain by hand since nineteen sixty-one, and the county sensor four hundred feet away recorded nothing at all over the same wet month.
[S2] You would think the wasp nest in the tipping bucket settles it, and his own budget memo from that spring says the same thing in plainer words than anyone used.
"""
)

BAD_MODE = ADDRESS_SWITCH.format(mode="nope")

# The fixture's spine and turns, whose every fact is in tests/fixtures/short-source.md
# (tests/test_grounding_check.py asserts that pair is clean), under frontmatter naming a
# mode and a source list. That keeps the grounding gate the only thing these tests measure.
_, FIXTURE_BODY, _ = strip_frontmatter(FIXTURE.read_text(encoding="utf-8"))

GROUNDED = """---
date: 2026-09-19
episode: gates
mode: {mode}
sources: {sources}
---
""" + FIXTURE_BODY

# 25 words, so the turn-length axis stays quiet, and every fact in it is invented: no
# source mentions an Ashford County board, a 47 or a 12.
INVENTED_TURN = (
    "[S2] The Ashford County board voted forty seven to twelve to keep the sensor, "
    "and the town spent two budget cycles arguing about a graduated cylinder."
)
REPLACED_TURN = "[S2] So the broken one"


def with_invented_turn(script: str) -> str:
    """The same script with one fixture turn swapped for a turn no source supports."""
    lines = script.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(REPLACED_TURN):
            lines[i] = INVENTED_TURN
            return "\n".join(lines) + "\n"
    raise AssertionError(f"no turn starting {REPLACED_TURN!r} in the fixture any more")


class WritingEngine:
    """Stands in for a loaded engine by writing a short silent wav per turn."""

    def voice_for(self, host_cfg):
        return "v"

    def render_turn(self, text, voice, out_wav, sample_rate, **kwargs):
        with wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * 400)


class RenderCase(unittest.TestCase):
    """A temp repo root with a proven engine marker, and the patched render call itself."""

    ENGINE = "kokoro"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.markers = self.root / "markers"
        self.markers.mkdir()
        # This engine is proven, so the fixture-first gate is not what any of these tests measure.
        (self.markers / self.ENGINE).write_text("2026-09-19 an earlier render\n")
        self.out = self.root / "out"

    def _script(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def _run(self, script, extra=()):
        with (
            mock.patch.object(render, "free_memory_bytes", return_value=512 * render.GIB),
            mock.patch.object(engines, "load", return_value=WritingEngine()),
            mock.patch.object(render, "slop_gate", return_value=(True, "skipped")),
            mock.patch.object(render, "to_mp3", return_value=None),
            mock.patch.object(render, "FIRST_RENDER_DIR", self.markers),
        ):
            return render.main(
                ["--script", str(script), "--engine", self.ENGINE, "--out", str(self.out), *extra]
            )

    def _log(self):
        logs = sorted(self.out.glob("*.json"))
        self.assertEqual(len(logs), 1, logs)
        with open(logs[0]) as fh:
            return json.load(fh)

    def _assert_nothing_written(self):
        self.assertFalse(self.out.exists() and list(self.out.iterdir()))


class RubricGate(RenderCase):
    def test_a_script_with_no_spine_exits_6_and_writes_nothing(self):
        code = self._run(self._script("no-spine.md", NO_SPINE))
        self.assertEqual(code, 6)
        self._assert_nothing_written()

    def test_allow_rubric_fail_renders_and_the_log_records_the_bypass(self):
        code = self._run(self._script("no-spine.md", NO_SPINE), extra=["--allow-rubric-fail"])
        self.assertEqual(code, 0)
        log = self._log()
        self.assertEqual(log["rubric_gate"], "bypassed with --allow-rubric-fail")
        self.assertEqual(log["mode"], "briefing")  # no frontmatter mode: config.toml's default

    def test_the_fixture_passes_the_rubric_gate_unmodified(self):
        code = self._run(FIXTURE)
        self.assertEqual(code, 0)
        self.assertEqual(self._log()["rubric_gate"], "passed")

    def test_digest_frontmatter_lets_a_mid_sentence_address_switch_through(self):
        code = self._run(self._script("digest.md", ADDRESS_SWITCH.format(mode="digest")))
        self.assertEqual(code, 0)
        log = self._log()
        self.assertEqual(log["mode"], "digest")
        self.assertEqual(log["rubric_gate"], "passed")

    def test_the_same_script_marked_briefing_fails_the_address_axis(self):
        code = self._run(self._script("briefing.md", ADDRESS_SWITCH.format(mode="briefing")))
        self.assertEqual(code, 6)
        self._assert_nothing_written()

    def test_mode_flag_overrides_the_frontmatter(self):
        script = self._script("briefing.md", ADDRESS_SWITCH.format(mode="briefing"))
        code = self._run(script, extra=["--mode", "digest"])
        self.assertEqual(code, 0)
        self.assertEqual(self._log()["mode"], "digest")

    def test_an_unregistered_frontmatter_mode_exits_10_and_writes_nothing(self):
        script = self._script("bad-mode.md", BAD_MODE)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(script)
        self.assertEqual(code, 10)
        self.assertIn("mode unavailable:", err.getvalue())
        self.assertIn("nope", err.getvalue())
        self._assert_nothing_written()


class GroundingGate(RenderCase):
    def _grounded(self, name, mode="digest", sources=None, script=None):
        text = script or GROUNDED.format(
            mode=mode, sources=str(SOURCE.resolve()) if sources is None else sources
        )
        return self._script(name, text)

    def test_a_briefing_script_with_no_sources_skips_the_gate(self):
        code = self._run(FIXTURE)
        self.assertEqual(code, 0)
        log = self._log()
        self.assertEqual(log["mode"], "briefing")
        self.assertEqual(log["grounding_gate"], "skipped (mode briefing)")
        self.assertEqual(log["sources"], [])

    def test_a_digest_script_whose_facts_are_all_in_its_source_passes(self):
        code = self._run(self._grounded("digest.md"))
        self.assertEqual(code, 0)
        log = self._log()
        self.assertEqual(log["mode"], "digest")
        self.assertEqual(log["grounding_gate"], "passed")
        self.assertEqual(log["sources"], [str(SOURCE.resolve())])

    def test_an_invented_fact_exits_7_and_writes_nothing(self):
        script = self._grounded("invented.md", script=with_invented_turn(
            GROUNDED.format(mode="digest", sources=str(SOURCE.resolve()))
        ))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(script)
        self.assertEqual(code, 7)
        self.assertIn("not in any source:", err.getvalue())
        self.assertIn("grounding gate failed", err.getvalue())
        self._assert_nothing_written()

    def test_allow_ungrounded_renders_and_the_log_records_the_bypass(self):
        script = self._grounded("invented.md", script=with_invented_turn(
            GROUNDED.format(mode="digest", sources=str(SOURCE.resolve()))
        ))
        code = self._run(script, extra=["--allow-ungrounded"])
        self.assertEqual(code, 0)
        self.assertEqual(self._log()["grounding_gate"], "bypassed with --allow-ungrounded")

    def test_a_digest_script_with_no_sources_listed_exits_7(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(self._grounded("no-sources.md", sources=""))
        self.assertEqual(code, 7)
        self.assertIn("needs sources:", err.getvalue())
        self._assert_nothing_written()

    def test_a_digest_script_naming_a_source_that_is_gone_exits_7_naming_it(self):
        gone = self.root / "gone.md"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(self._grounded("gone-source.md", sources=str(gone)))
        self.assertEqual(code, 7)
        self.assertIn(str(gone), err.getvalue())
        self._assert_nothing_written()

    def test_a_briefing_script_naming_a_source_that_is_gone_renders_with_a_warning(self):
        gone = self.root / "gone.md"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(self._grounded("gone-briefing.md", mode="briefing", sources=str(gone)))
        self.assertEqual(code, 0)
        # resolve(): the path is reported as the render resolved it, which on macOS means
        # /var is reported through its /private/var symlink.
        self.assertIn(f"sources: {gone.resolve()} not found (not needed for mode briefing)", err.getvalue())
        self.assertEqual(self._log()["grounding_gate"], "skipped (mode briefing)")

    def test_a_synthesis_script_exits_10_because_its_strategy_is_not_built(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(self._grounded("synthesis.md", mode="synthesis"))
        self.assertEqual(code, 10)
        self.assertIn("STORY-000026", err.getvalue())
        self._assert_nothing_written()


# One host holding every turn: the structure gate's min_turn_share fires and nothing else does.
# Both turns sit in the rubric's 20 to 45 word band and the spine line is present, so a
# structure failure is the only reason this script can be refused.
ONE_SIDED = """---
date: 2026-09-19
episode: gates
---
# spine: when the volunteer gauge and the county sensor disagree, which number does the town act on?
[S1] The volunteer station has logged rain by hand since nineteen sixty-one, and the county sensor four hundred feet away recorded nothing at all over the same wet month.
[S1] Four hundred feet is nothing in a rainstorm, so one of those two instruments is broken, and the cheap answer is always that the hand-written one is the broken one.
"""


class StructureGate(RenderCase):
    """The structure gate runs before the slop gate and has its own code and its own flag.

    develop folded a structure failure into --allow-slop and exit 3. They are different
    failures, so the merged render gives this one exit 12 and --allow-structure-fail.
    """

    def test_a_one_sided_script_exits_12_and_writes_nothing(self):
        code = self._run(self._script("one-sided.md", ONE_SIDED))
        self.assertEqual(code, 12)
        self._assert_nothing_written()

    def test_the_refusal_names_the_host_and_its_share(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._run(self._script("one-sided.md", ONE_SIDED))
        self.assertIn("structure gate:", err.getvalue())
        self.assertIn("S2 never speaks", err.getvalue())

    def test_allow_structure_fail_renders_and_the_log_records_the_bypass(self):
        code = self._run(self._script("one-sided.md", ONE_SIDED), extra=["--allow-structure-fail"])
        self.assertEqual(code, 0)
        self.assertEqual(self._log()["structure_gate"], "bypassed with --allow-structure-fail")

    def test_allow_slop_does_not_bypass_it(self):
        code = self._run(self._script("one-sided.md", ONE_SIDED), extra=["--allow-slop"])
        self.assertEqual(code, 12)

    def test_the_fixture_passes_the_structure_gate_unmodified(self):
        self.assertEqual(self._run(FIXTURE), 0)
        self.assertEqual(self._log()["structure_gate"], "passed")


class RelativeSources(RenderCase):
    """A `sources:` path that is not absolute resolves against the show root.

    pipeline.py writes home-relative or absolute paths, which is right for a working script.
    A script that is committed cannot carry one, because another machine's home directory
    differs, so a committed script names its sources relative to its show.
    """

    def _digest(self, sources: str) -> Path:
        body = GROUNDED.format(mode="digest", sources=sources)
        return self._script("relative.md", body)

    def test_a_show_relative_source_is_found(self):
        with support.show_at(self.root):
            shutil.copy(SOURCE, self.root / "in" / SOURCE.name)
            self.assertEqual(self._run(self._digest(f"in/{SOURCE.name}")), 0)
        self.assertEqual(self._log()["grounding_gate"], "passed")

    def test_an_absolute_source_still_works(self):
        self.assertEqual(self._run(self._digest(str(SOURCE.resolve()))), 0)

    def test_a_relative_source_that_is_not_there_still_exits_7(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self._run(self._digest("tests/fixtures/digest/nowhere.md"))
        self.assertEqual(code, 7)
        self.assertIn("nowhere.md", err.getvalue())

    def test_a_home_relative_source_is_expanded(self):
        # The same person on two Macs has two usernames, so `~/` is the spelling of an
        # absolute path that survives the trip. A literal `~` folder is never what was meant.
        under_home = Path("~/x/y.md")
        resolved = render.resolve_source_paths({"sources": str(under_home)}, support.SHOW)
        self.assertEqual(resolved, [Path.home() / "x" / "y.md"])

    def test_home_absolute_and_relative_can_appear_in_one_list(self):
        resolved = render.resolve_source_paths(
            {"sources": "~/a.md, /tmp/b.md, notes/c.md"}, Path("/show")
        )
        self.assertEqual(
            resolved,
            [
                (Path.home() / "a.md").resolve(),
                Path("/tmp/b.md").resolve(),
                Path("/show/notes/c.md").resolve(),
            ],
        )

    def test_a_grounded_source_outside_the_show_and_outside_home_is_refused(self):
        # Frontmatter under the `manual` writer is written by whoever answered a write
        # instruction that quotes the source material verbatim, so a sources: line is not by
        # itself a path the user chose. A grounded mode opens these files; confine them.
        ok, refused = render.readable_sources({"sources": "/etc/hosts"}, support.SHOW)
        self.assertEqual(ok, [])
        self.assertEqual(refused, [Path("/etc/hosts").resolve()])

    def test_a_source_under_the_show_or_under_home_is_allowed(self):
        meta = {"sources": f"{SOURCE.resolve()}, ~/anywhere.md"}
        ok, refused = render.readable_sources(meta, support.SHOW)
        self.assertEqual(refused, [])
        self.assertEqual(ok, [SOURCE.resolve(), (Path.home() / "anywhere.md").resolve()])

    def test_the_committed_digest_fixtures_name_a_source_that_resolves(self):
        # The fixtures name their sources relative to the checkout, which is their show here.
        checkout = FIXTURES.parent.parent
        for name in ("short-script.md", "long-script.md"):
            script = FIXTURES / "digest" / name
            meta, _, _ = strip_frontmatter(script.read_text(encoding="utf-8"))
            for source in render.resolve_source_paths(meta, checkout):
                self.assertTrue(source.is_file(), f"{name} names {source}")


class RenderLog(RenderCase):
    def test_the_log_carries_the_metrics_and_the_gap_plan(self):
        self._run(FIXTURE)
        log = self._log()
        self.assertEqual(len(log["gaps_ms"]), log["turns"] - 1)
        self.assertEqual(
            set(log["metrics"]),
            {"distinct_2", "mattr_50", "turn_words_mean", "turn_words_max", "question_share", "turn_share"},
        )

    def test_every_gate_result_is_recorded(self):
        self._run(FIXTURE)
        log = self._log()
        for gate in ("structure_gate", "slop_gate", "rubric_gate", "grounding_gate", "first_render_gate"):
            self.assertIn(gate, log, gate)


if __name__ == "__main__":
    unittest.main()
