import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support
from podcaster import cli, guard, paths, pipeline, render

# Every test in this file that reaches the pipeline or the render takes the machine lock.
# Point it at a scratch file so a test run never contends with a real render on this machine.
_LOCK_DIR = tempfile.TemporaryDirectory()
guard.LOCK_FILE = Path(_LOCK_DIR.name) / "test.lock"


class Dispatch(unittest.TestCase):
    def test_render_passes_its_flags_through(self):
        with mock.patch.object(render, "main", return_value=0) as m:
            code = cli.main(["render", "--script", "x.md", "--engine", "apple"])
        m.assert_called_once_with(["--script", "x.md", "--engine", "apple"])
        self.assertEqual(code, 0)

    def test_render_returns_the_wrapped_exit_code(self):
        with mock.patch.object(render, "main", return_value=3):
            self.assertEqual(cli.main(["render", "--script", "x.md"]), 3)

    def test_run_dispatches_to_the_pipeline(self):
        with mock.patch.object(pipeline, "main", return_value=0) as m:
            code = cli.main(["run", "--episode", "A"])
        m.assert_called_once_with(["--episode", "A"])
        self.assertEqual(code, 0)

    def test_write_adds_write_only(self):
        with mock.patch.object(pipeline, "main", return_value=5) as m:
            code = cli.main(["write", "--episode", "A"])
        m.assert_called_once_with(["--episode", "A", "--write-only"])
        self.assertEqual(code, 5)

    def test_no_subcommand_exits_2(self):
        with mock.patch("sys.stdout"):
            self.assertEqual(cli.main([]), 2)

    def test_init_without_a_folder_exits_2(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as ctx:
            cli.main(["init"])
        self.assertEqual(ctx.exception.code, 2)

    def test_help_on_init_and_shows_prints_help_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("pathlib.Path.cwd", return_value=Path(d)):
            for command in ("init", "shows"):
                with mock.patch("sys.stdout"), self.assertRaises(SystemExit) as ctx:
                    cli.main([command, "--help"])
                self.assertEqual(ctx.exception.code, 0)
            self.assertEqual(list(Path(d).iterdir()), [])


class Init(unittest.TestCase):
    def test_scaffolds_folders_and_copies_config_without_overwriting(self):
        with tempfile.TemporaryDirectory() as d:
            show = Path(d) / "history"
            made = cli.init_show(show)
            for sub in paths.SHOW_DIRS:
                self.assertTrue((show / sub).is_dir(), sub)
            self.assertIn("config.toml", made)
            (show / "config.toml").write_text("custom\n")
            cli.init_show(show)
            self.assertEqual((show / "config.toml").read_text(), "custom\n")

    def test_copies_every_mode_brief_the_template_has(self):
        with tempfile.TemporaryDirectory() as d:
            show = Path(d) / "history"
            cli.init_show(show)
            briefs = {p.name for p in (show / "prompts" / "briefs").glob("*.md")}
            self.assertEqual(briefs, {p.name for p in (support.TEMPLATE / "prompts" / "briefs").glob("*.md")})

    def test_reports_missing_prompts_when_template_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            template = Path(d) / "t"
            (template / "prompts" / "briefs").mkdir(parents=True)
            (template / "config.toml").write_text("[episode]\n")
            made = cli.init_show(Path(d) / "show", template=template)
            self.assertEqual(
                [m for m in made if m.startswith("MISSING")],
                [f"MISSING prompts/{n}" for n in cli.PROMPT_FILES],
            )

    def test_lists_shows_under_a_parent(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("b", "a"):
                cli.init_show(Path(d) / name)
            (Path(d) / "not-a-show").mkdir()
            self.assertEqual([p.name for p in cli.list_shows(Path(d))], ["a", "b"])


class ShowRoot(unittest.TestCase):
    def test_relative_config_paths_resolve_against_the_show_folder(self):
        with tempfile.TemporaryDirectory() as d:
            show = Path(d)
            (show / "config.toml").write_text(
                '[writer]\nrules = "prompts/rules.md"\ns1 = "prompts/s1.md"\ns2 = "prompts/s2.md"\n'
                '[modes.briefing]\nbrief = "prompts/briefs/briefing.md"\n'
                '[hosts.s1]\nhiggs_ref_audio = "voices/s1.wav"\n[hosts.s2]\nname = "x"\n'
            )
            cfg = render.load_config(show / "config.toml")
            self.assertEqual(cfg["_root"], str(show.resolve()))
            self.assertEqual(cfg["writer"]["rules"], str(show.resolve() / "prompts/rules.md"))
            self.assertEqual(cfg["modes"]["briefing"]["brief"], str(show.resolve() / "prompts/briefs/briefing.md"))
            self.assertEqual(cfg["hosts"]["s1"]["higgs_ref_audio"], str(show.resolve() / "voices/s1.wav"))
            self.assertNotIn("higgs_ref_audio", cfg["hosts"]["s2"])

    def test_template_config_paths_resolve_against_the_template(self):
        cfg = render.load_config(support.TEMPLATE / "config.toml")
        self.assertEqual(cfg["_root"], str(support.TEMPLATE.resolve()))
        self.assertTrue(Path(cfg["modes"]["briefing"]["brief"]).is_file())
        self.assertTrue(Path(cfg["writer"]["rules"]).is_file())

    def test_show_flag_beats_the_environment_which_beats_the_default(self):
        with mock.patch.dict(os.environ, {paths.SHOW_ENV: "/tmp/from-env"}):
            self.assertEqual(paths.show_root(Path("/tmp/from-flag")), Path("/tmp/from-flag"))
            self.assertEqual(paths.show_root(), Path("/tmp/from-env"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.show_root(), paths.DEFAULT_SHOW)

    def test_the_default_show_is_created_on_first_use(self):
        with tempfile.TemporaryDirectory() as d:
            default = Path(d) / "podcasts" / "default"
            with (
                mock.patch.object(paths, "DEFAULT_SHOW", default),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("sys.stderr"),
            ):
                config = paths.show_config()
            self.assertEqual(config, default / "config.toml")
            self.assertTrue(config.is_file())

    def test_a_named_show_is_never_created(self):
        with tempfile.TemporaryDirectory() as d:
            named = Path(d) / "typo"
            self.assertEqual(paths.show_config(named), named / "config.toml")
            self.assertFalse(named.exists())

    def test_run_against_an_empty_show_exits_4_no_sources(self):
        with tempfile.TemporaryDirectory() as d:
            show = Path(d) / "s"
            cli.init_show(show)
            with mock.patch("sys.stderr"):
                code = cli.main(["run", "--show", str(show), "--source", str(show / "in")])
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
