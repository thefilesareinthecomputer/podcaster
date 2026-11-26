import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podcaster import engines, guard, writers

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                  1000000.
Pages active:                                1072271.
Pages inactive:                               500000.
Pages speculative:                            250000.
Pages throttled:                                   0.
Pages wired down:                             145460.
Pages purchase:                                    0.
Pages purgeable:                              250000.
"""


class Lock(unittest.TestCase):
    def test_second_holder_is_refused_with_the_pid(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "x.lock"
            with guard.single_instance(lock):
                with self.assertRaises(guard.Busy) as ctx:
                    with guard.single_instance(lock):
                        pass
                self.assertIn(str(os.getpid()), str(ctx.exception))
            with guard.single_instance(lock):
                pass  # released after the first holder exits

    def test_lock_lives_outside_the_package_so_it_is_machine_wide(self):
        # Two checkouts of this repo on one machine must contend for the same lock; a lock
        # file next to the code gives each checkout its own and guards nothing.
        package = Path(guard.__file__).resolve().parent
        self.assertNotIn(package, guard.LOCK_FILE.resolve().parents)

    def test_busy_does_not_collide_with_another_gate(self):
        # Every exit code render.py can return has one meaning. 6 is the rubric gate.
        self.assertEqual(guard.EXIT_BUSY, 11)

    def test_render_exits_busy_while_another_process_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "x.lock"
            with guard.single_instance(lock):
                code = (
                    "import sys\n"
                    "from podcaster import guard, render\n"
                    "guard.LOCK_FILE = __import__('pathlib').Path(%r)\n"
                    "sys.exit(render.main(['--script', '/nonexistent.md']))"
                ) % str(lock)
                proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, guard.EXIT_BUSY, proc.stderr)
        self.assertIn("already in progress", proc.stderr)


class FreeMemory(unittest.TestCase):
    def test_counts_free_inactive_speculative_and_purgeable(self):
        # 2,000,000 pages * 16KB = 30.5GB
        self.assertAlmostEqual(guard.free_memory_gb(VM_STAT), 2_000_000 * 16384 / 1024**3, places=3)

    def test_require_raises_with_both_numbers(self):
        with self.assertRaises(MemoryError) as ctx:
            guard.require_free_memory(24, "writer mlx_lm", free_gb=9.5)
        self.assertIn("24GB", str(ctx.exception))
        self.assertIn("9.5GB", str(ctx.exception))
        guard.require_free_memory(24, "writer mlx_lm", free_gb=40)

    def test_writer_registry_refuses_below_its_estimate_plus_headroom(self):
        need = writers.WRITERS["mlx_lm"].resident_gb + guard.HEADROOM_GB
        with self.assertRaises(writers.WriterUnavailable) as ctx:
            writers.load("mlx_lm", memory_gb=64, free_gb=need - 0.5)
        self.assertIn("free", str(ctx.exception))
        self.assertIn(f"{need:.0f}GB", str(ctx.exception))

    def test_writer_registry_loads_once_its_estimate_plus_headroom_is_free(self):
        need = writers.WRITERS["mlx_lm"].resident_gb + guard.HEADROOM_GB
        writers.load("mlx_lm", memory_gb=64, free_gb=need + 0.5)

    def test_engine_registry_leaves_free_memory_to_the_render_preflight(self):
        # Importing an engine loads no weights; the preflight checks free memory before any turn.
        with mock.patch.object(guard, "free_memory_gb", side_effect=AssertionError("not checked here")):
            engines.load("kokoro", memory_gb=16)
            engines.load("apple", memory_gb=16)


if __name__ == "__main__":
    unittest.main()
