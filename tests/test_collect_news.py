"""filter_recent() / collect() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collect_news import MAX_PER_PUBLISHER, _norm_title, collect, filter_recent

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "google_news_spam_2026-10-23.json"


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

    def test_future_dated_entry_is_excluded(self) -> None:
        """フィード側の日時誤りやタイムゾーン不整合で未来日付になった記事が
        素通りしないこと（Codexの指摘に基づく回帰テスト）。"""
        entries = [
            {"title": "未来のAIニュース", "summary": "AIの話",
             "link": "https://example.com/1", "published_parsed": _epoch_struct(-5)},
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


def _entry(title: str, source: str | None = None, summary: str = "AIの話",
           link: str = "") -> dict:
    e = {"title": title, "summary": summary, "link": link or f"https://example.com/{abs(hash(title))}",
         "published_parsed": _epoch_struct(0.05)}
    if source is not None:
        e["source"] = {"title": source}
    return e


def _run(*feeds: tuple[str, list[dict]]) -> list[dict]:
    """feeds: (フィード名, entries) の列。feedparser.parse をモックして collect() を通す。"""
    parsed = {f"https://example.com/feed{i}": _FakeParsed(name, entries)
              for i, (name, entries) in enumerate(feeds)}
    with patch("src.collect_news.feedparser.parse", side_effect=lambda u: parsed[u]):
        return collect(list(parsed), ["ai"])


class TestBlockedTerms(unittest.TestCase):
    def test_title_with_term_is_dropped(self) -> None:
        r = _run(("f", [_entry("AI彼女 アプリ比較 - 何とか局", "何とか局"),
                        _entry("AIが新モデルを発表 - Reuters", "Reuters")]))
        self.assertEqual([it["title"] for it in r], ["AIが新モデルを発表 - Reuters"])

    def test_summary_only_term_is_kept(self) -> None:
        r = _run(("f", [_entry("AI規制の最新動向", "Reuters", summary="AIポルノ対策にも触れた")]))
        self.assertEqual(len(r), 1)

    def test_width_and_case_variants_are_dropped(self) -> None:
        titles = ["ＰＯＲＮ AI ツール", "NSFW AI generator", "Nsfw AI image", "AI アダルト動画", "AI ＮＳＦＷ"]
        r = _run(("f", [_entry(t, f"出典{i}") for i, t in enumerate(titles)]))
        self.assertEqual(r, [])


class TestPublisherCap(unittest.TestCase):
    def test_same_publisher_limited(self) -> None:
        r = _run(("f", [_entry(f"AI記事{i}", "同じ社") for i in range(10)]))
        self.assertEqual(len(r), MAX_PER_PUBLISHER)
        self.assertEqual([it["title"] for it in r], ["AI記事0", "AI記事1", "AI記事2"])

    def test_cap_is_across_feeds(self) -> None:
        r = _run(("f1", [_entry(f"AI記事A{i}", "同じ社") for i in range(2)]),
                 ("f2", [_entry(f"AI記事B{i}", "同じ社") for i in range(5)]))
        self.assertEqual(len(r), MAX_PER_PUBLISHER)

    def test_different_publishers_each_get_cap(self) -> None:
        r = _run(("f", [_entry(f"AI記事{p}{i}", f"社{p}") for p in "XY" for i in range(5)]))
        self.assertEqual(len(r), MAX_PER_PUBLISHER * 2)

    def test_publisher_normalized(self) -> None:
        r = _run(("f", [_entry(f"AI記事{i}", n) for i, n in
                        enumerate(["Reuters", "ＲＥＵＴＥＲＳ", "reuters ", "Reu ters", "Reuters"])]))
        self.assertEqual(len(r), MAX_PER_PUBLISHER)

    def test_falls_back_to_title_suffix(self) -> None:
        r = _run(("別フィード名", [_entry(f"AI記事{i} - 末尾社") for i in range(6)]))
        self.assertEqual(len(r), MAX_PER_PUBLISHER)
        r = _run(("f", [_entry(f"AI記事{i} - 社{i}") for i in range(6)]))
        self.assertEqual(len(r), 6)

    def test_unidentified_publisher_is_not_capped(self) -> None:
        # ITmedia・Publickey・TechCrunch のような単独出典のフィード（entry.sourceも
        # タイトル末尾の出典も無い）を、フィード名で数えて絞らない
        r = _run(("フィードP", [_entry(f"AI記事P{i}") for i in range(8)]),
                 ("フィードQ", [_entry(f"AI記事Q{i}") for i in range(8)]))
        self.assertEqual(len(r), 16)

    def test_hyphen_inside_a_word_is_not_a_publisher(self) -> None:
        # 区切りの前後に空白が無い「GPT-5」「Claude-4」を出典名として数えない
        r = _run(("f", [_entry(f"AI{i}モデルGPT-5 の新機能") for i in range(6)]))
        self.assertEqual(len(r), 6)
        r = _run(("f", [_entry(f"AIの話題{i} - 末尾社") for i in range(6)]))
        self.assertEqual(len(r), MAX_PER_PUBLISHER)

    def test_spam_publisher_without_blocked_terms_is_capped(self) -> None:
        spam = [_entry(f"最強の無料動画生成AIツール{i}選 - Universidad de Córdoba")
                for i in range(5)]
        real = [_entry("AI新製品発表 - 朝日新聞", "朝日新聞")]
        r = _run(("Google", spam + real))
        self.assertEqual(sum("Córdoba" in it["title"] for it in r), MAX_PER_PUBLISHER)
        self.assertEqual(sum("朝日新聞" in it["title"] for it in r), 1)


class TestSpamFixture(unittest.TestCase):
    def setUp(self) -> None:
        d = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        self.entries = []
        for e in d["entries"]:
            self.entries.append({**e, "source": {"title": e["source"]},
                                 "published_parsed": _epoch_struct(0.05)})
        self.result = _run(("Google ニュース", self.entries))

    def test_hokuriku_capped(self) -> None:
        n = sum(it["title"].endswith("北陸地方整備局") for it in self.result)
        self.assertLessEqual(n, MAX_PER_PUBLISHER)
        self.assertEqual(n, 0)  # 実フィードでは全件が語の除外で落ちる

    def test_real_publishers_not_dropped(self) -> None:
        # keyword_filter("ai")を通る北陸以外の記事。全角「ＡＩ」だけの見出しは
        # 既存のkeyword_filter(NFKCしない)で落ちるので期待に含めない
        # collect() は各フィードの先頭40件しか見ない（parsed.entries[:40]）
        real = [e for e in self.entries[:40] if e["source"]["title"] != "北陸地方整備局"
                and "ai" in f"{e['title']} {e['summary']}".lower()]
        self.assertTrue(real)
        kept = {it["title"] for it in self.result}
        for e in real:
            self.assertIn(e["title"][:120], kept, e["title"])

    def test_no_spam_words_in_titles(self) -> None:
        for it in self.result:
            for w in ("彼女", "彼氏", "ポルノ", "セックス"):
                self.assertNotIn(w, it["title"])


if __name__ == "__main__":
    unittest.main()
