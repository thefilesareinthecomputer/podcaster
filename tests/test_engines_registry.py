import sys
import unittest

from podcaster import engines
from podcaster.engines import higgs


class Registry(unittest.TestCase):
    def test_16gb_machine_gets_apple_and_kokoro_only(self):
        self.assertEqual(engines.available_on_this_machine(memory_gb=16), ["apple", "kokoro"])

    def test_64gb_machine_gets_everything(self):
        self.assertEqual(engines.available_on_this_machine(memory_gb=64), ["apple", "kokoro", "higgs"])

    def test_higgs_refuses_on_16gb_naming_the_need(self):
        with self.assertRaises(engines.EngineUnavailable) as ctx:
            engines.load("higgs", memory_gb=16)
        self.assertIn("needs about 24GB", str(ctx.exception))

    def test_unknown_engine_names_the_choices(self):
        with self.assertRaises(engines.EngineUnavailable) as ctx:
            engines.load("elevenlabs", memory_gb=64)
        self.assertIn("apple", str(ctx.exception))

    def test_registry_does_not_import_mlx(self):
        self.assertNotIn("mlx_audio", sys.modules)
        self.assertNotIn("mlx", sys.modules)


class HiggsChunking(unittest.TestCase):
    def test_packs_whole_sentences_under_the_cap(self):
        text = "One two three. Four five six! Seven eight nine? Ten."
        self.assertEqual(higgs.chunk_text(text, max_chars=32), ["One two three. Four five six!", "Seven eight nine? Ten."])

    def test_short_turn_is_one_chunk(self):
        self.assertEqual(higgs.chunk_text("Just one line."), ["Just one line."])

    def test_oversized_sentence_stands_alone(self):
        long = "word " * 80
        self.assertEqual(higgs.chunk_text(f"Short. {long.strip()}. End.", max_chars=50), ["Short.", long.strip() + ".", "End."])

    def test_voice_is_the_reference_clip_path(self):
        self.assertEqual(higgs.voice_for({"higgs_ref_audio": "voices/s1.wav"}), "voices/s1.wav")


if __name__ == "__main__":
    unittest.main()
