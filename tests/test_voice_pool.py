import tempfile
import unittest
from pathlib import Path

from podcaster import paths, render


def show(d: str, pool: str, hosts: str) -> Path:
    root = Path(d)
    (root / "voices").mkdir()
    (root / "voices" / "voices.toml").write_text(pool)
    (root / "config.toml").write_text(f"[writer]\nrules = 'r'\n{hosts}")
    return root


class VoicePool(unittest.TestCase):
    def test_host_voice_id_pulls_every_engine_key_and_host_keys_win(self):
        with tempfile.TemporaryDirectory() as d:
            root = show(
                d,
                '[warm]\ndescription = "d"\nkokoro_voice = "af_heart"\nhiggs_ref_audio = "a.wav"\nhiggs_ref_text = "t"\n',
                '[hosts.s1]\nvoice = "warm"\nkokoro_voice = "af_bella"\n',
            )
            cfg = render.load_config(root / "config.toml")
            h = cfg["hosts"]["s1"]
            self.assertEqual(h["kokoro_voice"], "af_bella")
            self.assertEqual(h["higgs_ref_text"], "t")
            self.assertEqual(h["higgs_ref_audio"], str(root.resolve() / "voices" / "a.wav"))
            self.assertNotIn("description", h)
            self.assertEqual(cfg["voices"]["warm"]["description"], "d")

    def test_unknown_voice_id_names_the_pool(self):
        with tempfile.TemporaryDirectory() as d:
            root = show(d, '[warm]\nkokoro_voice = "x"\n', '[hosts.s1]\nvoice = "cold"\n')
            with self.assertRaises(KeyError) as ctx:
                render.load_config(root / "config.toml")
            self.assertIn("warm", str(ctx.exception))

    def test_no_pool_file_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config.toml").write_text('[hosts.s1]\nkokoro_voice = "x"\n')
            self.assertEqual(render.load_config(Path(d) / "config.toml")["voices"], {})

    def test_template_config_resolves_both_hosts(self):
        cfg = render.load_config(paths.TEMPLATE / "config.toml")
        for s in ("s1", "s2"):
            self.assertIn("kokoro_voice", cfg["hosts"][s])
            self.assertTrue(cfg["hosts"][s]["higgs_ref_audio"].endswith(".wav"))


if __name__ == "__main__":
    unittest.main()
