import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import support
from podcaster import engines, render

FIXTURE = support.FIXTURE

VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    17640.
Pages active:                                 265149.
Pages inactive:                               264108.
Pages speculative:                               731.
Pages throttled:                                   0.
Pages wired down:                             134585.
Pages purgeable:                                1995.
"""


class RefusingEngine:
    """Stands in for a loaded engine and fails the test if the render loop starts."""

    def voice_for(self, host_cfg):
        return "v"

    def render_turn(self, *args, **kwargs):
        raise AssertionError("render_turn must not be called")


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


class VmStatParsing(unittest.TestCase):
    def test_free_inactive_and_speculative_pages_are_counted(self):
        expected = (17640 + 264108 + 731) * 16384
        self.assertEqual(render.parse_vm_stat(VM_STAT_SAMPLE), expected)

    def test_missing_page_size_is_an_error(self):
        with self.assertRaises(RuntimeError):
            render.parse_vm_stat("Mach Virtual Memory Statistics:\nPages free: 10.\n")


class Estimate(unittest.TestCase):
    def test_kokoro_estimate_is_resident_plus_streaming_audio_plus_headroom(self):
        spec = engines.ENGINES["kokoro"]
        need = render.estimate_render_bytes(spec, 3000, 24000, 150, 1.0)
        audio = 24000 * 2 * 2
        self.assertEqual(need["resident"], 2 * render.GIB)
        self.assertEqual(need["audio"], audio)
        self.assertEqual(need["headroom"], render.GIB)
        self.assertEqual(need["total"], 2 * render.GIB + audio + render.GIB)

    def test_audio_term_does_not_grow_with_episode_length(self):
        spec = engines.ENGINES["kokoro"]
        short = render.estimate_render_bytes(spec, 300, 24000, 150, 1.0)
        long = render.estimate_render_bytes(spec, 12000, 24000, 150, 1.0)
        self.assertEqual(short["audio"], long["audio"])

    def test_two_engines_on_one_render_sum_their_resident_terms(self):
        # A per-host engine override holds both models at once, so the preflight has to ask
        # for both. Estimating only the named engine is how a two-engine render swaps.
        pair = [engines.ENGINES["kokoro"], engines.ENGINES["higgs"]]
        need = render.estimate_render_bytes(pair, 3000, 24000, 150, 1.0)
        self.assertEqual(need["resident"], int((2.0 + 16.0) * render.GIB))

    def test_one_spec_and_a_list_of_one_agree(self):
        spec = engines.ENGINES["kokoro"]
        self.assertEqual(
            render.estimate_render_bytes(spec, 3000, 24000, 150, 1.0),
            render.estimate_render_bytes([spec], 3000, 24000, 150, 1.0),
        )


class Preflight(unittest.TestCase):
    CFG = {"render": {"sample_rate": 24000, "preflight_headroom_gb": 1.0}, "episode": {"words_per_minute": 150}}

    def test_report_names_the_need_and_the_free_figure(self):
        with mock.patch.object(render, "free_memory_bytes", return_value=64 * render.GIB):
            ok, report = render.preflight(engines.ENGINES["kokoro"], 3000, self.CFG)
        self.assertTrue(ok, report)
        self.assertIn("needs about", report)
        self.assertIn("64.0GB free measured", report)

    def test_refusal_says_what_to_do(self):
        with mock.patch.object(render, "free_memory_bytes", return_value=256 * 1024 * 1024):
            ok, report = render.preflight(engines.ENGINES["kokoro"], 3000, self.CFG)
        self.assertFalse(ok)
        self.assertIn("0.2GB free measured", report)
        self.assertIn("close other apps or pick a lighter engine", report)

    def test_kokoro_fits_in_the_free_memory_a_busy_16gb_machine_has(self):
        # About 6GB free is ordinary on a 16GB Mac with apps open; kokoro holds about 2GB.
        with mock.patch.object(render, "free_memory_bytes", return_value=6 * render.GIB):
            ok, report = render.preflight(engines.ENGINES["kokoro"], 3000, self.CFG)
        self.assertTrue(ok, report)

    def test_kokoro_is_refused_below_its_estimate_plus_headroom(self):
        with mock.patch.object(render, "free_memory_bytes", return_value=int(2.5 * render.GIB)):
            ok, _ = render.preflight(engines.ENGINES["kokoro"], 3000, self.CFG)
        self.assertFalse(ok)


class MainGuard(unittest.TestCase):
    def _run(self, engine_obj, free_bytes, out_dir):
        with (
            mock.patch.object(render, "free_memory_bytes", return_value=free_bytes),
            mock.patch.object(engines, "load", return_value=engine_obj),
            mock.patch.object(render, "slop_gate", return_value=(True, "skipped")),
            mock.patch.object(render, "to_mp3", return_value=None),
            mock.patch.object(render, "FIRST_RENDER_DIR", Path(out_dir) / "markers"),
        ):
            return render.main(["--script", str(FIXTURE), "--engine", "kokoro", "--out", str(out_dir)])

    def test_over_budget_render_exits_8_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(RefusingEngine(), 64 * 1024 * 1024, tmp)
            self.assertEqual(code, 8)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_in_budget_render_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(WritingEngine(), 512 * render.GIB, tmp)
            self.assertEqual(code, 0)
            self.assertEqual(len(list(Path(tmp).glob("*.wav"))), 1)
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 1)

    def test_render_saves_a_verbatim_script_copy_beside_the_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(WritingEngine(), 512 * render.GIB, tmp), 0)
            (wav,) = Path(tmp).glob("*.wav")
            copy = wav.with_suffix(".md")
            self.assertEqual(copy.read_bytes(), FIXTURE.read_bytes())
            log = json.loads(wav.with_suffix(".json").read_text())
            self.assertEqual(log["script_copy"], str(copy))


# A spine line and two turns inside the rubric's 20 to 45 word band, so these tests stop at the
# fixture-first gate and not at the rubric gate (tests/test_render_gates.py covers that one).
FULL_LENGTH = (
    "# spine: a script that is not the fixture, used to prove the fixture-first gate\n"
    "[S1] This is the first turn of a script that is not the fixture, written long enough to sit"
    " inside the rubric's twenty to forty five word band.\n"
    "[S2] The second turn of that same script says the same thing again, at the same length, so the"
    " fixture-first gate is the only thing these tests are measuring here.\n"
)


class FixtureFirst(unittest.TestCase):
    """A non-fixture script waits until that engine has finished one render on this machine."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.markers = self.root / "markers"
        self.out = self.root / "out"
        self.script = self.root / "full-length.md"
        self.script.write_text(FULL_LENGTH)

    def _run(self, script, engine_name, extra=()):
        with (
            mock.patch.object(render, "free_memory_bytes", return_value=512 * render.GIB),
            mock.patch.object(engines, "load", return_value=WritingEngine()),
            mock.patch.object(render, "slop_gate", return_value=(True, "skipped")),
            mock.patch.object(render, "to_mp3", return_value=None),
            mock.patch.object(render, "FIRST_RENDER_DIR", self.markers),
        ):
            return render.main(
                ["--script", str(script), "--engine", engine_name, "--out", str(self.out), *extra]
            )

    def _log(self):
        logs = sorted(self.out.glob("*.json"))
        self.assertEqual(len(logs), 1, logs)
        with open(logs[0]) as fh:
            return json.load(fh)

    def test_unproven_engine_refuses_a_non_fixture_script_and_writes_nothing(self):
        code = self._run(self.script, "kokoro")
        self.assertEqual(code, 9)
        self.assertFalse(self.out.exists() and list(self.out.iterdir()))
        self.assertFalse(render.first_render_marker("kokoro", self.markers).exists())

    def test_fixture_renders_on_an_unproven_engine_and_records_the_marker(self):
        code = self._run(FIXTURE, "kokoro")
        self.assertEqual(code, 0)
        marker = render.first_render_marker("kokoro", self.markers)
        self.assertTrue(marker.exists())
        self.assertIn(str(FIXTURE), marker.read_text())
        self.assertEqual(self._log()["first_render_gate"], "fixture")

    def test_marker_from_the_fixture_unlocks_a_non_fixture_script(self):
        self.assertEqual(self._run(FIXTURE, "kokoro"), 0)
        self.assertEqual(self._run(self.script, "kokoro"), 0)
        logs = sorted(self.out.glob("*.json"))
        self.assertEqual(len(logs), 2, logs)

    def test_one_engines_marker_does_not_unlock_another(self):
        self.assertEqual(self._run(FIXTURE, "kokoro"), 0)
        self.assertTrue(render.first_render_marker("kokoro", self.markers).exists())
        self.assertEqual(self._run(self.script, "apple"), 9)

    def test_first_render_ok_bypasses_the_gate_and_the_log_says_so(self):
        code = self._run(self.script, "kokoro", extra=["--first-render-ok"])
        self.assertEqual(code, 0)
        self.assertEqual(self._log()["first_render_gate"], "bypassed with --first-render-ok")
        self.assertTrue(render.first_render_marker("kokoro", self.markers).exists())

    def test_the_gate_report_names_the_engine_the_marker_and_the_flag(self):
        ok, report = render.fixture_gate(self.script, "higgs", False, self.markers)
        self.assertFalse(ok)
        self.assertIn("higgs", report)
        self.assertIn(str(self.markers / "higgs"), report)
        self.assertIn(str(render.FIXTURE), report)
        self.assertIn("--first-render-ok", report)


def write_turn(path: Path, frames: bytes, rate=24000, channels=1, width=2):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(frames)


def pattern(n_frames: int, seed: int) -> bytes:
    """`n_frames` of distinct non-zero 16-bit samples, so a misordered stitch would show up."""
    return b"".join(((seed * 991 + i) % 30000 + 1).to_bytes(2, "little") for i in range(n_frames))


def buffered_stitch(turn_wavs, out_wav, gap_ms):
    """The pre-STORY-000003 implementation, kept here as the reference output."""
    frames = []
    rate = None
    for p in turn_wavs:
        with wave.open(str(p), "rb") as wf:
            rate = wf.getframerate()
            frames.append(wf.readframes(wf.getnframes()))
    gap = b"\x00\x00" * int(rate * gap_ms / 1000)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(gap.join(frames))
    total_frames = sum(len(f) // 2 for f in frames) + len(gap) // 2 * (len(frames) - 1)
    return total_frames / rate


class StreamingStitch(unittest.TestCase):
    RATE = 24000
    GAP_MS = 350
    COUNTS = (30000, 5000, 48000)  # two turns cross a one-second chunk boundary, one stays under it

    def _turns(self, tmp):
        paths = []
        for i, n in enumerate(self.COUNTS):
            p = Path(tmp) / f"{i:04d}-S1.wav"
            write_turn(p, pattern(n, i + 1), rate=self.RATE)
            paths.append(p)
        return paths

    @staticmethod
    def _read_all(path: Path):
        with wave.open(str(path), "rb") as wf:
            return wf.getparams(), wf.readframes(wf.getnframes())

    def test_output_matches_the_buffered_implementation_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            turns = self._turns(tmp)
            streamed = Path(tmp) / "streamed.wav"
            reference = Path(tmp) / "reference.wav"
            got = render.stitch(turns, streamed, self.GAP_MS)
            want = buffered_stitch(turns, reference, self.GAP_MS)
            got_params, got_frames = self._read_all(streamed)
            want_params, want_frames = self._read_all(reference)
            self.assertEqual(got_frames, want_frames)
            self.assertEqual(got_params, want_params)
            self.assertEqual(got, want)

    def test_no_read_ever_asks_for_more_than_one_second_of_frames(self):
        original = wave.Wave_read.readframes
        largest = []

        def recording(self, n):
            largest.append(n)
            return original(self, n)

        with tempfile.TemporaryDirectory() as tmp:
            turns = self._turns(tmp)
            with mock.patch.object(wave.Wave_read, "readframes", autospec=True, side_effect=recording):
                render.stitch(turns, Path(tmp) / "out.wav", self.GAP_MS)
        self.assertTrue(largest)
        self.assertLessEqual(max(largest), self.RATE)
        self.assertLess(max(largest), max(self.COUNTS))

    def test_sample_rate_mismatch_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            turns = self._turns(tmp)
            odd = Path(tmp) / "9999-S2.wav"
            write_turn(odd, pattern(4000, 9), rate=22050)
            turns.append(odd)
            out = Path(tmp) / "out.wav"
            with self.assertRaises(RuntimeError) as ctx:
                render.stitch(turns, out, self.GAP_MS)
            self.assertIn("sample rate mismatch", str(ctx.exception))
            self.assertFalse(out.exists())

    def test_stereo_turn_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            turns = self._turns(tmp)
            stereo = Path(tmp) / "9999-S2.wav"
            write_turn(stereo, pattern(8000, 9), rate=self.RATE, channels=2)
            turns.append(stereo)
            out = Path(tmp) / "out.wav"
            with self.assertRaises(RuntimeError) as ctx:
                render.stitch(turns, out, self.GAP_MS)
            self.assertIn("not 16-bit mono", str(ctx.exception))
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
