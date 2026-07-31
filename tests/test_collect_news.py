"""filter_recent() / collect() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collect_news import _norm_title, collect, filter_recent


def _epoch_struct(hours_ago: float):
    return time.gmtime(time.time() - hours_ago * 3600)


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

    def test_same_article_different_source_suffix_is_excluded(self) -> None:
        """同一記事がGoogle News等で情報源名だけ違う複数エントリになるケースの回帰テスト。"""
        items = [
            {"title": "疲弊する新卒採用の現場 「生成AIの書いた作文」を読まされる担当者 - 日経BOOKプラス",
             "summary": "", "source": "s", "link": "https://example.com/new-link"},
        ]
        recent = [{"date": "20260721",
                  "title": "疲弊する新卒採用の現場 「生成AIの書いた作文」を読まされる担当者 - 日経ビジネス電子版",
                  "link": "https://example.com/old-link"}]
        result = filter_recent(items, recent)
        self.assertEqual(result, [])


class TestNormTitle(unittest.TestCase):
    def test_strips_trailing_source_suffix(self) -> None:
        self.assertEqual(
            _norm_title("同じ記事 - 日経ビジネス電子版"),
            _norm_title("同じ記事 - 日経BOOKプラス"),
        )

    def test_does_not_over_strip_short_titles(self) -> None:
        # ハイフンが無いタイトルはそのまま
        self.assertNotEqual(_norm_title("全く違う記事A"), _norm_title("全く違う記事B"))


class _FakeParsed:
    def __init__(self, feed_title: str, entries: list[dict]) -> None:
        self.feed = {"title": feed_title}
        self.entries = entries


class TestCollectFreshness(unittest.TestCase):
    def test_undated_entry_in_dated_feed_is_excluded(self) -> None:
        """同じフィード内で、他の記事に日付があるのに個別記事だけ日付が無い場合、
        鮮度不明として除外されること（過去に発生した不具合の回帰テスト）。"""
        entries = [
            {"title": "新しいAIニュース", "summary": "AIの話",
             "link": "https://example.com/1", "published_parsed": _epoch_struct(1)},
            {"title": "日付不明のAIニュース", "summary": "AIの話",
             "link": "https://example.com/2"},  # published_parsed/updated_parsedが無い
        ]
        with patch("src.collect_news.feedparser.parse",
                  return_value=_FakeParsed("テストフィード", entries)):
            result = collect(["https://example.com/feed"], ["ai"])
        titles = [it["title"] for it in result]
        self.assertIn("新しいAIニュース", titles)
        self.assertNotIn("日付不明のAIニュース", titles)

    def test_old_dated_entry_is_excluded(self) -> None:
        entries = [
            {"title": "古いAIニュース", "summary": "AIの話",
             "link": "https://example.com/1", "published_parsed": _epoch_struct(48)},
        ]
        with patch("src.collect_news.feedparser.parse",
                  return_value=_FakeParsed("テストフィード", entries)):
            result = collect(["https://example.com/feed"], ["ai"])
        self.assertEqual(result, [])

    def test_feed_entirely_without_dates_uses_fallback(self) -> None:
        entries = [
            {"title": f"AIニュース{i}", "summary": "AIの話", "link": f"https://example.com/{i}"}
            for i in range(10)
        ]
        with patch("src.collect_news.feedparser.parse",
                  return_value=_FakeParsed("テストフィード", entries)):
            result = collect(["https://example.com/feed"], ["ai"])
        # PER_FEED_FALLBACK=6件までのはず
        self.assertEqual(len(result), 6)


if __name__ == "__main__":
    unittest.main()
