import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podcaster import paths, render


class SlopGate(unittest.TestCase):
    def _gate(self, text: str, words: int = 400):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            path = Path(fh.name)
        try:
            return render.slop_gate(path, words)
        finally:
            path.unlink()

    def test_clean_script_passes(self):
        ok, report = self._gate(
            "[S1] The volunteer wrote down one and three tenths inches.\n"
            "[S2] And the county sensor recorded nothing at all.\n"
        )
        self.assertTrue(ok, report)

    def test_non_keyboard_character_fails(self):
        ok, report = self._gate("[S1] José's rain log says so.\n")
        self.assertFalse(ok)
        self.assertIn("symbol", report)

    def test_cadence_over_density_fails(self):
        lines = ["[S1] It is a brake, not an accelerator."] * 4
        ok, report = self._gate("\n".join(lines) + "\n", words=100)
        self.assertFalse(ok)
        self.assertIn("cadence", report)

    def test_vendored_checker_ships_in_the_package(self):
        self.assertTrue(render.SLOP_CHECK.exists(), f"no vendored linter at {render.SLOP_CHECK}")
        self.assertEqual(paths.PACKAGE, render.SLOP_CHECK.parent.parent)

    def test_missing_checker_skips(self):
        ok, report = render.slop_gate(Path("x.md"), 100, checker=Path("/nonexistent/slop_check.py"))
        self.assertTrue(ok)
        self.assertIn("slop gate skipped: no linter at /nonexistent/slop_check.py", report)
        self.assertIn("the render proceeds without the mechanical slop check", report)

    def test_a_linter_that_hangs_fails_the_gate_instead_of_holding_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            stuck = Path(d) / "stuck.py"
            stuck.write_text("import time\ntime.sleep(30)\n")
            with mock.patch.object(render, "SLOP_TIMEOUT_S", 0.5):
                ok, report = render.slop_gate(Path(d) / "x.md", 100, checker=stuck)
        self.assertFalse(ok)
        self.assertIn("ran past", report)


if __name__ == "__main__":
    unittest.main()
