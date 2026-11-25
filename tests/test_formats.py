import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support
from podcaster import guard, pipeline, writers
from podcaster.script_format import Turn, structure_check

_LOCK_DIR = tempfile.TemporaryDirectory()
guard.LOCK_FILE = Path(_LOCK_DIR.name) / "test.lock"

CFG = {
    "episode": {"format": "deep-dive", "words_per_minute": 100},
    "formats": {
        "deep-dive": {"hosts": 2, "minutes": 20, "structure": ["a", "b"]},
        "brief": {"hosts": 1, "minutes": 4, "structure": ["one"]},
    },
}


class Formats(unittest.TestCase):
    def test_default_and_override(self):
        self.assertEqual(pipeline.episode_format(CFG)["name"], "deep-dive")
        self.assertEqual(pipeline.episode_format(CFG, "brief")["hosts"], 1)

    def test_unknown_format_names_the_choices(self):
        with self.assertRaises(KeyError) as ctx:
            pipeline.episode_format(CFG, "opera")
        self.assertIn("brief", str(ctx.exception))

    def test_flat_config_without_formats_still_works(self):
        fmt = pipeline.episode_format({"episode": {"target_minutes": 10, "structure": ["x"]}})
        self.assertEqual((fmt["hosts"], fmt["minutes"], fmt["structure"]), (2, 10, ["x"]))

    def test_length_scale(self):
        self.assertEqual(pipeline.LENGTH_SCALE, {"short": 0.5, "default": 1.0, "long": 1.6})

    def test_write_instruction_carries_the_format_and_the_scaled_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "notes.md").write_text("some material\n")
            with support.show_at(root), mock.patch("sys.stderr") as err:
                rc = pipeline.main(
                    [
                        "--date", "1999-01-01", "--episode", "A",
                        "--source", str(root / "notes.md"),
                        "--format", "brief", "--length", "long",
                    ]
                )
            self.assertEqual(rc, 5)
            printed = "".join(str(c.args[0]) for c in err.write.call_args_list if c.args)
            self.assertIn("Format: brief, 1 host(s)", printed)
            self.assertIn("Solo episode", printed)
            self.assertIn("about 960 spoken words", printed)  # 4 minutes * 150 wpm * 1.6
            self.assertIn("words: 960", printed)  # and the frontmatter block agrees

    def test_the_focus_lands_in_the_brief(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "notes.md").write_text("some material\n")
            with support.show_at(root), mock.patch("sys.stderr") as err:
                pipeline.main(
                    ["--episode", "A", "--source", str(root / "notes.md"), "--focus", "the third source"]
                )
            printed = "".join(str(c.args[0]) for c in err.write.call_args_list if c.args)
            self.assertIn("Focus for this episode: the third source", printed)
            self.assertIn("1. open on a scene", printed)  # the deep-dive structure list from config.toml

    def test_unknown_format_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes.md").write_text("x\n")
            with support.show_at(Path(d)), mock.patch("sys.stderr"):
                code = pipeline.main(
                    ["--episode", "A", "--source", str(Path(d) / "notes.md"), "--format", "opera"]
                )
        self.assertEqual(code, 1)


class SoloGate(unittest.TestCase):
    def test_solo_script_passes_and_s2_fails_in_solo(self):
        solo = [Turn("S1", "a"), Turn("S1", "b"), Turn("S1", "c")]
        self.assertEqual(structure_check(solo, ["q"], {"hosts": 1}), [])
        fails = structure_check(solo + [Turn("S2", "d")], ["q"], {"hosts": 1})
        self.assertTrue(any("S2 speaks in a 1-host format" in f for f in fails), fails)

    def test_two_host_default_still_requires_both(self):
        fails = structure_check([Turn("S1", "a"), Turn("S1", "b")], ["q"], {})
        self.assertTrue(any("S2 never speaks" in f for f in fails))


class BriefFormat(unittest.TestCase):
    def test_brief_names_format_hosts_and_frontmatter_key(self):
        text = writers.brief({"format": "brief", "hosts": 1, "structure": ["one"]}, 600)
        self.assertIn("Format: brief, 1 host(s)", text)
        self.assertIn("format: brief", text)
        self.assertIn("Solo episode", text)


if __name__ == "__main__":
    unittest.main()
