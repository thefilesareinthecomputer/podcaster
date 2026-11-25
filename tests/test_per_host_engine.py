"""A host may render on a different engine from the other host.

Both engines are then resident at once, so the preflight asks for both and the fixture-first
gate wants a marker for each. Nothing here loads a model: the engines are stand-ins.
"""

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import support
from podcaster import engines, render

FIXTURE = support.FIXTURE

CONFIG = """
[episode]
default = "A"
target_minutes = 27
words_per_minute = 150

[hosts.s1]
name = "host"
kokoro_voice = "af_heart"
speed = 1.0

[hosts.s2]
name = "guest"
engine = "higgs"
kokoro_voice = "af_nicole"
higgs_ref_audio = "s2.wav"
speed = 1.0

[mode]
default = "briefing"

[gate]
require_outline = false

[render]
engine_default = "kokoro"
sample_rate = 24000
mp3_bitrate = "96k"
preflight_headroom_gb = 1.0
fixture_script = "{fixture}"
"""


class NamedEngine:
    """Stands in for a loaded engine, writing a short silent wav and naming itself in the voice."""

    def __init__(self, name):
        self.name = name
        self.turns = 0

    def voice_for(self, host_cfg):
        return self.name

    def render_turn(self, text, voice, out_wav, sample_rate, **kwargs):
        self.turns += 1
        with wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * 400)


class PerHostEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.markers = self.root / "markers"
        self.markers.mkdir()
        self.out = self.root / "out"
        self.config = self.root / "config.toml"
        self.config.write_text(CONFIG.format(fixture=FIXTURE.resolve()), encoding="utf-8")
        self.loaded = {}

    def _load(self, name, **kwargs):
        self.loaded.setdefault(name, NamedEngine(name))
        return self.loaded[name]

    def _run(self, free_gb=512, extra=()):
        with (
            mock.patch.object(render, "free_memory_bytes", return_value=int(free_gb * render.GIB)),
            mock.patch.object(engines, "load", side_effect=self._load),
            mock.patch.object(render, "slop_gate", return_value=(True, "skipped")),
            mock.patch.object(render, "to_mp3", return_value=None),
            mock.patch.object(render, "FIRST_RENDER_DIR", self.markers),
        ):
            return render.main(
                ["--script", str(FIXTURE), "--config", str(self.config), "--out", str(self.out), *extra]
            )

    def _log(self):
        logs = sorted(self.out.glob("*.json"))
        self.assertEqual(len(logs), 1, logs)
        with open(logs[0]) as fh:
            return json.load(fh)

    def test_each_host_renders_on_its_own_engine(self):
        self.assertEqual(self._run(), 0)
        self.assertEqual(set(self.loaded), {"kokoro", "higgs"})
        # The fixture alternates S1 and S2 over six turns.
        self.assertEqual(self.loaded["kokoro"].turns, 3)
        self.assertEqual(self.loaded["higgs"].turns, 3)

    def test_the_log_and_the_output_name_carry_both_engines(self):
        self._run()
        log = self._log()
        self.assertEqual(log["engine"], "kokoro+higgs")
        self.assertEqual(log["engines"], {"s1": "kokoro", "s2": "higgs"})
        self.assertIn("kokoro+higgs", Path(log["wav"]).name)

    def test_the_preflight_asks_for_both_models_at_once(self):
        # kokoro 2.0 + higgs 16.0 + 1.0 headroom is over 18GB, so 12GB free is short even
        # though either engine alone would fit.
        self.assertEqual(self._run(free_gb=12), 8)
        self.assertFalse(self.out.exists() and list(self.out.iterdir()))

    def test_the_refusal_names_both_engines(self):
        spec = [engines.ENGINES["kokoro"], engines.ENGINES["higgs"]]
        cfg = {"render": {"sample_rate": 24000, "preflight_headroom_gb": 1.0}, "episode": {"words_per_minute": 150}}
        with mock.patch.object(render, "free_memory_bytes", return_value=12 * render.GIB):
            ok, report = render.preflight(spec, 3000, cfg)
        self.assertFalse(ok)
        self.assertIn("kokoro + higgs resident", report)

    def test_one_engines_marker_does_not_prove_the_other(self):
        # The fixture always passes the gate, so this asserts the marker side: a render that
        # loads two engines records a marker for each, not just for the named default.
        self._run()
        self.assertTrue((self.markers / "kokoro").exists())
        self.assertTrue((self.markers / "higgs").exists())

    def test_engine_flag_overrides_every_host(self):
        self.assertEqual(self._run(extra=["--engine", "kokoro"]), 0)
        self.assertEqual(set(self.loaded), {"kokoro"})
        self.assertEqual(self._log()["engine"], "kokoro")


if __name__ == "__main__":
    unittest.main()
