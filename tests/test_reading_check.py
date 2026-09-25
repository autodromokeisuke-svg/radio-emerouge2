"""src/reading_check.py v2 の単体テスト（標準ライブラリ unittest のみ使用）。

normalize_kana()の表記ゆれ吸収と、find_misreadings()のJSONパースの
堅牢性（本番で実際に発生した壊れ方を再現）を中心に見る。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reading_check import (
    extract_phrases,
    extract_reading,
    find_misreadings,
    kana_contains,
    normalize_kana,
    to_hiragana,
    to_katakana,
)


class TestNormalizeKana(unittest.TestCase):
    def test_long_vowel_spellings_all_match(self) -> None:
        # 母音の繰り返し(トウキョウ)・音引き(トーキョー)・エンジン表記(トオキョオ)は同じ読み
        forms = ["トウキョウ", "トーキョー", "トオキョオ"]
        normalized = {normalize_kana(f) for f in forms}
        self.assertEqual(len(normalized), 1)

    def test_e_row_long_vowel(self) -> None:
        self.assertEqual(normalize_kana("センセイ"), normalize_kana("センセー"))

    def test_hiragana_is_normalized_to_katakana(self) -> None:
        self.assertEqual(normalize_kana("とうきょう"), normalize_kana("トウキョウ"))

    def test_punctuation_and_phrase_boundary_are_ignored(self) -> None:
        self.assertEqual(normalize_kana("オモウカ／タイソオダヨネ"),
                         normalize_kana("オモウカタ／イソオダヨネ"))

    def test_wo_du_di_are_normalized(self) -> None:
        self.assertEqual(normalize_kana("ヲ"), "オ")
        self.assertEqual(normalize_kana("ヅ"), "ズ")
        self.assertEqual(normalize_kana("ヂ"), "ジ")

    def test_non_kana_symbols_are_dropped(self) -> None:
        # isolated_word_reading等の付記「(3)」のような記号・数字は比較対象に含めない
        self.assertEqual(normalize_kana("サイテキカ(3)"), "サイテキカ")
        self.assertEqual(normalize_kana("最適化"), "")  # 漢字はそもそもカタカナではない

    def test_containment_check(self) -> None:
        self.assertIn(normalize_kana("ブン"), normalize_kana("ベンリニ／ナル／ブン,"))
        self.assertNotIn(normalize_kana("ブン"), normalize_kana("ベンリニ／ナル／ワケ,"))


class TestToHiragana(unittest.TestCase):
    def test_katakana_word_becomes_hiragana(self) -> None:
        self.assertEqual(to_hiragana("サイテキカ"), "さいてきか")

    def test_long_vowel_mark_is_unchanged(self) -> None:
        self.assertEqual(to_hiragana("カオー"), "かおー")


class TestToKatakana(unittest.TestCase):
    def test_hiragana_word_becomes_katakana(self) -> None:
        self.assertEqual(to_katakana("さいてきか"), "サイテキカ")

    def test_already_katakana_is_unchanged(self) -> None:
        self.assertEqual(to_katakana("ブン"), "ブン")

    def test_mixed_kana_only_hiragana_part_converts(self) -> None:
        self.assertEqual(to_katakana("ブンだけ"), "ブンダケ")


class TestKanaContains(unittest.TestCase):
    def test_plain_containment(self) -> None:
        self.assertTrue(kana_contains("ベンリニナルブン", "ブン"))
        self.assertFalse(kana_contains("ベンリニナルワケ", "ブン"))

    def test_boundary_vowel_fold_is_absorbed(self) -> None:
        # F4: 「ノ」の後の「ウ」は正規化で「オ」に畳み込まれるため、素朴な
        # normalize_kana(needle)だけの比較だと「ウエ」を含むと判定できない。
        # kana_containsは先頭ウ→オ／イ→エの読み替えも試すので検出できる。
        self.assertTrue(kana_contains("ツクエノウエニオイテネ", "ウエ"))
        # 素朴な「両方normalize_kanaしてinで比較」だけでは検出できないことの確認
        # （kana_containsが境界ゆれを追加で吸収して初めて見つかる）
        self.assertNotIn(normalize_kana("ウエ"), normalize_kana("ツクエノウエニオイテネ"))

    def test_leading_i_after_e_row_is_absorbed(self) -> None:
        # 「テ」(エ段)の直後の「イ」は正規化で「エ」に畳み込まれる（テイエ→テエエ）ため、
        # needle「イエ」単体の畳み込み（イエのまま）だけでは見つけられない
        self.assertTrue(kana_contains("テイエ", "イエ"))

    def test_empty_needle_is_false(self) -> None:
        self.assertFalse(kana_contains("ナニカ", ""))

    def test_hiragana_input_is_folded_too(self) -> None:
        self.assertTrue(kana_contains("べんりになるぶん", "ブン"))


class TestExtractPhrasesAndReading(unittest.TestCase):
    _QUERY = {
        "accent_phrases": [
            {"moras": [{"text": "ハイ"}, {"text": "ハアイ"}], "pause_mora": None},
            {"moras": [{"text": "ハジマリマシタ"}], "pause_mora": {"text": "、"}},
        ]
    }

    def test_extract_reading_concatenates_without_pause(self) -> None:
        self.assertEqual(extract_reading(self._QUERY), "ハイハアイハジマリマシタ")

    def test_extract_phrases_joins_with_slash_and_marks_pause(self) -> None:
        self.assertEqual(extract_phrases(self._QUERY), "ハイハアイ／ハジマリマシタ、")


class TestFindMisreadings(unittest.TestCase):
    def _pairs(self) -> list[dict]:
        return [{"index": 1, "text": "分かりやすい分だけ", "phrases": "ワケ／ダケ"}]

    def _resp(self, text: str, stop_reason: str = "end_turn") -> MagicMock:
        resp = MagicMock()
        resp.content = [MagicMock(type="text", text=text)]
        resp.usage = MagicMock(input_tokens=10, output_tokens=5)
        resp.stop_reason = stop_reason
        return resp

    def test_parses_plain_json(self) -> None:
        text = '{"corrections": [{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}]}'
        with patch("src.reading_check.Anthropic") as A:
            A.return_value.messages.create.return_value = self._resp(text)
            result = find_misreadings(self._pairs(), "claude-sonnet-4-6")
        self.assertEqual(result, [{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}])

    def test_parses_json_wrapped_in_code_fence(self) -> None:
        text = '```json\n{"corrections": [{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}]}\n```'
        with patch("src.reading_check.Anthropic") as A:
            A.return_value.messages.create.return_value = self._resp(text)
            result = find_misreadings(self._pairs(), "claude-sonnet-4-6")
        self.assertEqual(len(result), 1)

    def test_ignores_trailing_data_after_first_json_object(self) -> None:
        """本番障害の再現: 応答に2つ目のJSONオブジェクトや説明文が続くケース
        （"Extra data: line 3 column 1"）。最初の1つだけを使って復旧できること。"""
        text = ('{"corrections": [{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}]}\n'
               '{"corrections": []}')
        with patch("src.reading_check.Anthropic") as A:
            A.return_value.messages.create.return_value = self._resp(text)
            result = find_misreadings(self._pairs(), "claude-sonnet-4-6")
        self.assertEqual(len(result), 1)

    def test_drops_invalid_items(self) -> None:
        text = ('{"corrections": ['
               '{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"},'
               '{"index": "1", "surface": "分", "heard": "ワケ", "correct": "ブン"},'
               '{"index": 2, "surface": "", "heard": "ワケ", "correct": "ブン"},'
               '{"index": 3, "surface": "分"}'
               ']}')
        with patch("src.reading_check.Anthropic") as A:
            A.return_value.messages.create.return_value = self._resp(text)
            result = find_misreadings(self._pairs(), "claude-sonnet-4-6")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["index"], 1)

    def test_api_exception_returns_empty_list(self) -> None:
        with patch("src.reading_check.Anthropic", side_effect=RuntimeError("boom")):
            result = find_misreadings(self._pairs(), "claude-sonnet-4-6")
        self.assertEqual(result, [])

    def test_no_json_in_response_returns_empty_list(self) -> None:
        with patch("src.reading_check.Anthropic") as A:
            A.return_value.messages.create.return_value = self._resp("すみません、わかりません")
            result = find_misreadings(self._pairs(), "claude-sonnet-4-6")
        self.assertEqual(result, [])

    def test_empty_pairs_returns_empty_list_without_calling_api(self) -> None:
        with patch("src.reading_check.Anthropic") as A:
            result = find_misreadings([], "claude-sonnet-4-6")
        self.assertEqual(result, [])
        A.assert_not_called()


if __name__ == "__main__":
    unittest.main()
