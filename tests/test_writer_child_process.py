"""The mlx_lm writer's process boundary, without mlx-lm and without a model.

The boundary is the memory mechanism: a writer and a render engine are never resident
together on this machine, and only the OS can promise the model's memory is back. These
tests drive the real subprocess with `generate_script` stubbed, so the payload, the
handshake and the failure paths are exercised and nothing loads weights.
"""

import json
import subprocess
import sys
import unittest
from unittest import mock

from podcaster import writers
from podcaster.writers import mlx_lm

BRIEF = writers.Brief(
    mode_brief="MODE", rules="RULES", personas={"[S1]": "ONE", "[S2]": "TWO"}, episode="EPISODE"
)


class Payload(unittest.TestCase):
    def test_every_prompt_part_and_the_options_reach_the_child(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(json.loads(kwargs["input"]))
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, stdout="# spine: s\n[S1] a\n", stderr="")

        with mock.patch.object(mlx_lm.subprocess, "run", side_effect=fake_run):
            mlx_lm.write(BRIEF, "SOURCE", 900, model_id="m/x", options={"max_turn_tokens": 7})
        self.assertEqual(seen["argv"][1:], ["-m", "podcaster.writers.mlx_lm"])
        self.assertEqual(seen["brief"]["personas"], {"[S1]": "ONE", "[S2]": "TWO"})
        self.assertEqual(seen["brief"]["rules"], "RULES")
        self.assertEqual(seen["source_text"], "SOURCE")
        self.assertEqual(seen["word_budget"], 900)
        self.assertEqual(seen["model_id"], "m/x")
        self.assertEqual(seen["options"]["max_turn_tokens"], 7)

    def test_the_child_is_waited_for_with_a_timeout(self):
        with mock.patch.object(mlx_lm.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="[S1] a\n", stderr="")
            mlx_lm.write(BRIEF, "S", 100, model_id="m/x")
        self.assertEqual(run.call_args.kwargs["timeout"], mlx_lm.CHILD_TIMEOUT_S)

    def test_no_model_id_refuses_before_spawning_anything(self):
        with mock.patch.object(mlx_lm.subprocess, "run") as run:
            with self.assertRaises(writers.WriterUnavailable) as ctx:
                mlx_lm.write(BRIEF, "S", 100, model_id=None)
        run.assert_not_called()
        self.assertIn("mlx_lm_model", str(ctx.exception))


class ChildFailures(unittest.TestCase):
    def test_a_nonzero_exit_is_a_writer_failure_not_a_crash(self):
        with mock.patch.object(mlx_lm.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="mlx_lm: boom\n")
            with self.assertRaises(writers.WriterUnavailable) as ctx:
                mlx_lm.write(BRIEF, "S", 100, model_id="m/x")
        self.assertIn("exited 1", str(ctx.exception))

    def test_an_empty_script_is_a_writer_failure(self):
        with mock.patch.object(mlx_lm.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="   \n", stderr="")
            with self.assertRaises(writers.WriterUnavailable):
                mlx_lm.write(BRIEF, "S", 100, model_id="m/x")


class RealSubprocess(unittest.TestCase):
    """The actual `python -m podcaster.writers.mlx_lm` entry point, with the model stubbed out."""

    PAYLOAD = json.dumps(
        {
            "brief": {"mode_brief": "MODE", "rules": "RULES", "personas": {"[S1]": "ONE"}, "episode": "EP"},
            "source_text": "SOURCE",
            "word_budget": 120,
            "model_id": "mlx-community/fake-for-tests",
            "options": {"max_turn_tokens": 8},
        }
    )

    def _run(self, stub: str) -> subprocess.CompletedProcess:
        code = "\n".join(
            [
                "import sys",
                "from podcaster.writers import mlx_lm",
                stub,
                "mlx_lm.generate_script = _stub",
                "sys.exit(mlx_lm._main())",
            ]
        )
        return subprocess.run([sys.executable, "-c", code], input=self.PAYLOAD, capture_output=True, text=True)

    def test_the_script_comes_back_on_stdout_and_the_payload_survives_the_trip(self):
        proc = self._run(
            "def _stub(brief, source_text, word_budget, model_id, options):\n"
            "    assert brief.rules == 'RULES', brief\n"
            "    assert brief.personas == {'[S1]': 'ONE'}, brief\n"
            "    assert source_text == 'SOURCE' and word_budget == 120, (source_text, word_budget)\n"
            "    assert model_id == 'mlx-community/fake-for-tests' and options['max_turn_tokens'] == 8\n"
            "    return '# spine: it holds\\n[S1] a line\\n'"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "# spine: it holds\n[S1] a line\n")

    def test_a_writer_failure_in_the_child_exits_1_with_its_reason(self):
        proc = self._run(
            "from podcaster.writers import WriterUnavailable\n"
            "def _stub(*a, **k):\n"
            "    raise WriterUnavailable('mlx-lm is not installed')"
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("mlx-lm is not installed", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_a_model_outside_mlx_community_is_refused_before_any_download(self):
        # AGENTS.md makes this an ask-first decision. The payload picks which repo gets
        # downloaded, so the rule is enforced rather than only written down.
        payload = json.loads(self.PAYLOAD)
        payload["model_id"] = "some-other-org/whatever-4bit"
        code = "\n".join(
            [
                "import sys",
                "from podcaster.writers import mlx_lm",
                "def _stub(*a, **k):",
                "    raise AssertionError('must not reach the loader')",
                "mlx_lm.generate_script = _stub",
                "sys.exit(mlx_lm._main())",
            ]
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], input=json.dumps(payload), capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("mlx-community/", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_the_writer_module_holds_no_model_in_the_parent(self):
        # Importing the writer must not pull mlx in: the parent process stays clean and the
        # registry can list mlx_lm on a machine that cannot run it.
        self.assertNotIn("mlx", sys.modules)
        self.assertNotIn("mlx_lm.models", sys.modules)


if __name__ == "__main__":
    unittest.main()
