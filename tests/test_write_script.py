"""_format_recent_terms_block() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.write_script import _format_recent_terms_block, _validate


class TestValidateCoveredNewsTitles(unittest.TestCase):
    def _base_lines(self) -> list[dict[str, str]]:
        return [{"speaker": "eme" if i % 2 == 0 else "ruje", "text": f"セリフ{i}"}
                for i in range(8)]

    def test_covered_news_titles_included_as_list(self) -> None:
        data = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "covered_news_titles": ["ニュース見出し1", "ニュース見出し2"],
            "lines": self._base_lines(),
        }
        result = _validate(data)
        self.assertEqual(result["covered_news_titles"], ["ニュース見出し1", "ニュース見出し2"])

    def test_missing_or_invalid_covered_news_titles_defaults_to_empty_list(self) -> None:
        data_missing = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "lines": self._base_lines(),
        }
        self.assertEqual(_validate(data_missing)["covered_news_titles"], [])

        data_wrong_type = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "covered_news_titles": "ニュース見出し1",
            "lines": self._base_lines(),
        }
        self.assertEqual(_validate(data_wrong_type)["covered_news_titles"], [])

        data_mixed_types = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "covered_news_titles": ["有効な見出し", 123, None],
            "lines": self._base_lines(),
        }
        self.assertEqual(_validate(data_mixed_types)["covered_news_titles"], ["有効な見出し"])


class TestFormatRecentTermsBlock(unittest.TestCase):
    def test_empty_list_returns_placeholder(self) -> None:
        self.assertEqual(_format_recent_terms_block([]), "（まだ無し）")

    def test_terms_include_date_and_term(self) -> None:
        result = _format_recent_terms_block([
            {"date": "20260707", "term": "フィジカルAI"},
            {"date": "20260620", "term": "OCR"},
        ])
        self.assertIn("2026-07-07", result)
        self.assertIn("フィジカルAI", result)
        self.assertIn("2026-06-20", result)
        self.assertIn("OCR", result)

    def test_terms_are_ordered_newest_first(self) -> None:
        result = _format_recent_terms_block([
            {"date": "20260620", "term": "OCR"},
            {"date": "20260707", "term": "フィジカルAI"},
        ])
        self.assertLess(result.index("2026-07-07"), result.index("2026-06-20"))


if __name__ == "__main__":
    unittest.main()
