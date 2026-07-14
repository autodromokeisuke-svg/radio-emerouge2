"""filter_recent() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collect_news import filter_recent


class TestFilterRecent(unittest.TestCase):
    def test_exact_title_match_is_excluded(self) -> None:
        items = [
            {"title": "生成AIが話題に", "summary": "", "source": "s", "link": "https://example.com/1"},
            {"title": "別のニュース", "summary": "", "source": "s", "link": "https://example.com/2"},
        ]
        recent = [{"date": "20260711", "title": "生成AIが話題に", "link": "https://example.com/other"}]
        result = filter_recent(items, recent)
        self.assertEqual([it["title"] for it in result], ["別のニュース"])

    def test_exact_link_match_is_excluded_even_if_title_differs(self) -> None:
        items = [
            {"title": "見出しが変わったニュース", "summary": "", "source": "s", "link": "https://example.com/1"},
            {"title": "別のニュース", "summary": "", "source": "s", "link": "https://example.com/2"},
        ]
        recent = [{"date": "20260711", "title": "元のタイトル", "link": "https://example.com/1"}]
        result = filter_recent(items, recent)
        self.assertEqual([it["title"] for it in result], ["別のニュース"])

    def test_non_matching_items_are_kept(self) -> None:
        items = [
            {"title": "全く新しいニュース", "summary": "", "source": "s", "link": "https://example.com/3"},
        ]
        recent = [{"date": "20260711", "title": "無関係な用語", "link": "https://example.com/other"}]
        result = filter_recent(items, recent)
        self.assertEqual(result, items)

    def test_empty_recent_excludes_nothing(self) -> None:
        items = [
            {"title": "ニュースA", "summary": "", "source": "s", "link": "https://example.com/1"},
            {"title": "ニュースB", "summary": "", "source": "s", "link": "https://example.com/2"},
        ]
        result = filter_recent(items, [])
        self.assertEqual(result, items)


if __name__ == "__main__":
    unittest.main()
