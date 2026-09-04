"""glossary_history.json 周りの単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.make_feed import (
    _write_index,
    _episode_meta,
    load_recent_glossary_terms,
    record_glossary_term,
    load_recent_news_titles,
    record_used_news,
)

JST = timezone(timedelta(hours=9))


def _recent_date_key(days_ago: int = 2) -> str:
    """days_ago日前の日付キー(YYYYMMDD)。

    テストに日付を直書きすると、時間の経過で days 窓（用語30日・ニュース7日）から
    外れてテストが恒常的に落ちる。実際に 20260707 直書きの4件がそうなっていた。
    """
    return (datetime.now(JST) - timedelta(days=days_ago)).strftime("%Y%m%d")


class TestGlossaryHistory(unittest.TestCase):
    def test_record_then_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            date_key = _recent_date_key()
            record_glossary_term(site, date_key, "フィジカルAI")
            terms = load_recent_glossary_terms(site, days=30)
            self.assertEqual(terms, [{"date": date_key, "term": "フィジカルAI"}])

    def test_old_entries_excluded_by_days_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old_date = (datetime.now(JST) - timedelta(days=40)).strftime("%Y%m%d")
            recent_date = (datetime.now(JST) - timedelta(days=5)).strftime("%Y%m%d")
            (site / "glossary_history.json").write_text(
                json.dumps([
                    {"date": old_date, "term": "古い用語"},
                    {"date": recent_date, "term": "新しい用語"},
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            terms = load_recent_glossary_terms(site, days=30)
            self.assertEqual(terms, [{"date": recent_date, "term": "新しい用語"}])

    def test_record_same_date_key_overwrites_not_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            date_key = _recent_date_key()
            record_glossary_term(site, date_key, "フィジカルAI")
            record_glossary_term(site, date_key, "OCR")
            terms = load_recent_glossary_terms(site, days=30)
            self.assertEqual(terms, [{"date": date_key, "term": "OCR"}])

    def test_load_returns_empty_list_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            terms = load_recent_glossary_terms(site, days=30)
            self.assertEqual(terms, [])

    def test_load_returns_empty_list_when_file_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "glossary_history.json").write_text("{not valid json", encoding="utf-8")
            terms = load_recent_glossary_terms(site, days=30)
            self.assertEqual(terms, [])

    def test_record_with_empty_term_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            record_glossary_term(site, "20260707", "")
            self.assertFalse((site / "glossary_history.json").exists())


class TestNewsHistory(unittest.TestCase):
    def test_record_then_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            date_key = _recent_date_key()
            record_used_news(site, date_key, [
                {"title": "AI速報", "link": "https://example.com/a"},
                {"title": "AI速報2", "link": "https://example.com/b"},
            ])
            news = load_recent_news_titles(site, days=30)
            self.assertEqual(news, [
                {"date": date_key, "title": "AI速報", "link": "https://example.com/a"},
                {"date": date_key, "title": "AI速報2", "link": "https://example.com/b"},
            ])

    def test_old_entries_excluded_by_days_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old_date = (datetime.now(JST) - timedelta(days=20)).strftime("%Y%m%d")
            recent_date = (datetime.now(JST) - timedelta(days=3)).strftime("%Y%m%d")
            (site / "news_history.json").write_text(
                json.dumps([
                    {"date": old_date, "title": "古いニュース", "link": ""},
                    {"date": recent_date, "title": "新しいニュース", "link": ""},
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            news = load_recent_news_titles(site, days=7)
            self.assertEqual(news, [{"date": recent_date, "title": "新しいニュース", "link": ""}])

    def test_record_same_date_key_overwrites_not_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            date_key = _recent_date_key()
            record_used_news(site, date_key, [{"title": "A", "link": ""}])
            record_used_news(site, date_key, [{"title": "B", "link": ""}, {"title": "C", "link": ""}])
            news = load_recent_news_titles(site, days=30)
            self.assertEqual(news, [
                {"date": date_key, "title": "B", "link": ""},
                {"date": date_key, "title": "C", "link": ""},
            ])

    def test_load_returns_empty_list_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            news = load_recent_news_titles(site, days=7)
            self.assertEqual(news, [])


class TestEpisodeMetaRobustness(unittest.TestCase):
    """必須キー（dateなど）が欠けたエピソードメタ情報でKeyErrorにならず、
    そのファイルだけスキップされること（Codexの指摘に基づく回帰テスト）。"""

    def test_meta_missing_required_key_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            episodes = site / "episodes"
            episodes.mkdir()
            valid = {"date": "20260707", "pub": "2026-07-07T00:00:00+09:00",
                    "title": "t", "description": "d", "file": "radio-20260707.mp3", "bytes": 1}
            (episodes / "radio-20260707.json").write_text(
                json.dumps(valid, ensure_ascii=False), encoding="utf-8")
            broken = {"pub": "2026-07-08T00:00:00+09:00",
                     "title": "t2", "description": "d2", "file": "radio-20260708.mp3", "bytes": 1}
            (episodes / "radio-20260708.json").write_text(
                json.dumps(broken, ensure_ascii=False), encoding="utf-8")
            metas = _episode_meta(site)
            self.assertEqual(metas, [valid])


if __name__ == "__main__":
    unittest.main()


class TestHistoryLoadersSinceFilter(unittest.TestCase):
    """since（公開開始日）より前の放送履歴を読み込まないこと。

    非公開の試験運用期間(2026-08)の履歴が台本生成AIへ渡ると、リスナーの知らない
    放送へ言及してしまうため（2026-09-03に実発生）。
    """

    def _dates(self) -> tuple[str, str]:
        """days窓には確実に入るが、公開開始日を挟む2日分を返す（古い, 新しい）。"""
        now = datetime.now(JST)
        return ((now - timedelta(days=3)).strftime("%Y%m%d"),
                (now - timedelta(days=1)).strftime("%Y%m%d"))

    def test_glossary_since_excludes_older_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            before, after = self._dates()
            (site / "glossary_history.json").write_text(
                json.dumps([{"date": before, "term": "非公開期間の用語"},
                            {"date": after, "term": "公開後の用語"}], ensure_ascii=False),
                encoding="utf-8")
            terms = load_recent_glossary_terms(site, days=30, since=after)
            self.assertEqual(terms, [{"date": after, "term": "公開後の用語"}])

    def test_news_since_excludes_older_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            before, after = self._dates()
            (site / "news_history.json").write_text(
                json.dumps([{"date": before, "title": "非公開期間のニュース", "link": ""},
                            {"date": after, "title": "公開後のニュース", "link": ""}],
                           ensure_ascii=False),
                encoding="utf-8")
            titles = [n["title"] for n in load_recent_news_titles(site, days=7, since=after)]
            self.assertEqual(titles, ["公開後のニュース"])

    def test_empty_since_keeps_everything_in_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            before, after = self._dates()
            (site / "news_history.json").write_text(
                json.dumps([{"date": before, "title": "古い", "link": ""},
                            {"date": after, "title": "新しい", "link": ""}], ensure_ascii=False),
                encoding="utf-8")
            self.assertEqual(len(load_recent_news_titles(site, days=7, since="")), 2)

class TestOgpTags(unittest.TestCase):
    """番組ページのOGP（X等での共有カード）タグ。"""

    SHOW = {"title": "デイリーAIニュース RADIOえめるーじぇ",
            "description": "毎朝のAIニュース番組。",
            "author": "えめるーじぇ", "credit": "音声クレジット"}
    BASE = "https://example.github.io/radio"

    def _render(self, has_cover=True, base_url=BASE):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            metas = [{"date": "20260904", "pub": "Fri, 04 Sep 2026 06:00:00 +0900",
                      "title": "デイリーAIニュース 9月4日号（9/4）",
                      "description": "今日の話題: A / B", "file": "radio-20260904.mp3",
                      "bytes": 100, "duration_sec": 60}]
            _write_index(site, metas, self.SHOW, has_cover, {}, base_url)
            return (site / "index.html").read_text(encoding="utf-8")

    def test_ogp_tags_present(self):
        """og:title/description/url/image と twitter:card が出力される。"""
        out = self._render()
        self.assertIn('<meta property="og:title" content="デイリーAIニュース RADIOえめるーじぇ">', out)
        self.assertIn('<meta property="og:description" content="毎朝のAIニュース番組。">', out)
        self.assertIn(f'<meta property="og:url" content="{self.BASE}/">', out)
        self.assertIn(f'<meta property="og:image" content="{self.BASE}/cover.jpg">', out)
        # カバーは1:1なので、2:1に切り抜かれるlarge系ではなくsummaryを使う
        self.assertIn('<meta name="twitter:card" content="summary">', out)
        self.assertNotIn("summary_large_image", out)

    def test_no_image_tag_without_cover(self):
        """カバー画像が無いときは og:image を出さない（404画像を指さない）。"""
        out = self._render(has_cover=False)
        self.assertNotIn("og:image", out)
        self.assertIn('<meta property="og:title"', out)

    def test_base_url_trailing_slash_is_normalized(self):
        """base_urlの末尾スラッシュ有無でURLが二重スラッシュにならない。"""
        out = self._render(base_url=self.BASE + "/")
        self.assertIn(f'<meta property="og:url" content="{self.BASE}/">', out)
        self.assertNotIn("radio//", out)

    def test_no_ogp_when_base_url_missing(self):
        """base_urlが空なら空URLのタグを出さずに済ませる。"""
        out = self._render(base_url="")
        self.assertNotIn("og:title", out)

    def test_title_is_escaped(self):
        """タイトルにHTML特殊文字が入ってもcontent属性を壊さない。"""
        show = dict(self.SHOW, title='AI & <script>')
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            _write_index(site, [], show, True, {}, self.BASE)
            out = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn("AI &amp; &lt;script&gt;", out)
        self.assertNotIn("<script>", out)
