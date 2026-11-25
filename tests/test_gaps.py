import tempfile
import unittest
import wave
from pathlib import Path

from podcaster import render
from podcaster.script_format import Turn, gap_plan, parse_script, section_starts

SCRIPT = """# spine: whether it holds
# q: first
# q: second
[S1] Opening line.
[S2] Is that right?
[S1] It is.
# q: second
[S2] New scene here.
[S1] Nearly done.
[S2] Done.
"""
GAPS = {"default_ms": 350, "after_question_ms": 200, "section_ms": 900, "closing_ms": 700}


class Sections(unittest.TestCase):
    def test_inline_q_marks_the_turn_it_precedes_and_top_block_marks_none(self):
        self.assertEqual(section_starts(SCRIPT), {3})

    def test_plan_by_boundary_type(self):
        turns = parse_script(SCRIPT)
        # before turn 1: default; before 2: after a question; before 3: section; before 4 and 5: closing
        self.assertEqual(gap_plan(turns, section_starts(SCRIPT), GAPS), [350, 200, 900, 700, 700])

    def test_missing_keys_fall_back_to_default(self):
        turns = [Turn("S1", "a?"), Turn("S2", "b"), Turn("S1", "c"), Turn("S2", "d")]
        self.assertEqual(gap_plan(turns, {2}, {"default_ms": 100}), [100, 100, 100])


def silent_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


class Stitch(unittest.TestCase):
    def test_per_boundary_gaps_add_up(self):
        with tempfile.TemporaryDirectory() as d:
            wavs = [silent_wav(Path(d) / f"{i}.wav", 1.0) for i in range(3)]
            seconds = render.stitch(wavs, Path(d) / "out.wav", [500, 250])
        self.assertAlmostEqual(seconds, 3.75, places=3)

    def test_single_int_is_the_old_behavior(self):
        with tempfile.TemporaryDirectory() as d:
            wavs = [silent_wav(Path(d) / f"{i}.wav", 1.0) for i in range(3)]
            self.assertAlmostEqual(render.stitch(wavs, Path(d) / "out.wav", 1000), 5.0, places=3)

    def test_wrong_plan_length_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            wavs = [silent_wav(Path(d) / f"{i}.wav", 1.0) for i in range(3)]
            with self.assertRaises(RuntimeError):
                render.stitch(wavs, Path(d) / "out.wav", [1])


if __name__ == "__main__":
    unittest.main()
