import sys
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from podcaster import modes
from podcaster.modes import length
from podcaster.paths import TEMPLATE

with open(TEMPLATE / "config.toml", "rb") as fh:
    CFG = tomllib.load(fh)


class Registry(unittest.TestCase):
    def test_registry_imports_no_model_or_writer_module(self):
        for name in ("mlx", "mlx_audio", "mlx_lm", "anthropic"):
            self.assertNotIn(name, sys.modules)

    def test_names_lists_all_four_modes(self):
        self.assertEqual(modes.names(), ["briefing", "digest", "synthesis", "study-review"])

    def test_unknown_mode_names_the_choices(self):
        with self.assertRaises(modes.ModeUnavailable) as ctx:
            modes.load("interview", CFG)
        self.assertIn("briefing", str(ctx.exception))

    def test_spec_returns_the_entry_without_needing_a_brief(self):
        # digest's brief does not exist yet, so load() refuses it; render only needs the weights.
        entry = modes.spec("digest")
        self.assertEqual(entry.rubric_weights["address"], 0.0)
        with self.assertRaises(modes.ModeUnavailable) as ctx:
            modes.spec("interview")
        self.assertIn("briefing", str(ctx.exception))

    def test_briefing_resolves_to_a_brief_on_disk(self):
        mode = modes.load("briefing", CFG)
        self.assertTrue(mode.brief.is_file())
        self.assertEqual(mode.spec.length, "fixed")
        self.assertEqual(mode.spec.grounding, "none")

    def test_unbuilt_modes_say_the_brief_is_designed_not_written(self):
        for name in ("synthesis", "study-review"):
            with self.assertRaises(modes.ModeUnavailable) as ctx:
                modes.load(name, CFG)
            self.assertIn("brief designed, not yet written", str(ctx.exception))

    def test_digest_resolves_to_its_brief(self):
        # prompts/briefs/digest.md landed with STORY-000021.
        mode = modes.load("digest", CFG)
        self.assertTrue(mode.brief.is_file())
        self.assertEqual(mode.brief.name, "digest.md")
        self.assertEqual(mode.spec.length, "scaled")
        self.assertEqual(mode.spec.grounding, "any_source")

    def test_a_configured_brief_missing_on_disk_is_named(self):
        cfg = {"modes": {"digest": {"brief": "prompts/briefs/nowhere.md"}}}
        with self.assertRaises(modes.ModeUnavailable) as ctx:
            modes.load("digest", cfg)
        self.assertIn("prompts/briefs/nowhere.md", str(ctx.exception))
        self.assertIn("[modes.digest]", str(ctx.exception))

    def test_available_lists_only_the_modes_that_resolve_today(self):
        self.assertEqual(modes.available(CFG), ["briefing", "digest"])

    def test_unknown_length_strategy_raises_at_load(self):
        broken = replace(modes.MODES["briefing"], length="golden_ratio")
        with mock.patch.dict(modes.MODES, {"briefing": broken}):
            with self.assertRaises(modes.ModeUnavailable) as ctx:
                modes.load("briefing", CFG)
        self.assertIn("golden_ratio", str(ctx.exception))

    def test_unknown_grounding_strategy_raises_at_load(self):
        broken = replace(modes.MODES["briefing"], grounding="vibes")
        with mock.patch.dict(modes.MODES, {"briefing": broken}):
            with self.assertRaises(modes.ModeUnavailable) as ctx:
                modes.load("briefing", CFG)
        self.assertIn("vibes", str(ctx.exception))


class Budget(unittest.TestCase):
    def test_fixed_budget_is_minutes_times_words_per_minute(self):
        mode = modes.load("briefing", CFG)
        expected = CFG["episode"]["target_minutes"] * CFG["episode"]["words_per_minute"]
        self.assertEqual(length.budget(mode, CFG, source_words=99_999), expected)

    def test_scaled_budget_clamps_to_the_floor_and_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "digest.md"
            brief.write_text("a digest brief stands in for STORY-000021\n", encoding="utf-8")
            cfg = dict(CFG)
            cfg["modes"] = dict(CFG["modes"])
            cfg["modes"]["digest"] = dict(CFG["modes"]["digest"], brief=str(brief))
            mode = modes.load("digest", cfg)
            self.assertEqual(mode.brief, brief)
            self.assertEqual(mode.spec.length, "scaled")
            self.assertEqual(length.budget(mode, cfg, 500), 600)  # 250 raised to the floor
            self.assertEqual(length.budget(mode, cfg, 10_000), 4050)  # 5000 cut to the ceiling
            self.assertEqual(length.budget(mode, cfg, 2000), 1000)  # between the two, unclamped
            self.assertEqual(length.budget(mode, cfg, 5000), 2500)  # ratio 0.5 keeps 5000 words short of the ceiling

    def test_missing_scaled_setting_names_the_key_and_the_table(self):
        mode = modes.Mode(spec=modes.MODES["digest"], brief=Path("x.md"), settings={"floor_words": 600})
        with self.assertRaises(modes.ModeUnavailable) as ctx:
            length.budget(mode, CFG, 1000)
        self.assertIn("words_per_source_word", str(ctx.exception))
        self.assertIn("modes.digest", str(ctx.exception))

    def test_rubric_weights_carry_all_three_axes(self):
        self.assertEqual(
            dict(modes.MODES["study-review"].rubric_weights),
            {"spine": 1.0, "turn_length": 0.0, "address": 0.0},
        )


if __name__ == "__main__":
    unittest.main()
