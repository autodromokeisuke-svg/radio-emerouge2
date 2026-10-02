"""glossary_history.json 周りの単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.make_feed import (
    _load_glossary_by_date,
    _write_index,
    _episode_meta,
    build_episode_description,
    load_recent_glossary_terms,
    record_glossary_term,
    load_recent_news_titles,
    record_used_news,
    update_site,
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


class TestGlossaryHistoryAllPeriod(unittest.TestCase):
    """用語履歴は無期限に保持し、既定では日数の窓を使わず全期間を返すこと。

    「今日のひとこと」は公開開始(2026-09-01)以降の全期間で重複させない方針。
    30日の窓だと9/1→10/1（30日）でフィジカルAIが窓から落ちて重複した（2026-10-01実発生）。
    """

    def _write(self, site: Path, entries: list[dict[str, str]]) -> None:
        (site / "glossary_history.json").write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    def _old_and_new(self) -> tuple[str, str]:
        now = datetime.now(JST)
        return ((now - timedelta(days=200)).strftime("%Y%m%d"),
                (now - timedelta(days=3)).strftime("%Y%m%d"))

    def test_days_none_returns_all_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old, new = self._old_and_new()
            self._write(site, [{"date": old, "term": "古い用語"}, {"date": new, "term": "新しい用語"}])
            self.assertEqual(load_recent_glossary_terms(site, days=None),
                             [{"date": old, "term": "古い用語"}, {"date": new, "term": "新しい用語"}])
            # days省略（既定）も全期間
            self.assertEqual(len(load_recent_glossary_terms(site)), 2)

    def test_days_zero_or_negative_returns_all_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old, new = self._old_and_new()
            self._write(site, [{"date": old, "term": "古い用語"}, {"date": new, "term": "新しい用語"}])
            self.assertEqual(len(load_recent_glossary_terms(site, days=0)), 2)
            self.assertEqual(len(load_recent_glossary_terms(site, days=-5)), 2)

    def test_positive_days_still_uses_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old, new = self._old_and_new()
            self._write(site, [{"date": old, "term": "古い用語"}, {"date": new, "term": "新しい用語"}])
            self.assertEqual(load_recent_glossary_terms(site, days=30),
                             [{"date": new, "term": "新しい用語"}])
            self.assertEqual(len(load_recent_glossary_terms(site, days=365)), 2)

    def test_since_excludes_the_private_august_period_in_all_period_mode(self) -> None:
        """全期間モードでも、公開開始日(since)より前（8月の非公開期間）は返さない。"""
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write(site, [{"date": "20260801", "term": "非公開期間の用語"},
                               {"date": "20260831", "term": "公開前日の用語"},
                               {"date": "20260901", "term": "フィジカルAI"},
                               {"date": "20260930", "term": "公開後の用語"}])
            terms = load_recent_glossary_terms(site, days=None, since="20260901")
            self.assertEqual([t["term"] for t in terms], ["フィジカルAI", "公開後の用語"])

    def test_since_with_days_zero_equivalent_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write(site, [{"date": "20260801", "term": "A"}, {"date": "20260901", "term": "B"}])
            self.assertEqual(load_recent_glossary_terms(site, days=0, since="20260901"),
                             load_recent_glossary_terms(site, days=None, since="20260901"))

    def test_invalid_date_entries_are_skipped_when_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write(site, [{"date": "bad", "term": "X"}, {"date": "20260901", "term": "Y"}])
            self.assertEqual(load_recent_glossary_terms(site, days=None),
                             [{"date": "20260901", "term": "Y"}])

    def test_record_does_not_delete_entries_older_than_200_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old, new = self._old_and_new()
            self._write(site, [{"date": old, "term": "200日前の用語"}])
            record_glossary_term(site, new, "今日の用語")
            saved = json.loads((site / "glossary_history.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, [{"date": old, "term": "200日前の用語"},
                                     {"date": new, "term": "今日の用語"}])

    def test_record_keeps_publish_start_entry_across_long_gaps(self) -> None:
        """9/1分が90日後（11/30頃）に消えて重複判定が効かなくなる退行の防止。"""
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write(site, [{"date": "20260901", "term": "フィジカルAI"}])
            record_glossary_term(site, _recent_date_key(1), "別の用語")
            terms = load_recent_glossary_terms(site, days=None, since="20260901")
            self.assertIn("フィジカルAI", [t["term"] for t in terms])

    def test_record_drops_invalid_dates_and_sorts_ascending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write(site, [{"date": "20260905", "term": "C"}, {"date": "oops", "term": "X"},
                               {"date": "20260901", "term": "A"}])
            record_glossary_term(site, "20260903", "B")
            saved = json.loads((site / "glossary_history.json").read_text(encoding="utf-8"))
            self.assertEqual([e["term"] for e in saved], ["A", "B", "C"])

    def test_record_same_date_overwrites_even_for_old_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            old, _ = self._old_and_new()
            record_glossary_term(site, old, "最初")
            record_glossary_term(site, old, "上書き")
            saved = json.loads((site / "glossary_history.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, [{"date": old, "term": "上書き"}])


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


class TestHistoryFilesAreWrittenAtomically(unittest.TestCase):
    """履歴JSONは一時ファイル＋os.replaceで書く。書き込みの途中で落ちても、途中で切れた
    不完全なJSONが残らない（残ると履歴が全部読めなくなり、重複判定が効かなくなる）。"""

    ORIGINAL_GLOSSARY = [{"date": "20260901", "term": "フィジカルAI"}]
    ORIGINAL_NEWS_DATE = _recent_date_key(3)

    def _cases(self):
        """(ファイル名, 元の中身, 更新を実行する関数) の組。"""
        news_original = [{"date": self.ORIGINAL_NEWS_DATE, "title": "元のニュース", "link": ""}]
        return [
            ("glossary_history.json", self.ORIGINAL_GLOSSARY,
             lambda site: record_glossary_term(site, "20260903", "新しい用語")),
            ("news_history.json", news_original,
             lambda site: record_used_news(site, _recent_date_key(1), [{"title": "新しいニュース", "link": ""}])),
        ]

    def test_crash_in_the_middle_of_writing_keeps_the_original_file(self) -> None:
        original_write_text = Path.write_text

        def crash_midway(self_path, data, *args, **kwargs):
            original_write_text(self_path, data[: len(data) // 2], *args, **kwargs)  # 途中まで書いて
            raise OSError("disk full")                                               # 落ちる

        for name, original, update in self._cases():
            with self.subTest(file=name), tempfile.TemporaryDirectory() as tmp:
                site = Path(tmp)
                (site / name).write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
                with patch.object(Path, "write_text", crash_midway):
                    with self.assertRaises(OSError):
                        update(site)
                self.assertEqual(json.loads((site / name).read_text(encoding="utf-8")), original)
                self.assertEqual(sorted(p.name for p in site.iterdir()), [name])  # 一時ファイルも残さない

    def test_failure_of_the_final_replace_keeps_the_original_and_leaves_no_temp_file(self) -> None:
        for name, original, update in self._cases():
            with self.subTest(file=name), tempfile.TemporaryDirectory() as tmp:
                site = Path(tmp)
                (site / name).write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
                with patch("src.make_feed.os.replace", side_effect=OSError("locked")):
                    with self.assertRaises(OSError):
                        update(site)
                self.assertEqual(json.loads((site / name).read_text(encoding="utf-8")), original)
                self.assertEqual(sorted(p.name for p in site.iterdir()), [name])

    def test_temp_file_is_in_the_same_directory_and_replaces_the_target(self) -> None:
        for name, original, update in self._cases():
            with self.subTest(file=name), tempfile.TemporaryDirectory() as tmp:
                site = Path(tmp)
                (site / name).write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
                calls = []
                real_replace = os.replace   # patchはosモジュール自体の属性を差し替えるので、先に本物を控える

                def recording_replace(src, dst):
                    calls.append((Path(src), Path(dst)))
                    return real_replace(src, dst)

                with patch("src.make_feed.os.replace", side_effect=recording_replace):
                    update(site)
                self.assertEqual(len(calls), 1)
                src, dst = calls[0]
                self.assertEqual(dst, site / name)
                self.assertEqual(src.parent, dst.parent)   # 同じディレクトリ（別ドライブ等でのreplace失敗を避ける）
                self.assertNotEqual(src, dst)
                self.assertEqual(sorted(p.name for p in site.iterdir()), [name])   # 成功後は一時ファイルが残らない
                self.assertGreater(len(json.loads((site / name).read_text(encoding="utf-8"))), len(original))


class TestBrokenHistoryFileIsRewrittenWithWarning(unittest.TestCase):
    """既存の履歴JSONが壊れていて読めないときは、警告を出して新しいファイルとして書き直す
    （空リストから作り直す挙動は維持）。ファイルが無い（初回）ときは警告を出さない。"""

    def _record(self, name: str):
        if name == "glossary_history.json":
            return lambda site: record_glossary_term(site, "20260903", "新しい用語")
        return lambda site: record_used_news(site, _recent_date_key(1), [{"title": "新しいニュース", "link": ""}])

    def _run(self, name: str, content: bytes | None) -> tuple[list[str], list]:
        site_dir = tempfile.TemporaryDirectory()
        self.addCleanup(site_dir.cleanup)
        site = Path(site_dir.name)
        if content is not None:
            (site / name).write_bytes(content)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self._record(name)(site)
        warns = [ln for ln in buf.getvalue().splitlines() if ln.startswith("[warn]")]
        return warns, json.loads((site / name).read_text(encoding="utf-8"))

    def test_broken_files_are_rewritten_with_one_warning(self) -> None:
        broken = {"壊れたJSON": b"{not valid json", "途中で切れたJSON": b'[{"date": "20260901", "te',
                  "空ファイル": b"", "リストでないJSON": b'{"date": "20260901"}',
                  "nullのJSON": b"null", "不正な文字コード": b"\xff\xfe\x00[]"}
        for name in ["glossary_history.json", "news_history.json"]:
            for label, content in broken.items():
                with self.subTest(file=name, broken=label):
                    warns, saved = self._run(name, content)
                    self.assertEqual(warns, [f"[warn] {name} が壊れていたため、新しいファイルとして書き直します"])
                    self.assertEqual(len(saved), 1)   # 空リストから作り直し＝今回の1件だけ

    def test_missing_or_valid_files_do_not_warn(self) -> None:
        for name in ["glossary_history.json", "news_history.json"]:
            with self.subTest(file=name, state="missing"):
                warns, saved = self._run(name, None)
                self.assertEqual(warns, [])
                self.assertEqual(len(saved), 1)
            with self.subTest(file=name, state="valid"):
                warns, saved = self._run(name, b"[]")
                self.assertEqual(warns, [])
                self.assertEqual(len(saved), 1)


class TestHistoryEntriesWithNonStringDate(unittest.TestCase):
    """エントリの date が文字列でない（数値・None・リスト等）ときは、TypeErrorで落ちず、
    そのエントリを読み飛ばす。"""

    BAD_DATES = [20260901, None, ["20260901"], {"d": 1}, 1.5, True]

    def _dump(self, site: Path, name: str, valid: dict) -> None:
        entries = [dict(valid, date=bad) for bad in self.BAD_DATES] + [valid]
        (site / name).write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    def test_glossary_loader_skips_them(self) -> None:
        valid = {"date": _recent_date_key(2), "term": "有効な用語"}
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._dump(site, "glossary_history.json", valid)
            for kwargs in [{}, {"days": None}, {"days": 30}, {"since": "20260101"},
                           {"days": 30, "since": "20260101"}]:
                with self.subTest(kwargs=kwargs):
                    self.assertEqual(load_recent_glossary_terms(site, **kwargs), [valid])

    def test_news_loader_skips_them(self) -> None:
        valid = {"date": _recent_date_key(2), "title": "有効なニュース", "link": ""}
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._dump(site, "news_history.json", valid)
            for kwargs in [{}, {"days": 30}, {"since": "20260101"}]:
                with self.subTest(kwargs=kwargs):
                    self.assertEqual(load_recent_news_titles(site, **kwargs), [valid])

    def test_glossary_by_date_for_the_page_skips_them(self) -> None:
        valid = {"date": "20260901", "term": "有効な用語"}
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._dump(site, "glossary_history.json", valid)
            self.assertEqual(_load_glossary_by_date(site), {"20260901": "有効な用語"})

    def test_record_glossary_term_drops_them_and_keeps_the_rest(self) -> None:
        valid = {"date": "20260901", "term": "有効な用語"}
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._dump(site, "glossary_history.json", valid)
            record_glossary_term(site, "20260903", "今日の用語")
            saved = json.loads((site / "glossary_history.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, [valid, {"date": "20260903", "term": "今日の用語"}])

    def test_record_used_news_drops_them_and_keeps_the_rest(self) -> None:
        valid = {"date": _recent_date_key(5), "title": "有効なニュース", "link": ""}
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._dump(site, "news_history.json", valid)
            today = _recent_date_key(1)
            record_used_news(site, today, [{"title": "今日のニュース", "link": ""}])
            saved = json.loads((site / "news_history.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, [valid, {"date": today, "title": "今日のニュース", "link": ""}])

    def test_non_dict_entries_are_still_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "glossary_history.json").write_text(
                json.dumps(["文字列", None, 3, [], {"date": "20260901", "term": "有効"}], ensure_ascii=False),
                encoding="utf-8")
            self.assertEqual(load_recent_glossary_terms(site), [{"date": "20260901", "term": "有効"}])
            record_glossary_term(site, "20260902", "追加")
            saved = json.loads((site / "glossary_history.json").read_text(encoding="utf-8"))
            self.assertEqual([e["term"] for e in saved], ["有効", "追加"])


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


class TestPinnedEpisodes(unittest.TestCase):
    """pinned_episodesに載せた日付は episodes_keep のローテーション削除から
    除外され、永久保存されること（記念すべき初回放送を残すため 2026-09-09追加）。
    """

    SHOW = {"title": "テスト番組", "description": "desc", "author": "test",
            "owner_email": "a@b.com", "category": "Technology",
            "explicit": False, "credit": "credit"}

    def _make_episode(self, episodes: Path, date_key: str) -> None:
        (episodes / f"radio-{date_key}.mp3").write_bytes(b"dummy")
        meta = {"date": date_key, "pub": f"{date_key}T00:00:00+09:00",
                "title": f"放送{date_key}", "description": "desc",
                "file": f"radio-{date_key}.mp3", "bytes": 5}
        (episodes / f"radio-{date_key}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def test_pinned_episode_survives_beyond_keep_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            episodes = site / "episodes"
            episodes.mkdir()
            today = datetime.now(JST)
            pinned_date = (today - timedelta(days=100)).strftime("%Y%m%d")
            # pin対象1本 + keep範囲より古い2本 + 直近14本ぶんのダミー
            self._make_episode(episodes, pinned_date)
            old1 = (today - timedelta(days=20)).strftime("%Y%m%d")
            old2 = (today - timedelta(days=19)).strftime("%Y%m%d")
            self._make_episode(episodes, old1)
            self._make_episode(episodes, old2)
            for i in range(14):
                self._make_episode(episodes, (today - timedelta(days=13 - i)).strftime("%Y%m%d"))

            show_cfg = dict(self.SHOW, episodes_keep=14, pinned_episodes=[pinned_date])
            dummy_mp3 = site / "dummy.mp3"
            dummy_mp3.write_bytes(b"dummy")
            update_site(site, dummy_mp3, "最新放送", "desc",
                        "https://example.com", show_cfg)

            remaining = {p.stem.replace("radio-", "") for p in episodes.glob("radio-*.json")}
            self.assertIn(pinned_date, remaining,
                          "pinned_episodesに指定した回がepisodes_keepで削除された")
            self.assertNotIn(old1, remaining, "pin対象外の古い回が削除されていない")
            self.assertNotIn(old2, remaining, "pin対象外の古い回が削除されていない")


class TestBuildEpisodeDescription(unittest.TestCase):
    """番組説明文の組み立て（2026-09-27号の「… / Op」断片切れの再発防止）。"""

    FALLBACK = "エメとルジェが毎朝AIニュースを届けるラジオ番組の説明文です。" * 5

    def test_many_headlines_truncate_at_heading_boundary_with_hoka(self) -> None:
        # 9/27相当: 9件の見出しで合計400字を超える
        picked = [
            "OpenAIが新しい推論モデルを発表し性能とコストの両面で大幅な改善を報告したと業界各紙が一斉に報道",
            "Googleが検索結果にAI要約機能を全面展開しパブリッシャー側から著作権侵害を懸念する強い反発の声",
            "Anthropicが企業向けエージェント機能を大幅に拡張し大手金融機関数社との協業を新たに発表したと明かす",
            "Metaが新型ARグラスを公開し生成AIとの連携機能を披露するデモンストレーションを実施したと発表",
            "日本政府がAI人材育成のための新しい補助金制度の詳細を公表し来年度予算案に反映させる方針を示す",
            "米国でAI開発企業に対する新たな規制法案が議会に提出され業界団体が対応を検討していると報じられる",
            "半導体大手が次世代AIチップの量産計画を前倒しすると発表し株価が大きく上昇したと市場関係者が話す",
            "国内スタートアップがAI音声合成技術で大型資金調達に成功し海外展開を加速する方針を明らかにする",
            "欧州委員会がAI著作権ルールの見直しに着手すると表明し関係団体からの意見公募を開始したと発表",
        ]
        result = build_episode_description(picked, self.FALLBACK, limit=400)

        self.assertLessEqual(len(result), 400)
        self.assertTrue(result.startswith("今日の話題: "))
        self.assertTrue(result.endswith(" ほか"),
                        "見出しを1件以上省略したら「ほか」で終わるはず")
        # 見出しの途中で切れていない（「Op」のような断片が残らない）ことを確認
        body = result[len("今日の話題: "):-len(" ほか")]
        included_titles = body.split(" / ")
        for title in included_titles:
            self.assertIn(title, picked,
                          "見出しが途中で切れて元のリストに存在しない断片になっている")

    def test_short_list_matches_previous_behavior(self) -> None:
        picked = ["OpenAIが新モデルを発表", "Googleが検索機能を刷新"]
        result = build_episode_description(picked, self.FALLBACK, limit=400)
        expected = "今日の話題: " + " / ".join(picked)
        self.assertEqual(result, expected)
        self.assertNotIn("ほか", result)

    def test_empty_picked_uses_fallback_truncated(self) -> None:
        result = build_episode_description([], self.FALLBACK, limit=400)
        self.assertEqual(result, self.FALLBACK[:400])

    def test_first_headline_alone_exceeds_limit(self) -> None:
        long_title = "非常に長い見出し" * 60  # limitを大きく超える1件のみ
        picked = [long_title, "短い見出し"]
        result = build_episode_description(picked, self.FALLBACK, limit=400)
        self.assertEqual(len(result), 400)
        self.assertTrue(result.endswith("…"))
        self.assertTrue(result.startswith("今日の話題: " + long_title[:10]))

    def test_no_duplicate_headlines_kept(self) -> None:
        picked = ["同じ見出し", "同じ見出し", "別の見出し"]
        result = build_episode_description(picked, self.FALLBACK, limit=400)
        self.assertEqual(result, "今日の話題: 同じ見出し / 別の見出し")


def _freeze_make_feed_now(fixed: datetime):
    """src.make_feed 内の datetime.now(tz) を fixed（JSTのaware）に固定するパッチ。"""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    return patch("src.make_feed.datetime", _Frozen)


class TestHistoryWindowIncludesExactlyNDaysAgo(unittest.TestCase):
    """日数の窓は日付（暦日）単位で比較し、ちょうどN日前の当日分を含む（N+1日前は除外）。

    以前は 00:00 に正規化した日付と「現在時刻 - N日」を比較していたため、N日前の当日分が
    必ず落ち、実効N-1日になっていた（プロンプトの「直近14日」より1日短かった）。
    """

    TODAY = datetime(2026, 10, 5, tzinfo=JST)
    # 時刻によらず同じ結果になること（00:07 / 正午 / 23:59）
    CLOCKS = [(0, 7), (12, 0), (23, 59)]

    def _key(self, days_ago: int) -> str:
        return (self.TODAY - timedelta(days=days_ago)).strftime("%Y%m%d")

    def _at(self, hour: int, minute: int):
        return _freeze_make_feed_now(self.TODAY.replace(hour=hour, minute=minute))

    def test_news_includes_exactly_n_days_ago_and_excludes_n_plus_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "news_history.json").write_text(json.dumps(
                [{"date": self._key(n), "title": f"{n}日前", "link": ""} for n in range(0, 17)],
                ensure_ascii=False), encoding="utf-8")
            for hour, minute in self.CLOCKS:
                for days in (1, 7, 14):
                    with self.subTest(clock=f"{hour:02d}:{minute:02d}", days=days), \
                         self._at(hour, minute):
                        got = [e["title"] for e in load_recent_news_titles(site, days=days)]
                        self.assertEqual(got, [f"{n}日前" for n in range(0, days + 1)])
                        self.assertIn(f"{days}日前", got)
                        self.assertNotIn(f"{days + 1}日前", got)

    def test_glossary_includes_exactly_n_days_ago_and_excludes_n_plus_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "glossary_history.json").write_text(json.dumps(
                [{"date": self._key(n), "term": f"{n}日前"} for n in range(0, 17)],
                ensure_ascii=False), encoding="utf-8")
            for hour, minute in self.CLOCKS:
                for days in (1, 7, 14):
                    with self.subTest(clock=f"{hour:02d}:{minute:02d}", days=days), \
                         self._at(hour, minute):
                        got = [e["term"] for e in load_recent_glossary_terms(site, days=days)]
                        self.assertEqual(got, [f"{n}日前" for n in range(0, days + 1)])
                        self.assertIn(f"{days}日前", got)
                        self.assertNotIn(f"{days + 1}日前", got)

    def test_glossary_without_days_is_still_all_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "glossary_history.json").write_text(json.dumps(
                [{"date": self._key(n), "term": f"{n}日前"} for n in (0, 14, 15, 300)],
                ensure_ascii=False), encoding="utf-8")
            with self._at(12, 0):
                self.assertEqual(len(load_recent_glossary_terms(site, days=None)), 4)
                self.assertEqual(len(load_recent_glossary_terms(site, days=0)), 4)

    def test_record_used_news_still_prunes_at_30_days(self) -> None:
        """保存側のプルーニング（_NEWS_KEEP_DAYS=30）は従来どおり（今回の変更対象外）。"""
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "news_history.json").write_text(json.dumps(
                [{"date": self._key(n), "title": f"{n}日前", "link": ""} for n in (29, 30, 31)],
                ensure_ascii=False), encoding="utf-8")
            with self._at(12, 0):
                record_used_news(site, self._key(0), [{"title": "今日", "link": ""}])
            saved = json.loads((site / "news_history.json").read_text(encoding="utf-8"))
            titles = {e["title"] for e in saved}
            self.assertIn("今日", titles)
            self.assertIn("29日前", titles)
            self.assertNotIn("31日前", titles)   # 30日前の境界は変更対象外のため検証しない


class TestHistoryFilesWithBomAndBadEncoding(unittest.TestCase):
    """履歴JSONの読み込みの耐性。BOM付き（メモ帳等で保存し直された）でも正しく読め、
    cp932等で保存された不正なバイト列でも例外を投げない。書き込みは常にBOMなしUTF-8。"""

    BOM = b"\xef\xbb\xbf"

    def _write_bom(self, site: Path, name: str, entries: list) -> None:
        (site / name).write_bytes(self.BOM + json.dumps(entries, ensure_ascii=False).encode("utf-8"))

    def test_loaders_read_bom_prefixed_history(self) -> None:
        g = [{"date": "20260901", "term": "フィジカルAI"}]
        n = [{"date": _recent_date_key(2), "title": "BOM付きニュース", "link": ""}]
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write_bom(site, "glossary_history.json", g)
            self._write_bom(site, "news_history.json", n)
            self.assertEqual(load_recent_glossary_terms(site), g)
            self.assertEqual(load_recent_glossary_terms(site, days=None, since="20260901"), g)
            self.assertEqual(_load_glossary_by_date(site), {"20260901": "フィジカルAI"})
            self.assertEqual(load_recent_news_titles(site, days=7), n)

    def test_record_keeps_existing_entries_of_bom_prefixed_history_and_writes_without_bom(self) -> None:
        g = [{"date": "20260901", "term": "フィジカルAI"}]
        n_date = _recent_date_key(3)
        n = [{"date": n_date, "title": "BOM付きニュース", "link": ""}]
        today = _recent_date_key(1)
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            self._write_bom(site, "glossary_history.json", g)
            self._write_bom(site, "news_history.json", n)
            buf = io.StringIO()
            with redirect_stdout(buf):
                record_glossary_term(site, "20260903", "新しい用語")
                record_used_news(site, today, [{"title": "今日のニュース", "link": ""}])
            self.assertNotIn("[warn]", buf.getvalue())   # 壊れた扱い（書き直し）になっていない
            for name in ["glossary_history.json", "news_history.json"]:
                self.assertFalse((site / name).read_bytes().startswith(self.BOM), name)
            self.assertEqual(
                json.loads((site / "glossary_history.json").read_text(encoding="utf-8")),
                g + [{"date": "20260903", "term": "新しい用語"}])
            self.assertEqual(
                json.loads((site / "news_history.json").read_text(encoding="utf-8")),
                n + [{"date": today, "title": "今日のニュース", "link": ""}])

    def test_cp932_bytes_do_not_raise_in_loaders(self) -> None:
        cp932 = json.dumps([{"date": "20260901", "term": "フィジカルAI"}],
                           ensure_ascii=False).encode("cp932")
        with self.assertRaises(UnicodeDecodeError):   # 前提: これはUTF-8として読めない
            cp932.decode("utf-8-sig")
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "glossary_history.json").write_bytes(cp932)
            (site / "news_history.json").write_bytes(cp932)
            self.assertEqual(load_recent_glossary_terms(site), [])
            self.assertEqual(load_recent_glossary_terms(site, days=30), [])
            self.assertEqual(_load_glossary_by_date(site), {})
            self.assertEqual(load_recent_news_titles(site, days=7), [])

    def test_cp932_bytes_do_not_raise_in_record(self) -> None:
        cp932 = json.dumps([{"date": "20260901", "term": "フィジカルAI"}],
                           ensure_ascii=False).encode("cp932")
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "glossary_history.json").write_bytes(cp932)
            (site / "news_history.json").write_bytes(cp932)
            with redirect_stdout(io.StringIO()):
                record_glossary_term(site, "20260903", "新しい用語")
                record_used_news(site, _recent_date_key(1), [{"title": "今日", "link": ""}])
            self.assertEqual(
                json.loads((site / "glossary_history.json").read_text(encoding="utf-8")),
                [{"date": "20260903", "term": "新しい用語"}])
            self.assertEqual(len(json.loads((site / "news_history.json").read_text(encoding="utf-8"))), 1)
