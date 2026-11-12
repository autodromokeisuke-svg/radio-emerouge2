"""src/reading_rules.find_rule_hits() の単体テスト（エンジン非依存の純粋関数）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reading_rules import find_rule_hits  # noqa: E402


def _surfaces(text: str, rule: str | None = None) -> list[str]:
    return [h["surface"] for h in find_rule_hits(text) if rule is None or h["rule"] == rule]


class TestHito(unittest.TestCase):
    def test_hits(self) -> None:
        for text in ["感じてる人は多い", "って人いそう", "人による", "あの人", "人。", "人は多い"]:
            with self.subTest(text=text):
                hits = [h for h in find_rule_hits(text) if h["rule"] == "hito"]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["kana"], "ひと")
                self.assertEqual(text[hits[0]["start"]:hits[0]["end"]], "人")

    def test_non_hits(self) -> None:
        for text in ["3人", "三人", "日本人", "アメリカ人", "人間", "人気", "人次第", "人々",
                     "助っ人", "何人", "100万人", "５人", "ABC人"]:
            with self.subTest(text=text):
                self.assertEqual(_surfaces(text, "hito"), [])


class TestHoka(unittest.TestCase):
    def test_hits(self) -> None:
        for text in ["他の会社", "他にも", "他は", "この他"]:
            with self.subTest(text=text):
                hits = [h for h in find_rule_hits(text) if h["rule"] == "hoka"]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["kana"], "ほか")

    def test_non_hits(self) -> None:
        for text in ["その他", "他社", "他人", "自他ともに"]:
            with self.subTest(text=text):
                self.assertEqual(_surfaces(text, "hoka"), [])


class TestBun(unittest.TestCase):
    def test_hits(self) -> None:
        for text in ["便利な分", "作れる分、", "知ってる分だけ", "その分", "ある分には"]:
            with self.subTest(text=text):
                hits = [h for h in find_rule_hits(text) if h["rule"] == "bun"]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["kana"], "ぶん")

    def test_non_hits(self) -> None:
        for text in ["自分", "5分", "三十分", "半分", "て分かった", "を分ける", "の分野", "に分類"]:
            with self.subTest(text=text):
                self.assertEqual(_surfaces(text, "bun"), [])


class TestYuru(unittest.TestCase):
    def test_hits(self) -> None:
        for text in ["緩すぎて", "緩すぎず", "緩い", "緩やか", "緩める"]:
            with self.subTest(text=text):
                hits = [h for h in find_rule_hits(text) if h["rule"] == "yuru"]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["kana"], "ゆる")
                self.assertEqual(hits[0]["surface"], "緩")

    def test_non_hits(self) -> None:
        for text in ["緩和", "緩急"]:
            with self.subTest(text=text):
                self.assertEqual(_surfaces(text, "yuru"), [])


class TestMultipleHits(unittest.TestCase):
    def test_multiple_hits_sorted_and_positions_correct(self) -> None:
        text = "その人は他にも、便利な分だけ緩すぎて困る人がいる"
        hits = find_rule_hits(text)
        self.assertEqual([h["rule"] for h in hits], ["hito", "hoka", "bun", "yuru", "hito"])
        starts = [h["start"] for h in hits]
        self.assertEqual(starts, sorted(starts))
        for a, b in zip(hits, hits[1:]):
            self.assertLessEqual(a["end"], b["start"])  # 重なりなし
        for h in hits:
            self.assertEqual(text[h["start"]:h["end"]], h["surface"])

    def test_no_hits_returns_empty(self) -> None:
        self.assertEqual(find_rule_hits("今日はいい天気だね"), [])

    def test_hit_has_all_keys(self) -> None:
        h = find_rule_hits("あの人")[0]
        self.assertEqual(set(h), {"start", "end", "surface", "kana", "rule"})


if __name__ == "__main__":
    unittest.main()
