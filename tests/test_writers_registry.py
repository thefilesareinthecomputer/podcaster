import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podcaster import writers

CFG = {"rules": "prompts/rules.md", "s1": "prompts/s1.md", "s2": "prompts/s2.md", "mlx_lm_model": "m", "max_turn_tokens": 10}


class Registry(unittest.TestCase):
    def test_16gb_machine_gets_manual_only(self):
        self.assertEqual(writers.available_on_this_machine(memory_gb=16), ["manual"])

    def test_64gb_machine_gets_both(self):
        self.assertEqual(writers.available_on_this_machine(memory_gb=64), ["manual", "mlx_lm"])

    def test_mlx_lm_refuses_on_16gb(self):
        with self.assertRaises(writers.WriterUnavailable) as ctx:
            writers.load("mlx_lm", memory_gb=16)
        self.assertIn("24GB", str(ctx.exception))

    def test_unknown_writer_names_the_choices(self):
        with self.assertRaises(writers.WriterUnavailable) as ctx:
            writers.load("anthropic", memory_gb=64)
        self.assertIn("manual", str(ctx.exception))

    def test_manual_raises_so_the_pipeline_exits_5(self):
        mod = writers.load("manual", memory_gb=16)
        with self.assertRaises(writers.WriterUnavailable) as ctx:
            mod.write(writers.Brief(), "a b c", 4000)
        self.assertIn("write it", str(ctx.exception))

    def test_registry_does_not_import_mlx(self):
        self.assertNotIn("mlx_lm", sys.modules)
        self.assertNotIn("mlx", sys.modules)

    def test_only_mlx_lm_claims_memory_and_nothing_leaves_the_machine_yet(self):
        self.assertEqual(writers.WRITERS["manual"].resident_gb, 0.0)
        self.assertGreater(writers.WRITERS["mlx_lm"].resident_gb, 0.0)
        self.assertFalse(any(s.leaves_machine for s in writers.WRITERS.values()))


class BriefParts(unittest.TestCase):
    """A writer gets prompt parts, not paths, and not the mode's name."""

    BRIEF = writers.Brief(
        mode_brief="MODE",
        rules="RULES",
        personas={"[S1]": "ONE", "[S2]": "TWO"},
        episode="EPISODE",
    )

    def test_shared_leaves_the_personas_out(self):
        shared = self.BRIEF.shared()
        self.assertIn("RULES", shared)
        self.assertIn("MODE", shared)
        self.assertIn("EPISODE", shared)
        self.assertNotIn("ONE", shared)
        self.assertNotIn("TWO", shared)

    def test_joined_carries_every_part_in_tag_order(self):
        joined = self.BRIEF.joined()
        for part in ("MODE", "RULES", "ONE", "TWO", "EPISODE"):
            self.assertIn(part, joined)
        self.assertLess(joined.index("ONE"), joined.index("TWO"))

    def test_empty_parts_do_not_leave_blank_gaps(self):
        self.assertEqual(writers.Brief(rules="RULES").joined(), "RULES")
        self.assertEqual(writers.Brief().joined(), "")


if __name__ == "__main__":
    unittest.main()
