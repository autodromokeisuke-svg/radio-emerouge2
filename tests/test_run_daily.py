"""run_daily.py の単体テスト。

- 試し放送向けの環境変数フック（_apply_script_model_override / _uploads_disabled）
- show.publish_from の検証（_validated_publish_from）
- main() が config の値（publish_from・各 *_reuse_avoid_days）を履歴ローダへ正しく渡していること
  （ROOT を一時ディレクトリへ差し替え、write_script に到達した時点で止める配線テスト）

標準ライブラリ unittest + unittest.mock のみ使用（config の読み込みに PyYAML）。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.run_daily as run_daily
from src.make_feed import JST
from src.run_daily import (_apply_script_model_override, _uploads_disabled,
                           _validated_publish_from)


class TestApplyScriptModelOverride(unittest.TestCase):
    def test_env_var_unset_keeps_config_model(self) -> None:
        cfg = {"script": {"model": "claude-sonnet-4-6"}}
        with patch.dict("os.environ", {}, clear=True):
            _apply_script_model_override(cfg)
        self.assertEqual(cfg["script"]["model"], "claude-sonnet-4-6")

    def test_env_var_set_overrides_config_model(self) -> None:
        cfg = {"script": {"model": "claude-sonnet-4-6"}}
        with patch.dict("os.environ", {"RADIO_SCRIPT_MODEL": "claude-sonnet-5"}, clear=True):
            _apply_script_model_override(cfg)
        self.assertEqual(cfg["script"]["model"], "claude-sonnet-5")

    def test_empty_env_var_does_not_override(self) -> None:
        cfg = {"script": {"model": "claude-sonnet-4-6"}}
        with patch.dict("os.environ", {"RADIO_SCRIPT_MODEL": ""}, clear=True):
            _apply_script_model_override(cfg)
        self.assertEqual(cfg["script"]["model"], "claude-sonnet-4-6")


class TestUploadsDisabled(unittest.TestCase):
    def test_default_uploads_not_disabled(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(_uploads_disabled())

    def test_flag_set_to_1_disables_uploads(self) -> None:
        with patch.dict("os.environ", {"RADIO_DISABLE_UPLOADS": "1"}, clear=True):
            self.assertTrue(_uploads_disabled())

    def test_other_value_does_not_disable_uploads(self) -> None:
        with patch.dict("os.environ", {"RADIO_DISABLE_UPLOADS": "true"}, clear=True):
            self.assertFalse(_uploads_disabled())


class TestValidatedPublishFrom(unittest.TestCase):
    def test_empty_or_unset_means_no_limit(self) -> None:
        for show in [{}, {"publish_from": ""}, {"publish_from": None}]:
            with self.subTest(show=show):
                self.assertEqual(_validated_publish_from(show), "")

    def test_valid_yyyymmdd_is_returned_as_is(self) -> None:
        self.assertEqual(_validated_publish_from({"publish_from": "20260901"}), "20260901")
        # YAMLで引用符なしに書かれた場合は整数になる。従来どおり文字列として受け付ける
        self.assertEqual(_validated_publish_from({"publish_from": 20260901}), "20260901")
        self.assertEqual(_validated_publish_from({"publish_from": "20240229"}), "20240229")  # うるう日

    def test_other_formats_are_rejected_with_a_helpful_message(self) -> None:
        for bad in ["2026-09-01", "2026/09/01", "26-09-01", "2026091", "202609011", "20261301",
                    "20260230", "20250229", "00000000", "abcdefgh", " 20260901", "20260901 ",
                    "２０２６０９０１", "2026年9月1日", date(2026, 9, 1), 2026091, True]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    _validated_publish_from({"publish_from": bad})
                msg = str(cm.exception)
                self.assertIn("show.publish_from は YYYYMMDD 形式で指定してください", msg)
                self.assertIn("20260901", msg)    # 例
                self.assertIn(repr(bad), msg)     # 現在値


class _Stop(Exception):
    """write_script に到達した時点で main() を止めるための専用例外。"""


def _run_main_until_write_script(show: dict, script: dict):
    """ROOT を一時ディレクトリに差し替え、collect・履歴ローダ・write_script をモックして main() を走らせる。

    write_script に到達したら _Stop を投げて止める（以降の収録・配信は実行しない）。
    戻り値: (main() が投げた例外, {"collect","news","terms","write"} の各モック)
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = {"show": show, "script": script, "news_feeds": []}
        (root / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        with patch.object(run_daily, "ROOT", root), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(run_daily, "collect", return_value=[]) as collect, \
             patch.object(run_daily, "load_recent_news_titles", return_value=[]) as news, \
             patch.object(run_daily, "load_recent_glossary_terms", return_value=[]) as terms, \
             patch.object(run_daily, "write_script", side_effect=_Stop) as write, \
             redirect_stdout(io.StringIO()):
            try:
                run_daily.main()
                raised = None
            except Exception as e:  # noqa: BLE001
                raised = e
    return raised, {"collect": collect, "news": news, "terms": terms, "write": write}


class TestMainWiring(unittest.TestCase):
    """main() が config の値を履歴ローダへ正しく配線していること（変異検出用）。"""

    SHOW = {"publish_from": "20260901", "minutes": 20}

    def _run(self, show: dict | None = None, **script):
        script = {"model": "m", **script}
        raised, mocks = _run_main_until_write_script(self.SHOW if show is None else show, script)
        self.assertIsInstance(raised, _Stop, f"write_script に到達していない: {raised!r}")
        mocks["write"].assert_called_once()
        return mocks

    def test_glossary_days_zero_unset_or_null_means_the_whole_period(self) -> None:
        for script in [{"glossary_reuse_avoid_days": 0}, {}, {"glossary_reuse_avoid_days": None}]:
            with self.subTest(script=script):
                terms = self._run(**script)["terms"]
                terms.assert_called_once()
                self.assertIn("days", terms.call_args.kwargs)
                self.assertIsNone(terms.call_args.kwargs["days"])

    def test_positive_glossary_days_is_passed_through(self) -> None:
        terms = self._run(glossary_reuse_avoid_days=45)["terms"]
        self.assertEqual(terms.call_args.kwargs["days"], 45)

    def test_publish_from_is_passed_as_since_to_both_history_loaders(self) -> None:
        mocks = self._run(glossary_reuse_avoid_days=0, news_reuse_avoid_days=14)
        self.assertEqual(mocks["terms"].call_args.kwargs["since"], "20260901")
        self.assertEqual(mocks["news"].call_args.kwargs["since"], "20260901")

    def test_unset_publish_from_means_since_is_empty(self) -> None:
        for show in [{"minutes": 20}, {"publish_from": "", "minutes": 20}]:
            with self.subTest(show=show):
                mocks = self._run(show)
                self.assertEqual(mocks["terms"].call_args.kwargs["since"], "")
                self.assertEqual(mocks["news"].call_args.kwargs["since"], "")

    def test_news_days_defaults_to_14_and_follows_config(self) -> None:
        self.assertEqual(self._run()["news"].call_args.kwargs["days"], 14)
        self.assertEqual(self._run(news_reuse_avoid_days=7)["news"].call_args.kwargs["days"], 7)

    def test_history_loaders_read_the_site_directory_under_root(self) -> None:
        mocks = self._run()
        for key in ["terms", "news"]:
            self.assertEqual(mocks[key].call_args.args[0].name, "site")

    def test_invalid_publish_from_stops_before_anything_runs(self) -> None:
        raised, mocks = _run_main_until_write_script(
            {"publish_from": "2026-09-01", "minutes": 20}, {"model": "m"})
        self.assertIsInstance(raised, ValueError)
        self.assertIn("YYYYMMDD", str(raised))
        self.assertIn("2026-09-01", str(raised))
        for key in ["collect", "news", "terms", "write"]:
            with self.subTest(mock=key):
                mocks[key].assert_not_called()


class TestMainRunsToTheEnd(unittest.TestCase):
    """main() を最後まで通す統合テスト（配線の変異検出用）。

    write_script・build・export_mp3・collect・履歴ローダ（load_recent_*）だけをモックし、
    update_site・record_used_news・write_latest_json・filter_recent は本物を使って、
    ROOT を差し替えた一時ディレクトリへ書く。write_script に履歴・設定を渡し忘れる／
    履歴へ用語・ニュースを記録し忘れる退行（後者は全期間の重複判定が永久に空振りする）を、
    日次ログにもテストにも検知手段が無いまま見逃さないための網。
    """

    FIXED_NOW = datetime(2026, 10, 5, 7, 30, tzinfo=JST)
    DATE_KEY = "20261005"

    SHOW = {
        "title": "テスト番組", "episode_title_prefix": "テスト番組", "author": "テスト作者",
        "description": "番組の説明文", "minutes": 20, "episodes_keep": 14,
        "pinned_episodes": [], "publish_from": "20260901", "category": "Technology",
        "explicit": False, "credit": "音声クレジット",
        "apple_podcasts_url": "https://podcasts.example.com/id1",
    }
    SCRIPT_CFG = {"model": "test-model", "max_news": 4, "news_reuse_avoid_days": 14,
                  "glossary_reuse_avoid_days": 0}

    OLD = {"title": "既出ニュース", "link": "https://example.com/old", "summary": "", "source": "s"}
    A = {"title": "ニュースA", "link": "https://example.com/a", "summary": "", "source": "s"}
    B = {"title": "ニュースB", "link": "https://example.com/b", "summary": "", "source": "s"}
    C = {"title": "ニュースC", "link": "https://example.com/c", "summary": "", "source": "s"}
    # 既出ニュースは filter_recent で落ち、番号は [A=1, B=2, C=3] になる。採用は C → A の順
    SCRIPT = {"title": "AIの付けたタイトル", "glossary_term": "ベクトルDB",
              "covered_news_indices": [3, 1],
              "lines": [{"speaker": "eme", "text": "おはよう"}, {"speaker": "ruje", "text": "おはよう"}]}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.site = self.root / "site"
        cfg = {"show": self.SHOW, "script": self.SCRIPT_CFG, "news_feeds": [],
               "tts": {"engine": "aivis"}, "bgm": {},
               # 外部アップロードは設定上は有効にしておき、RADIO_DISABLE_UPLOADS=1 で止まることまで見る
               "drive": {"upload_enabled": True, "folder_id": "dummy-folder"},
               "youtube": {"enabled": True}}
        (self.root / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True),
                                               encoding="utf-8")
        self.site.mkdir()
        (self.site / "glossary_history.json").write_text(
            json.dumps([{"date": "20260901", "term": "フィジカルAI"}], ensure_ascii=False),
            encoding="utf-8")

        # ローダが返す値は、そのまま write_script に渡ることを同一性（is）で見るための目印
        self.recent_terms = [{"date": "20260901", "term": "フィジカルAI"}]
        self.recent_news = [{"date": "20261003", "title": self.OLD["title"], "link": self.OLD["link"]}]

        fixed = self.FIXED_NOW

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        def fake_export_mp3(audio, out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"dummy-mp3")

        self.write = patch.object(run_daily, "write_script", return_value=dict(self.SCRIPT)).start()
        self.addCleanup(patch.stopall)
        self.build = patch.object(run_daily, "build", return_value="AUDIO").start()
        self.export = patch.object(run_daily, "export_mp3", side_effect=fake_export_mp3).start()
        self.collect = patch.object(run_daily, "collect",
                                    return_value=[self.OLD, self.A, self.B, self.C]).start()
        self.news_loader = patch.object(run_daily, "load_recent_news_titles",
                                        return_value=self.recent_news).start()
        self.terms_loader = patch.object(run_daily, "load_recent_glossary_terms",
                                         return_value=self.recent_terms).start()
        self.drive = patch.object(run_daily, "upload_to_drive").start()
        self.youtube = patch.object(run_daily, "upload_to_youtube").start()
        patch.object(run_daily, "ROOT", self.root).start()
        patch.dict("os.environ", {"RADIO_DISABLE_UPLOADS": "1"}, clear=True).start()
        patch("src.run_daily.datetime", _Frozen).start()
        patch("src.make_feed.datetime", _Frozen).start()

        with redirect_stdout(io.StringIO()):
            run_daily.main()

    def _load(self, name: str):
        return json.loads((self.site / name).read_text(encoding="utf-8"))

    def test_loader_results_and_show_cfg_are_passed_to_write_script(self) -> None:
        self.write.assert_called_once()
        kwargs = self.write.call_args.kwargs
        # (a) 用語履歴・(b) ニュース履歴は、ローダの戻り値がそのまま渡る
        self.assertIs(kwargs["recent_terms"], self.recent_terms)
        self.assertIs(kwargs["recent_news"], self.recent_news)
        # (c) show_cfg は cfg["show"] そのもの
        self.assertEqual(kwargs["show_cfg"], self.SHOW)
        self.assertEqual(kwargs["minutes"], self.SHOW["minutes"])

    def test_write_script_receives_the_news_without_recently_used_ones(self) -> None:
        news = self.write.call_args.args[0]
        self.assertEqual([n["title"] for n in news], ["ニュースA", "ニュースB", "ニュースC"])
        self.assertEqual(self.write.call_args.args[1]["model"], "test-model")

    def test_glossary_term_is_recorded_in_the_site_history(self) -> None:
        # (d) 履歴に今日の用語が記録されない＝全期間の重複判定が永久に空振りする
        self.assertEqual(self._load("glossary_history.json"),
                         [{"date": "20260901", "term": "フィジカルAI"},
                          {"date": self.DATE_KEY, "term": "ベクトルDB"}])

    def test_covered_news_are_recorded_in_the_news_history(self) -> None:
        # (e) 採用番号は絞り込み後のニュースへの番号。記録は採用した順（C → A）
        self.assertEqual(self._load("news_history.json"),
                         [{"date": self.DATE_KEY, "title": "ニュースC", "link": self.C["link"]},
                          {"date": self.DATE_KEY, "title": "ニュースA", "link": self.A["link"]}])

    def test_latest_json_and_episode_files_are_written_under_the_temp_root(self) -> None:
        latest = self._load("latest.json")
        self.assertEqual(latest["date"], self.DATE_KEY)
        self.assertEqual(latest["news"], ["ニュースC", "ニュースA"])
        self.assertEqual(latest["glossary_term"], "ベクトルDB")
        self.assertIn("10月5日", latest["title"])
        self.assertTrue(latest["episode_url"].endswith(f"/episodes/radio-{self.DATE_KEY}.mp3"))
        self.assertIsNone(latest["youtube_url"])
        self.assertEqual((self.site / "episodes" / f"radio-{self.DATE_KEY}.mp3").read_bytes(),
                         b"dummy-mp3")
        self.assertEqual(self._load(f"episodes/radio-{self.DATE_KEY}.json")["date"], self.DATE_KEY)
        self.assertTrue((self.site / "feed.xml").exists())
        self.assertTrue((self.site / "index.html").exists())

    def test_external_uploads_are_not_called_when_disabled(self) -> None:
        self.drive.assert_not_called()
        self.youtube.assert_not_called()


if __name__ == "__main__":
    unittest.main()
