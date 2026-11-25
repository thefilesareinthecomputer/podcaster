import unittest

from podcaster.script_format import Turn, distinct_n, mattr, metrics


class Metrics(unittest.TestCase):
    def test_distinct_n_is_one_without_repetition_and_falls_with_it(self):
        self.assertEqual(distinct_n(list("abcdefgh"), 2, 100), 1.0)
        self.assertLess(distinct_n(["the", "same"] * 30, 2, 100), 0.1)

    def test_mattr_short_text_is_plain_ttr_and_repetition_lowers_it(self):
        self.assertEqual(mattr(["a", "b", "c", "d"], 50), 1.0)
        self.assertLess(mattr(["a"] * 200, 50), 0.05)

    def test_metrics_shape_and_known_values(self):
        turns = [Turn("S1", "Why does it hold?"), Turn("S2", "Because the record says so."), Turn("S1", "Then it holds.")]
        m = metrics(turns)
        self.assertEqual(set(m), {"distinct_2", "mattr_50", "turn_words_mean", "turn_words_max", "question_share", "turn_share"})
        self.assertEqual(m["turn_words_max"], 5)
        self.assertAlmostEqual(m["turn_words_mean"], 4.0)
        self.assertAlmostEqual(m["question_share"], 1 / 3, places=3)
        self.assertAlmostEqual(m["turn_share"]["S1"], 2 / 3, places=3)
        self.assertEqual(m["distinct_2"], 1.0)


if __name__ == "__main__":
    unittest.main()
