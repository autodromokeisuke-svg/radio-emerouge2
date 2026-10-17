"""_format_recent_terms_block() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import copy
import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.write_script import (_check_glossary_term, _check_glossary_topic_safety,
                              _bgm_intro_block, _check_ordinal_references, _check_sections,
                              _drop_before_publish,
                              _format_recent_terms_block, _holiday_block, _resolve_used_news,
                              _usage_summary, _validate, write_script)
from src.write_script import JST as _JST


class TestValidateCoveredNewsIndices(unittest.TestCase):
    def _base_lines(self) -> list[dict[str, str]]:
        return [{"speaker": "eme" if i % 2 == 0 else "ruje", "text": f"セリフ{i}"}
                for i in range(8)]

    def test_covered_news_indices_included_as_list(self) -> None:
        data = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "covered_news_indices": [1, 3],
            "lines": self._base_lines(),
        }
        result = _validate(data)
        self.assertEqual(result["covered_news_indices"], [1, 3])

    def test_missing_or_invalid_covered_news_indices_defaults_to_empty_list(self) -> None:
        data_missing = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "lines": self._base_lines(),
        }
        self.assertEqual(_validate(data_missing)["covered_news_indices"], [])

        data_wrong_type = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "covered_news_indices": "1",
            "lines": self._base_lines(),
        }
        self.assertEqual(_validate(data_wrong_type)["covered_news_indices"], [])

        data_mixed_types = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "covered_news_indices": [1, "2", None, True, 3],
            "lines": self._base_lines(),
        }
        self.assertEqual(_validate(data_mixed_types)["covered_news_indices"], [1, 3])


class TestValidateNonDictLines(unittest.TestCase):
    """linesの要素が辞書でない場合にAttributeErrorで落ちず、
    不正な要素だけスキップされること（Codexの指摘に基づく回帰テスト）。"""

    def test_non_dict_line_elements_are_skipped_not_crashed(self) -> None:
        valid_lines = [{"speaker": "eme" if i % 2 == 0 else "ruje", "text": f"セリフ{i}"}
                      for i in range(8)]
        data = {
            "title": "テスト放送",
            "glossary_term": "OCR",
            "lines": ["文字列が混ざっている", None, 123] + valid_lines,
        }
        result = _validate(data)
        self.assertEqual(len(result["lines"]), 8)


class TestCheckGlossaryTerm(unittest.TestCase):
    """今日のひとこと用語の重複・グラウンディング（実在ニュースに基づくか）チェック。
    フィジカルAIの再三の重複・8/2ニュースに無い話題を扱ったとの指摘への対応。"""

    def _news(self) -> list[dict[str, str]]:
        return [{"title": "OCRで手書きメモをデジタル化", "summary": "光学文字認識の新技術"}]

    def test_valid_term_has_no_problems(self) -> None:
        data = {"glossary_term": "OCR"}
        problems = _check_glossary_term(data, recent_terms=[], used_news=self._news())
        self.assertEqual(problems, [])

    def test_term_not_in_news_is_flagged(self) -> None:
        data = {"glossary_term": "フィジカルAI"}
        problems = _check_glossary_term(data, recent_terms=[], used_news=self._news())
        self.assertTrue(any("見当たりません" in p for p in problems))

    def test_recently_used_term_is_flagged(self) -> None:
        data = {"glossary_term": "OCR"}
        recent = [{"date": "20260701", "term": "OCR"}]
        problems = _check_glossary_term(data, recent_terms=recent, used_news=self._news())
        self.assertTrue(any("使用済み" in p for p in problems))

    def test_empty_term_has_no_problems(self) -> None:
        problems = _check_glossary_term({"glossary_term": ""}, recent_terms=[], used_news=self._news())
        self.assertEqual(problems, [])

    def test_term_only_in_unselected_candidate_is_flagged(self) -> None:
        """候補全体には載っているが、本編で選ばれなかったニュースの用語は
        グラウンディング不成立として扱うこと（2026-09-17発覚の再発防止）。"""
        data = {"glossary_term": "ソフトバンク"}
        used_news = [{"title": "OCRで手書きメモをデジタル化", "summary": "光学文字認識の新技術"}]
        problems = _check_glossary_term(data, recent_terms=[], used_news=used_news)
        self.assertTrue(any("見当たりません" in p for p in problems))

    def test_reuse_message_cites_date_and_past_term(self) -> None:
        recent = [{"date": "20260701", "term": "OCR"}]
        problems = _check_glossary_term({"glossary_term": "OCR"}, recent_terms=recent,
                                        used_news=self._news())
        self.assertEqual(problems, ["「OCR」は2026-07-01に使用済みの用語「OCR」と"
                                    "同じです（公開開始以降は再利用禁止）"])

    def test_notation_variants_of_a_used_term_are_flagged(self) -> None:
        """全角/半角・大小・ひらカナ・「エーアイ」の違いはすり抜けさせない。"""
        recent = [{"date": "20260901", "term": "フィジカルAI"}]
        for variant in ["フィジカルＡＩ", "フィジカルエーアイ", "ふぃじかるAI", "フィジカル ＡＩ",
                        "「フィジカルAI」"]:
            with self.subTest(variant=variant):
                news = [{"title": variant, "summary": ""}]
                problems = _check_glossary_term({"glossary_term": variant}, recent, news)
                self.assertEqual(len(problems), 1)
                self.assertIn("2026-09-01", problems[0])

    def test_alias_dictionary_catches_english_name_and_other_wording(self) -> None:
        recent = [{"date": "20260901", "term": "フィジカルAI"}]
        for variant in ["physical AI", "Physical AI", "ＰＨＹＳＩＣＡＬ　ＡＩ", "身体性AI"]:
            with self.subTest(variant=variant):
                news = [{"title": variant, "summary": ""}]
                problems = _check_glossary_term({"glossary_term": variant}, recent, news)
                self.assertEqual(len(problems), 1)
                self.assertIn("「フィジカルAI」", problems[0])

    def test_term_with_bracketed_alias_is_flagged_against_the_plain_past_term(self) -> None:
        """「フィジカルAI（身体性AI）」のような括弧つき表記の素通りを防ぐ。"""
        recent = [{"date": "20260901", "term": "フィジカルAI"}]
        for variant in ["フィジカルAI（身体性AI）", "フィジカルAI(Physical AI)"]:
            with self.subTest(variant=variant):
                news = [{"title": variant, "summary": ""}]
                problems = _check_glossary_term({"glossary_term": variant}, recent, news)
                self.assertEqual(len(problems), 1)
                self.assertIn("2026-09-01", problems[0])
                self.assertIn("「フィジカルAI」", problems[0])
        # 別の用語の括弧つき表記は重複にしない
        news = [{"title": "マルチエージェント（Multi-Agent）", "summary": ""}]
        self.assertEqual(_check_glossary_term({"glossary_term": "マルチエージェント（Multi-Agent）"},
                                              [{"date": "20260901", "term": "AIエージェント"}], news), [])

    def test_alias_dictionary_is_what_catches_english_name(self) -> None:
        """英語名の同一視は別名辞書の仕事。辞書が空なら通る（辞書が配線されている証拠）。"""
        recent = [{"date": "20260901", "term": "フィジカルAI"}]
        news = [{"title": "physical AI", "summary": ""}]
        with patch("src.write_script.load_alias_groups", return_value=[]):
            problems = _check_glossary_term({"glossary_term": "physical AI"}, recent, news)
        self.assertEqual(problems, [])

    def test_related_but_different_term_is_not_flagged(self) -> None:
        recent = [{"date": "20260901", "term": "マルチエージェント"}]
        news = [{"title": "AIエージェントが急増", "summary": ""}]
        problems = _check_glossary_term({"glossary_term": "AIエージェント"}, recent, news)
        self.assertEqual(problems, [])

    def test_similar_term_only_logs_info_and_is_not_a_problem(self) -> None:
        recent = [{"date": "20260901", "term": "AIエージェント"}]
        news = [{"title": "AIエージェント基盤が登場", "summary": ""}]
        buf = io.StringIO()
        with redirect_stdout(buf):
            problems = _check_glossary_term({"glossary_term": "AIエージェント基盤"}, recent, news)
        self.assertEqual(problems, [])
        self.assertIn("[info]", buf.getvalue())
        self.assertEqual(len(buf.getvalue().strip().splitlines()), 1)


class TestGlossaryDuplicateRegression(unittest.TestCase):
    """2026-10-01実発生の再発防止。10/1放送の「今日のひとこと」が「フィジカルAI」で、
    9/1放送分と重複した（30日窓から落ちたため）。実データと同じ形の履歴で、

    - 公開開始(9/1)以降の全期間で重複を検知する
    - 表記揺れ（physical AI）でも検知する
    - 非公開期間(8月)にしか使っていない用語は、重複扱いにも言及にも使わない（隔離）
    """

    HISTORY = [{"date": "20260801", "term": "フィジカルAI"},
               {"date": "20260823", "term": "ソブリンAI"},
               {"date": "20260901", "term": "フィジカルAI"},
               {"date": "20260902", "term": "AX戦略"},
               {"date": "20260915", "term": "ハルシネーション"}]

    def _visible(self, entries=None) -> list[dict[str, str]]:
        return _drop_before_publish(entries if entries is not None else self.HISTORY,
                                    "20260901", "テスト")

    @staticmethod
    def _news(*words: str) -> list[dict[str, str]]:
        return [{"title": " / ".join(words), "summary": "概要"}]

    def test_term_used_on_publish_start_is_flagged_a_month_later(self) -> None:
        problems = _check_glossary_term({"glossary_term": "フィジカルAI"}, self._visible(),
                                        self._news("フィジカルAI"))
        self.assertEqual(len(problems), 1)
        self.assertIn("2026-09-01", problems[0])
        self.assertNotIn("2026-08-01", problems[0])

    def test_english_notation_is_also_flagged(self) -> None:
        problems = _check_glossary_term({"glossary_term": "physical AI"}, self._visible(),
                                        self._news("physical AI"))
        self.assertEqual(len(problems), 1)
        self.assertIn("2026-09-01", problems[0])

    def test_term_used_only_in_private_august_is_not_flagged(self) -> None:
        for term in ["ソブリンAI", "sovereign AI", "主権AI"]:
            with self.subTest(term=term):
                problems = _check_glossary_term({"glossary_term": term}, self._visible(),
                                                self._news(term))
                self.assertEqual(problems, [])

    def test_august_only_history_does_not_flag_anything(self) -> None:
        visible = self._visible([{"date": "20260801", "term": "フィジカルAI"}])
        self.assertEqual(visible, [])
        problems = _check_glossary_term({"glossary_term": "フィジカルAI"}, visible,
                                        self._news("フィジカルAI"))
        self.assertEqual(problems, [])

    def test_unused_term_is_not_flagged(self) -> None:
        problems = _check_glossary_term({"glossary_term": "マルチモーダル"}, self._visible(),
                                        self._news("マルチモーダル"))
        self.assertEqual(problems, [])

    def test_write_script_retries_until_a_fresh_term_and_never_cites_august(self) -> None:
        news = [{"title": "physical AI と フィジカルAI と ソブリンAI の話題",
                 "summary": "概要", "source": "s", "link": ""}]
        bad = {"title": "テスト放送", "glossary_term": "physical AI",
               "covered_news_indices": [1], "lines": _base_lines()}
        # 「ソブリンAI」は8月（非公開期間）にしか使っていないので、公開後の放送では初出扱い
        good = {"title": "テスト放送", "glossary_term": "ソブリンAI",
                "covered_news_indices": [1], "lines": _base_lines()}
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [_fake_response(bad), _fake_response(good)]
        with patch("src.write_script.Anthropic", return_value=mock_client), \
             patch("src.write_script._check_glossary_topic_safety", return_value=[]):
            result = write_script(news, {"model": "test-model", "chars_per_minute": 320},
                                  minutes=1, recent_terms=list(self.HISTORY),
                                  show_cfg={"publish_from": "20260901"})
        self.assertEqual(result["glossary_term"], "ソブリンAI")
        self.assertEqual(mock_client.messages.create.call_count, 2)
        hint = mock_client.messages.create.call_args.kwargs["messages"][-1]["content"]
        self.assertIn("2026-09-01", hint)
        self.assertIn("使用済み", hint)
        self.assertNotIn("2026-08", hint)


class TestResolveUsedNews(unittest.TestCase):
    """covered_news_indicesから、本編で実際に扱ったニュースを引き当てる。

    run_daily.pyの同名ロジックと一致させる必要がある（ズレると「今日の
    ひとこと」が参照してよい範囲の判定が本編の実際の内容と食い違う）。
    """

    def _candidates(self, n: int) -> list[dict[str, str]]:
        return [{"title": f"候補{i}", "summary": f"概要{i}"} for i in range(1, n + 1)]

    def test_uses_covered_indices_when_present(self) -> None:
        news = self._candidates(24)  # 候補は最大24件想定
        data = {"covered_news_indices": [1, 3, 7]}
        used = _resolve_used_news(data, news, max_news=6)
        self.assertEqual([u["title"] for u in used], ["候補1", "候補3", "候補7"])

    def test_falls_back_to_max_news_when_indices_empty(self) -> None:
        news = self._candidates(24)
        used = _resolve_used_news({"covered_news_indices": []}, news, max_news=6)
        self.assertEqual(len(used), 6)
        self.assertEqual(used[0]["title"], "候補1")

    def test_out_of_range_indices_are_ignored(self) -> None:
        news = self._candidates(5)
        used = _resolve_used_news({"covered_news_indices": [1, 99, 3]}, news, max_news=6)
        self.assertEqual([u["title"] for u in used], ["候補1", "候補3"])


class TestCheckOrdinalReferences(unittest.TestCase):
    """「○本目」「○番目」のような順序参照が、本編で実際に扱った本数を
    超えていないか検証する（2026-09-17発覚: 未選択の7本目=ソフトバンクの
    ニュースを、あたかも本編で扱ったかのように「今日のひとこと」内で参照していた）。
    """

    def _lines(self, text: str) -> list[dict[str, str]]:
        return [{"speaker": "ruje", "text": text}]

    def test_out_of_range_arabic_ordinal_is_flagged(self) -> None:
        problems = _check_ordinal_references(
            self._lines("7本目のニュースで話したソフトバンクの件だけど"), covered_count=6)
        self.assertEqual(len(problems), 1)

    def test_out_of_range_fullwidth_ordinal_is_flagged(self) -> None:
        problems = _check_ordinal_references(
            self._lines("７番目で紹介した話だけど"), covered_count=6)
        self.assertEqual(len(problems), 1)

    def test_out_of_range_kanji_ordinal_is_flagged(self) -> None:
        problems = _check_ordinal_references(
            self._lines("七本目のニュースなんだけど"), covered_count=6)
        self.assertEqual(len(problems), 1)

    def test_in_range_ordinal_is_not_flagged(self) -> None:
        problems = _check_ordinal_references(
            self._lines("3本目のニュースで話したように"), covered_count=6)
        self.assertEqual(problems, [])

    def test_no_ordinal_reference_is_not_flagged(self) -> None:
        problems = _check_ordinal_references(
            self._lines("さっきのオープンAIの話だけどね"), covered_count=6)
        self.assertEqual(problems, [])

    def test_zero_is_flagged(self) -> None:
        problems = _check_ordinal_references(self._lines("0本目の話"), covered_count=6)
        self.assertEqual(len(problems), 1)


class TestSectionLabels(unittest.TestCase):
    """各セリフの section ラベル（BGMを流す区間の判定用）の引き継ぎと検証。"""

    def _lines(self, sections: list[str]) -> list[dict[str, str]]:
        return [{"speaker": "eme" if i % 2 == 0 else "ruje", "section": sec, "text": f"セリフ{i}"}
                for i, sec in enumerate(sections)]

    def test_validate_keeps_valid_section(self) -> None:
        secs = ["opening", "opening", "news", "news", "news", "glossary", "ending", "ending"]
        result = _validate({"title": "t", "glossary_term": "x", "lines": self._lines(secs)})
        self.assertEqual([ln["section"] for ln in result["lines"]], secs)

    def test_validate_blanks_unknown_or_missing_section(self) -> None:
        lines = self._lines(["opening"] * 8)
        lines[3]["section"] = "intro"      # 未知の値
        del lines[4]["section"]            # 欠落
        result = _validate({"title": "t", "glossary_term": "x", "lines": lines})
        self.assertEqual(result["lines"][3]["section"], "")
        self.assertEqual(result["lines"][4]["section"], "")
        self.assertEqual(result["lines"][0]["section"], "opening")

    def test_check_sections_accepts_valid_order(self) -> None:
        secs = ["opening", "news", "news", "glossary", "ending"]
        self.assertEqual(_check_sections(self._lines(secs)), [])

    def test_check_sections_reports_missing_labels(self) -> None:
        lines = self._lines(["opening", "news", "news", "glossary", "ending"])
        lines[2]["section"] = ""
        self.assertEqual(len(_check_sections(lines)), 1)

    def test_check_sections_reports_wrong_order(self) -> None:
        secs = ["opening", "news", "glossary", "news", "ending"]
        self.assertEqual(len(_check_sections(self._lines(secs))), 1)


class TestWriteScriptRetriesOnBadSections(unittest.TestCase):
    """sectionが不正な台本は、直せるうちはリトライさせ、最終試行では受容する
    （BGM無しになるだけで、放送自体は止めない）。"""

    def _payload(self, sections: list[str]) -> dict:
        lines = _base_lines()
        for ln, sec in zip(lines, sections):
            ln["section"] = sec
        return {"title": "テスト放送", "glossary_term": "OCR",
                "covered_news_indices": [1], "lines": lines}

    def _run(self, payloads: list[dict]):
        news = [{"title": "OCRで手書きメモをデジタル化", "summary": "光学文字認識の新技術",
                 "source": "s", "link": ""}]
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [_fake_response(p) for p in payloads]
        with patch("src.write_script.Anthropic", return_value=mock_client),              patch("src.write_script._check_glossary_topic_safety", return_value=[]):
            result = write_script(news, {"model": "test-model", "chars_per_minute": 320},
                                  minutes=1)
        return result, mock_client.messages.create.call_count

    def test_retries_when_sections_are_invalid(self) -> None:
        bad = self._payload([""] * 8)
        good = self._payload(["opening", "opening", "news", "news", "news", "glossary", "ending", "ending"])
        result, calls = self._run([bad, good])
        self.assertEqual(calls, 2)
        self.assertEqual(result["lines"][0]["section"], "opening")

    def test_accepts_invalid_sections_on_final_attempt(self) -> None:
        """3回とも不正でも例外にせず台本を返す（BGMが付かないだけで放送は成立する）。"""
        bad = self._payload([""] * 8)
        result, calls = self._run([bad, bad, bad])
        self.assertEqual(calls, 3)
        self.assertEqual(len(result["lines"]), 8)


class TestBgmIntroBlock(unittest.TestCase):
    """BGMが初めて入る回（show.bgm_intro_date）だけ、冒頭でBGM導入を紹介する指示が入る。"""

    def _today(self) -> str:
        from datetime import datetime
        from src.write_script import JST
        return datetime.now(JST).strftime("%Y%m%d")

    def test_block_appears_only_on_the_configured_date(self) -> None:
        self.assertIn("番組にBGMが入りました", _bgm_intro_block({"bgm_intro_date": self._today()}))
        self.assertEqual(_bgm_intro_block({"bgm_intro_date": "20000101"}), "")

    def test_unset_or_empty_is_a_normal_day(self) -> None:
        self.assertEqual(_bgm_intro_block({}), "")
        self.assertEqual(_bgm_intro_block({"bgm_intro_date": ""}), "")
        self.assertEqual(_bgm_intro_block(None), "")

    def test_block_does_not_reveal_who_made_the_bgm(self) -> None:
        """エメとルジェにBGMの作り手・AI利用を語らせない（正体を伏せる方針）。
        指示文が「触れるな」と明記していること。"""
        block = _bgm_intro_block({"bgm_intro_date": self._today()})
        self.assertIn("誰が・何で作ったか", block)
        self.assertIn("触れない", block)

    def test_block_reaches_the_prompt_only_on_that_day(self) -> None:
        news = [{"title": "OCR", "summary": "s", "source": "s", "link": ""}]
        payload = {"title": "t", "glossary_term": "OCR", "covered_news_indices": [1],
                   "lines": _base_lines()}

        def prompt_for(show_cfg: dict) -> str:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _fake_response(payload)
            with patch("src.write_script.Anthropic", return_value=mock_client),                  patch("src.write_script._check_glossary_topic_safety", return_value=[]):
                write_script(news, {"model": "m", "chars_per_minute": 320}, minutes=1,
                             show_cfg=show_cfg)
            return _prompt_text(mock_client.messages.create.call_args.kwargs)

        self.assertIn("番組にBGMが入りました", prompt_for({"bgm_intro_date": self._today()}))
        self.assertNotIn("番組にBGMが入りました", prompt_for({"bgm_intro_date": "20000101"}))
        self.assertNotIn("番組にBGMが入りました", prompt_for({}))


class TestCheckGlossaryTopicSafety(unittest.TestCase):
    """「今日のひとこと」テーマ選定へのX投稿ルール（全面回避テーマ）適用。

    2026-09-12発覚: 9/12放送分の「今日のひとこと」が「フーシ派」（政治・軍事的な
    武装組織）だった。用語自体がキーワードとして「軍事」等を含まないため、
    単純な禁止語リストでは検出できない。意味判断が要るためAI（Claude API）で
    分類する（reading_check.pyと同じ設計）。
    """

    def _fake_safety_response(self, blocked: bool, category: str = "", reason: str = "") -> MagicMock:
        return _fake_response({"blocked": blocked, "category": category, "reason": reason})

    def test_blocks_military_political_term(self) -> None:
        """軍事・政治組織の固有名詞は、キーワードに"軍事"等を含まなくてもブロックする。"""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._fake_safety_response(
            True, "政治、選挙、軍事、防衛", "武装組織であり軍事・政治が主題のため")
        with patch("src.write_script.Anthropic", return_value=mock_client):
            problems = _check_glossary_topic_safety(
                "フーシ派", [{"title": "AIを使った衝突分析にフーシ派が言及される"}], "test-model")
        self.assertEqual(len(problems), 1)
        self.assertIn("フーシ派", problems[0])

    def test_allows_benign_ai_term(self) -> None:
        """通常のAI技術用語はブロックしない。"""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._fake_safety_response(False)
        with patch("src.write_script.Anthropic", return_value=mock_client):
            problems = _check_glossary_topic_safety(
                "マルチモーダル", [{"title": "新しいマルチモーダルAIモデルが登場"}], "test-model")
        self.assertEqual(problems, [])

    def test_empty_term_is_not_checked(self) -> None:
        """空文字はAPIを呼ばずスキップする（無駄なAPI呼び出しをしない）。"""
        with patch("src.write_script.Anthropic") as mock_anthropic:
            problems = _check_glossary_topic_safety("", [], "test-model")
        mock_anthropic.assert_not_called()
        self.assertEqual(problems, [])

    def test_api_failure_is_fail_soft(self) -> None:
        """API呼び出しに失敗しても例外を投げず、ブロックせずに続行する
        （放送を止めないことを優先。最終防波堤は人間の日次確認）。"""
        with patch("src.write_script.Anthropic", side_effect=RuntimeError("network error")):
            problems = _check_glossary_topic_safety("何らかの用語", [{"title": "t"}], "test-model")
        self.assertEqual(problems, [])

    def test_malformed_json_is_fail_soft(self) -> None:
        """APIが不正なJSONを返しても例外を投げずブロックしない。"""
        block = MagicMock()
        block.type = "text"
        block.text = "これはJSONではありません"
        resp = MagicMock()
        resp.content = [block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = resp
        with patch("src.write_script.Anthropic", return_value=mock_client):
            problems = _check_glossary_topic_safety("何らかの用語", [{"title": "t"}], "test-model")
        self.assertEqual(problems, [])


def _fake_response(payload: dict) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps(payload, ensure_ascii=False)
    resp = MagicMock()
    resp.content = [block]
    return resp


def _blocks_text(content) -> str:
    """system / user の content（文字列、または text ブロックのリスト）を1つの文字列にする。"""
    if isinstance(content, str):
        return content
    return "\n".join(b["text"] for b in content)


def _system_text(call_kwargs: dict) -> str:
    return _blocks_text(call_kwargs["system"])


def _user_text(call_kwargs: dict) -> str:
    """1通目のuserメッセージ（可変部）のテキスト。"""
    return _blocks_text(call_kwargs["messages"][0]["content"])


def _prompt_text(call_kwargs: dict) -> str:
    """プロンプト全体（system＋1通目user）のテキスト。分割前の「1本のプロンプト」と同じ検査に使う。"""
    return _system_text(call_kwargs) + "\n" + _user_text(call_kwargs)


# BGMの区間判定用のsectionラベル（opening→news→glossary→ending の順）。
# write_scriptは各セリフのsectionを検証し、不正ならリトライさせるため、モックの台本にも必要
_SECTIONS_8 = ["opening", "opening", "news", "news", "news", "glossary", "ending", "ending"]


def _base_lines() -> list[dict[str, str]]:
    return [{"speaker": "eme" if i % 2 == 0 else "ruje", "section": _SECTIONS_8[i],
             "text": f"セリフ{i}" * 20}
            for i in range(8)]


class TestWriteScriptRetriesOnGlossaryReuse(unittest.TestCase):
    """今日のひとこと用語が直近使用済みだった場合、自動的に選び直しをリトライすること。"""

    def test_retries_when_glossary_term_is_reused(self) -> None:
        news = [{"title": "OCRで手書きメモをデジタル化", "summary": "光学文字認識の新技術", "source": "s", "link": ""}]
        recent_terms = [{"date": "20260701", "term": "OCR"}]

        bad_payload = {"title": "テスト放送", "glossary_term": "OCR",
                       "covered_news_indices": [1], "lines": _base_lines()}
        good_payload = {"title": "テスト放送", "glossary_term": "手書きメモ",
                        "covered_news_indices": [1], "lines": _base_lines()}

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _fake_response(bad_payload),
            _fake_response(good_payload),
        ]

        # このテストはglossary_term重複時のリトライだけを検証する。テーマ安全性
        # チェック（別クラスTestCheckGlossaryTopicSafetyで個別に検証）は
        # write_script内で別途Anthropicを呼ぶため、no-opにして呼び出し回数の
        # 数え間違いを防ぐ
        with patch("src.write_script.Anthropic", return_value=mock_client), \
             patch("src.write_script._check_glossary_topic_safety", return_value=[]):
            result = write_script(
                news, {"model": "test-model", "chars_per_minute": 320},
                minutes=1, recent_terms=recent_terms,
            )

        self.assertEqual(result["glossary_term"], "手書きメモ")
        self.assertEqual(mock_client.messages.create.call_count, 2)


class TestWriteScriptRetriesOnOutOfRangeOrdinal(unittest.TestCase):
    """「今日のひとこと」等が、本編で扱っていないニュースを順序参照した場合に
    自動的に選び直しをリトライすること（2026-09-17発覚の再発防止）。
    """

    def test_retries_when_ordinal_exceeds_covered_count(self) -> None:
        news = [{"title": f"候補{i}", "summary": f"概要{i}", "source": "s", "link": ""}
                for i in range(1, 8)]  # 候補7件（本編で扱うのは1件だけ）

        bad_lines = [{"speaker": "ruje",
                     "text": "7本目のニュースで話したソフトバンクの件だけどね" + "あ" * 20}] + \
                    _base_lines()[1:]
        bad_payload = {"title": "テスト放送", "glossary_term": "候補1",
                       "covered_news_indices": [1], "lines": bad_lines}
        good_payload = {"title": "テスト放送", "glossary_term": "候補1",
                        "covered_news_indices": [1], "lines": _base_lines()}

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _fake_response(bad_payload),
            _fake_response(good_payload),
        ]

        with patch("src.write_script.Anthropic", return_value=mock_client), \
             patch("src.write_script._check_glossary_topic_safety", return_value=[]):
            result = write_script(
                news, {"model": "test-model", "chars_per_minute": 320, "max_news": 1},
                minutes=1,
            )

        self.assertEqual(mock_client.messages.create.call_count, 2)
        self.assertNotIn("7本目", " ".join(ln["text"] for ln in result["lines"]))


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

    def test_line_format_is_date_colon_term(self) -> None:
        result = _format_recent_terms_block([{"date": "20260905", "term": "OCR"}])
        self.assertEqual(result, "- 2026-09-05: OCR")

    def test_aliases_are_appended_only_when_the_term_has_some(self) -> None:
        result = _format_recent_terms_block([
            {"date": "20260901", "term": "フィジカルAI"},
            {"date": "20260905", "term": "OCR"},
        ])
        self.assertEqual(result.splitlines(), [
            "- 2026-09-05: OCR",
            "- 2026-09-01: フィジカルAI（別表記: physical AI / 身体性AI）",
        ])

    def test_no_alias_note_when_dictionary_is_unavailable(self) -> None:
        with patch("src.write_script.load_alias_groups", return_value=[]):
            result = _format_recent_terms_block([{"date": "20260901", "term": "フィジカルAI"}])
        self.assertEqual(result, "- 2026-09-01: フィジカルAI")


if __name__ == "__main__":
    unittest.main()


class TestDropBeforePublish(unittest.TestCase):
    """参照可能な放送履歴を公開開始日(show.publish_from)以降に限定すること。

    非公開の試験運用期間(2026-08)の放送内容がAIへ渡ると、リスナーが存在を知らない
    放送に「先週も話しましたが」と言及してしまう（2026-09-03に実発生）。
    """

    def test_drops_entries_before_publish_from(self) -> None:
        entries = [{"date": "20260831", "term": "アンソロピック"},
                   {"date": "20260901", "term": "フィジカルAI"},
                   {"date": "20260902", "term": "AX戦略"}]
        kept = _drop_before_publish(entries, "20260901", "テスト")
        self.assertEqual([e["date"] for e in kept], ["20260901", "20260902"])

    def test_keeps_entry_exactly_on_publish_from(self) -> None:
        kept = _drop_before_publish([{"date": "20260901", "term": "X"}], "20260901", "テスト")
        self.assertEqual(len(kept), 1)

    def test_empty_publish_from_keeps_everything(self) -> None:
        """publish_fromが空文字（=全エピソード配信）のときは何も落とさない。"""
        entries = [{"date": "20260801", "term": "X"}, {"date": "20260901", "term": "Y"}]
        self.assertEqual(len(_drop_before_publish(entries, "", "テスト")), 2)

    def test_entry_without_date_is_dropped(self) -> None:
        """dateが欠けたエントリは安全側に倒して除外する。"""
        self.assertEqual(_drop_before_publish([{"term": "X"}], "20260901", "テスト"), [])


class TestWriteScriptHidesPrePublishHistory(unittest.TestCase):
    """write_scriptがAIへ渡すプロンプトに、公開開始日より前の履歴を含めないこと。"""

    def _run_and_capture_prompt(self, show_cfg: dict) -> str:
        news = [{"title": "オープンAI次期モデル、極めて高性能",
                 "summary": "追加安全対策が必要", "source": "s", "link": ""}]
        payload = {"title": "テスト放送", "glossary_term": "次期モデル",
                   "covered_news_indices": [1], "lines": _base_lines()}
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_response(payload)

        # call_args（最後の呼び出し）で台本生成プロンプトを取りたいので、テーマ
        # 安全性チェック（別途Anthropicを呼ぶ）はno-opにして呼び出し順を汚さない
        with patch("src.write_script.Anthropic", return_value=mock_client), \
             patch("src.write_script._check_glossary_topic_safety", return_value=[]):
            write_script(
                news, {"model": "test-model", "chars_per_minute": 320}, minutes=1,
                recent_terms=[{"date": "20260830", "term": "AIウォッシング"},
                              {"date": "20260902", "term": "AX戦略"}],
                recent_news=[{"date": "20260828", "title": "OpenAIの暴走AI、1200体が結託"},
                             {"date": "20260902", "title": "霞が関にAI課長？"}],
                show_cfg=show_cfg,
            )
        return _prompt_text(mock_client.messages.create.call_args.kwargs)

    def test_pre_publish_history_is_absent_from_prompt(self) -> None:
        prompt = self._run_and_capture_prompt({"publish_from": "20260901"})
        # 非公開期間（8月）の話題・用語がプロンプトに現れてはならない
        self.assertNotIn("OpenAIの暴走AI", prompt)
        self.assertNotIn("AIウォッシング", prompt)
        self.assertNotIn("2026-08-28", prompt)
        self.assertNotIn("2026-08-30", prompt)
        # 公開後（9月）の履歴は残っていること
        self.assertIn("霞が関にAI課長？", prompt)
        self.assertIn("AX戦略", prompt)

    def test_history_is_kept_when_publish_from_is_unset(self) -> None:
        prompt = self._run_and_capture_prompt({})
        self.assertIn("OpenAIの暴走AI", prompt)
        self.assertIn("AIウォッシング", prompt)


# ---------------------------------------------------------------------------
# プロンプト分割（system=固定部 / user=可変部）・プロンプトキャッシュ・usageログ・
# stop_reason（max_tokens / refusal）・大型連休ブロック
# ---------------------------------------------------------------------------

def _resp(payload, stop_reason="end_turn", usage=None, stop_details=None) -> SimpleNamespace:
    """Anthropic のレスポンスに似せた軽量オブジェクト（MagicMockと違い、属性が無ければ本当に無い）。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           stop_reason=stop_reason, usage=usage, stop_details=stop_details)


def _good_payload(term: str = "OCR") -> dict:
    return {"title": "テスト放送", "glossary_term": term,
            "covered_news_indices": [1], "lines": _base_lines()}


_DEFAULT_NEWS = [{"title": "OCRで手書きメモをデジタル化", "summary": "光学文字認識の新技術",
                  "source": "s", "link": ""}]


def _run_recorded(responses, news=None, holiday_block="", script_extra=None, **kwargs):
    """APIをモックして write_script を実行する。

    戻り値: (結果 または RuntimeError, 各 create 呼び出し時点の kwargs のスナップショット, 標準出力)
    リトライで messages が後から増えても、各呼び出しの時点の中身を比較できるよう deepcopy で記録する。
    大型連休ブロックは実行日に左右されないよう holiday_block で固定する。
    """
    calls: list[dict] = []
    queue = list(responses)

    def fake_create(**kw):
        calls.append(copy.deepcopy(kw))
        return queue.pop(0)

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = fake_create
    buf = io.StringIO()
    with patch("src.write_script.Anthropic", return_value=mock_client), \
         patch("src.write_script._check_glossary_topic_safety", return_value=[]), \
         patch("src.write_script._holiday_block", return_value=holiday_block), \
         redirect_stdout(buf):
        try:
            outcome = write_script(news if news is not None else _DEFAULT_NEWS,
                                   {"model": "test-model", "chars_per_minute": 320,
                                    **(script_extra or {})},
                                   minutes=1, **kwargs)
        except RuntimeError as e:
            outcome = e
    return outcome, calls, buf.getvalue()


def _count_cache_controls(call_kwargs: dict) -> int:
    blocks = [] if isinstance(call_kwargs["system"], str) else list(call_kwargs["system"])
    for m in call_kwargs["messages"]:
        if not isinstance(m["content"], str):
            blocks += list(m["content"])
    return sum(1 for b in blocks if "cache_control" in b)


class TestPromptSplitAndCache(unittest.TestCase):
    """固定部=system、可変部=1通目user。systemは日が変わっても完全一致（キャッシュ接頭辞の安定性）。"""

    def _run_day(self, label: str, tomorrow: str, tag: str, debut: str, bgm: str, holiday: str):
        news = [{"title": f"{tag}のニュース見出し OCR", "summary": f"{tag}の概要",
                 "source": "s", "link": ""}]
        with patch("src.write_script._today_label", return_value=label), \
             patch("src.write_script._tomorrow_label", return_value=tomorrow), \
             patch("src.write_script._debut_block", return_value=debut), \
             patch("src.write_script._bgm_intro_block", return_value=bgm):
            outcome, calls, _ = _run_recorded(
                [_resp(_good_payload("OCR"))], news=news, holiday_block=holiday,
                recent_terms=[{"date": "20260929", "term": f"{tag}の用語"}],
                recent_news=[{"date": "20260930", "title": f"{tag}の履歴ニュース"}],
                show_cfg={"publish_from": "20260901"})
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def _two_days(self):
        day1 = self._run_day("2026年10月2日 金曜日", "土曜日", "DAYONE", "## 初回放送の案内（本日限定）\nデビュー指示\n",
                             "", _holiday_block(datetime(2026, 9, 18, 7, tzinfo=_JST)))
        day2 = self._run_day("2026年10月3日 土曜日", "日曜日", "DAYTWO", "",
                             "## BGM導入の案内（本日限定）\nBGM指示\n", "")
        return day1, day2

    def test_system_is_identical_across_days(self) -> None:
        day1, day2 = self._two_days()
        self.assertEqual(day1["system"], day2["system"])
        # 逆に、可変部（user）は日ごとにちゃんと違う
        self.assertNotEqual(day1["messages"][0], day2["messages"][0])

    def test_system_has_fixed_prompt_and_no_leftover_placeholders(self) -> None:
        day1, _ = self._two_days()
        system = _system_text(day1)
        self.assertTrue(system.startswith("あなたは日本語ラジオ番組の放送作家です。"))
        self.assertIn("RADIOえめるーじぇ", system)
        self.assertIn("目標尺: 約1分（日本語で合計320文字以上", system)
        self.assertIsNone(re.search(r"\{[a-z_]+\}", system), "固定部に未置換のプレースホルダが残っている")
        self.assertIsNone(re.search(r"\{[a-z_]+\}", _user_text(day1)))

    def test_per_day_values_are_in_user_and_never_in_system(self) -> None:
        day1, _ = self._two_days()
        system, user = _system_text(day1), _user_text(day1)
        for needle in ["2026年10月2日 金曜日", "土曜日", "DAYONEのニュース見出し", "DAYONEの概要",
                       "DAYONEの履歴ニュース", "2026-09-30", "DAYONEの用語", "2026-09-29",
                       "デビュー指示", "大型連休の案内（本日限定）", "明日から5連休"]:
            with self.subTest(needle=needle):
                self.assertIn(needle, user)
                self.assertNotIn(needle, system)

    def test_cache_breakpoints_are_exactly_two(self) -> None:
        recent = [{"date": "20260905", "term": "OCR"}]
        news = [{"title": "OCRで手書きメモをデジタル化", "summary": "光学文字認識の新技術", "source": "s", "link": ""}]
        # 1回目は用語が使用済みで差し戻し → 2回目で成功（追記メッセージが付いた状態も検査する）
        outcome, calls, _ = _run_recorded(
            [_resp(_good_payload("OCR")), _resp(_good_payload("手書きメモ"))],
            news=news, recent_terms=recent)
        self.assertEqual(outcome["glossary_term"], "手書きメモ")
        self.assertEqual(len(calls), 2)
        for kw in calls:
            self.assertEqual(kw["system"][-1]["cache_control"], {"type": "ephemeral"})
            first_user = kw["messages"][0]
            self.assertEqual(first_user["role"], "user")
            self.assertEqual(first_user["content"][-1]["cache_control"], {"type": "ephemeral"})
            self.assertEqual(_count_cache_controls(kw), 2)
            self.assertEqual(kw["max_tokens"], 20000)

    def test_retry_keeps_first_message_and_appends_plain_strings(self) -> None:
        recent = [{"date": "20260905", "term": "OCR"}]
        outcome, calls, _ = _run_recorded(
            [_resp(_good_payload("OCR")), _resp(_good_payload("手書きメモ"))], recent_terms=recent)
        self.assertEqual(len(calls[0]["messages"]), 1)
        self.assertEqual(len(calls[1]["messages"]), 3)
        # 1通目と system は不変（リトライ時にキャッシュが読める）
        self.assertEqual(calls[1]["messages"][0], calls[0]["messages"][0])
        self.assertEqual(calls[1]["system"], calls[0]["system"])
        # 追記分は文字列content
        self.assertEqual(calls[1]["messages"][1]["role"], "assistant")
        self.assertIsInstance(calls[1]["messages"][1]["content"], str)
        self.assertEqual(calls[1]["messages"][2]["role"], "user")
        self.assertIsInstance(calls[1]["messages"][2]["content"], str)
        self.assertIn("使用済み", calls[1]["messages"][2]["content"])


class TestNewsReuseAvoidDaysDefault(unittest.TestCase):
    """news_reuse_avoid_days のコード内既定値は config.yaml と同じ14日。"""

    def test_default_is_14_days_in_both_system_and_user_prompts(self) -> None:
        _, calls, _ = _run_recorded([_resp(_good_payload())])
        self.assertIn("直近14日以内に扱った", _system_text(calls[0]))
        self.assertIn("直近14日で扱ったニュース", _system_text(calls[0]))
        self.assertIn("直近14日で扱ったニュース:", _user_text(calls[0]))
        self.assertNotIn("直近7日", _prompt_text(calls[0]))

    def test_explicit_config_value_wins_over_the_default(self) -> None:
        _, calls, _ = _run_recorded([_resp(_good_payload())],
                                    script_extra={"news_reuse_avoid_days": 7})
        self.assertIn("直近7日で扱ったニュース:", _user_text(calls[0]))
        self.assertIn("直近7日以内に扱った", _system_text(calls[0]))
        self.assertNotIn("直近14日", _prompt_text(calls[0]))


class TestUsageLog(unittest.TestCase):
    def test_summary_with_real_numbers(self) -> None:
        usage = SimpleNamespace(input_tokens=1200, cache_creation_input_tokens=4000,
                                cache_read_input_tokens=0, output_tokens=9000)
        self.assertEqual(_usage_summary(_resp(_good_payload(), usage=usage)),
                         "input=1200 cache_write=4000 cache_read=0 output=9000 stop=end_turn")

    def test_summary_never_raises_on_odd_responses(self) -> None:
        unknown = "input=? cache_write=? cache_read=? output=? stop=?"
        for label, resp in [
            ("MagicMock", MagicMock()),
            ("usageなし", SimpleNamespace(content=[])),
            ("usage=None", SimpleNamespace(usage=None, stop_reason=None)),
            ("None自体", None),
            ("各値None", SimpleNamespace(usage=SimpleNamespace(
                input_tokens=None, cache_creation_input_tokens=None,
                cache_read_input_tokens=None, output_tokens=None), stop_reason=None)),
            ("文字列の値", SimpleNamespace(usage=SimpleNamespace(input_tokens="x"), stop_reason=3)),
        ]:
            with self.subTest(label=label):
                self.assertEqual(_usage_summary(resp), unknown)

    def test_one_usage_line_per_call_with_values(self) -> None:
        usage = SimpleNamespace(input_tokens=11, cache_creation_input_tokens=22,
                                cache_read_input_tokens=33, output_tokens=44)
        outcome, calls, out = _run_recorded([_resp(_good_payload(), usage=usage)])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(out.count("[usage]"), 1)
        self.assertIn("[usage] input=11 cache_write=22 cache_read=33 output=44 stop=end_turn", out)

    def test_missing_usage_attribute_does_not_crash(self) -> None:
        resp = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(
            _good_payload(), ensure_ascii=False))])  # usage も stop_reason も無い
        outcome, _, out = _run_recorded([resp])
        self.assertIsInstance(outcome, dict)
        self.assertIn("[usage] input=? cache_write=? cache_read=? output=? stop=?", out)

    def test_magicmock_response_does_not_crash(self) -> None:
        """既存テストと同じMagicMock応答（usage・stop_reasonがMagicMockになる）でも落ちない。"""
        outcome, _, out = _run_recorded([_fake_response(_good_payload())])
        self.assertIsInstance(outcome, dict)
        self.assertIn("[usage] input=? cache_write=? cache_read=? output=? stop=?", out)

    def test_usage_is_logged_for_every_attempt(self) -> None:
        recent = [{"date": "20260905", "term": "OCR"}]
        outcome, calls, out = _run_recorded(
            [_resp(_good_payload("OCR")), _resp(_good_payload("手書きメモ"))], recent_terms=recent)
        self.assertEqual(len(calls), 2)
        self.assertEqual(out.count("[usage]"), 2)

    def test_no_secret_like_value_is_logged(self) -> None:
        _, _, out = _run_recorded([_resp(_good_payload())])
        self.assertNotIn("sk-ant", out)


class TestStopReasonHandling(unittest.TestCase):
    TRUNCATED = '{"title": "テスト放送", "glossary_term": "OCR", "lines": [{"speaker": "eme", "section": "opening", "te'

    def test_max_tokens_is_retried_with_a_length_hint(self) -> None:
        usage = SimpleNamespace(input_tokens=5000, cache_creation_input_tokens=0,
                                cache_read_input_tokens=4800, output_tokens=16000)
        outcome, calls, out = _run_recorded([
            _resp(self.TRUNCATED, stop_reason="max_tokens", usage=usage),
            _resp(_good_payload()),
        ])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 2)
        hint = calls[1]["messages"][-1]["content"]
        self.assertIn("出力が長すぎて途中で切れました", hint)
        self.assertIn("JSONが閉じる長さに収めて出力し直してください", hint)
        self.assertEqual(calls[1]["messages"][1], {"role": "assistant", "content": self.TRUNCATED})
        # [warn] に usage も出る
        warn_lines = [ln for ln in out.splitlines()
                      if ln.startswith("[warn]") and "途中で切れ" in ln and "stop=max_tokens" in ln]
        self.assertEqual(len(warn_lines), 1)
        self.assertIn("input=5000", warn_lines[0])
        self.assertIn("cache_read=4800", warn_lines[0])
        self.assertIn("output=16000", warn_lines[0])

    def test_max_tokens_is_a_failure_even_if_the_text_happens_to_be_valid_json(self) -> None:
        outcome, calls, _ = _run_recorded([
            _resp(_good_payload(), stop_reason="max_tokens"),
            _resp(_good_payload()),
        ])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 2)

    def test_max_tokens_on_every_attempt_raises(self) -> None:
        outcome, calls, _ = _run_recorded(
            [_resp(self.TRUNCATED, stop_reason="max_tokens")] * 3)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(len(calls), 3)
        self.assertIn("台本生成に失敗", str(outcome))
        self.assertIn("出力が長すぎて途中で切れました", str(outcome))

    def test_refusal_is_retried_without_growing_messages(self) -> None:
        refusal = _resp("", stop_reason="refusal", stop_details=SimpleNamespace(
            type="refusal", category="cyber", explanation=None))
        outcome, calls, out = _run_recorded([refusal, _resp(_good_payload())])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0]["messages"]), 1)
        self.assertEqual(len(calls[1]["messages"]), 1)
        self.assertEqual(calls[1]["messages"], calls[0]["messages"])
        self.assertEqual(calls[1]["system"], calls[0]["system"])
        warn_lines = [ln for ln in out.splitlines() if ln.startswith("[warn]") and "拒否" in ln]
        self.assertEqual(len(warn_lines), 1)
        self.assertIn("cyber", warn_lines[0])

    def test_refusal_on_every_attempt_raises_with_category(self) -> None:
        refusal = _resp("", stop_reason="refusal", stop_details=SimpleNamespace(category="bio"))
        outcome, calls, _ = _run_recorded([refusal] * 3)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(str(outcome), "台本生成が安全装置により拒否されました: bio")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(len(kw["messages"]) == 1 for kw in calls))

    def test_refusal_category_unknown_when_stop_details_missing(self) -> None:
        outcome, _, _ = _run_recorded([_resp("", stop_reason="refusal")] * 3)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(str(outcome), "台本生成が安全装置により拒否されました: 不明")

    def test_refusal_category_may_be_a_dict(self) -> None:
        refusal = _resp("", stop_reason="refusal", stop_details={"category": "frontier_llm"})
        outcome, _, _ = _run_recorded([refusal] * 3)
        self.assertEqual(str(outcome), "台本生成が安全装置により拒否されました: frontier_llm")

    def test_mixed_refusal_and_other_failure_reports_the_generic_failure(self) -> None:
        """全試行が拒否ではない場合は、拒否専用のメッセージにしない。"""
        refusal = _resp("", stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
        outcome, calls, _ = _run_recorded([
            refusal, refusal, _resp(self.TRUNCATED, stop_reason="max_tokens")])
        self.assertIsInstance(outcome, RuntimeError)
        self.assertTrue(str(outcome).startswith("台本生成に失敗"))
        self.assertEqual(len(calls), 3)

    def test_normal_stop_reasons_are_not_failures(self) -> None:
        for stop in ["end_turn", "stop_sequence", None]:
            with self.subTest(stop=stop):
                outcome, calls, _ = _run_recorded([_resp(_good_payload(), stop_reason=stop)])
                self.assertIsInstance(outcome, dict)
                self.assertEqual(len(calls), 1)


def _thinking_only_resp(stop_reason: str = "max_tokens") -> SimpleNamespace:
    """textブロックが無く、thinkingブロックだけの応答（思考だけで出力上限に達したケース）。"""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="考え中" * 100, signature="sig")],
        stop_reason=stop_reason, usage=None, stop_details=None)


class TestRetryDoesNotSendEmptyAssistantMessage(unittest.TestCase):
    """出力が空・空白のとき、空のassistantメッセージを付けて再送しない（APIは空contentを拒否する）。"""

    TRUNCATED = '{"title": "テスト放送", "glossary_term": "OCR", "lines": [{"speaker": "eme", "te'

    def assertNoEmptyContent(self, call_kwargs: dict) -> None:
        for m in call_kwargs["messages"]:
            content = m["content"]
            joined = content if isinstance(content, str) else "".join(b.get("text", "") for b in content)
            self.assertTrue(joined.strip(), f"空のcontentのメッセージがある: {m}")

    def test_thinking_only_response_with_max_tokens_adds_no_assistant_message(self) -> None:
        outcome, calls, _ = _run_recorded([_thinking_only_resp("max_tokens"), _resp(_good_payload())])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 2)
        second = calls[1]["messages"]
        self.assertNoEmptyContent(calls[1])
        self.assertNotIn("assistant", [m["role"] for m in second])
        # 1通目（キャッシュ対象）は不変で、userのhintだけが追記される
        self.assertEqual(second[0], calls[0]["messages"][0])
        self.assertEqual([m["role"] for m in second], ["user", "user"])
        self.assertIsInstance(second[1]["content"], str)
        self.assertIn("出力が長すぎて途中で切れました", second[1]["content"])

    def test_whitespace_only_text_adds_no_assistant_message(self) -> None:
        outcome, calls, _ = _run_recorded([_resp(" \n\t  \n", stop_reason="end_turn"),
                                           _resp(_good_payload())])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 2)
        self.assertNoEmptyContent(calls[1])
        self.assertEqual([m["role"] for m in calls[1]["messages"]], ["user", "user"])
        self.assertTrue(calls[1]["messages"][1]["content"].strip())

    def test_empty_on_every_attempt_never_sends_an_empty_message(self) -> None:
        outcome, calls, _ = _run_recorded([_thinking_only_resp()] * 3)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(len(calls), 3)
        for kw in calls:
            self.assertNoEmptyContent(kw)
            self.assertNotIn("assistant", [m["role"] for m in kw["messages"]])
        self.assertEqual([len(kw["messages"]) for kw in calls], [1, 2, 3])

    def test_truncated_but_non_empty_text_is_still_stacked_as_assistant(self) -> None:
        for stop in ["max_tokens", "end_turn"]:
            with self.subTest(stop=stop):
                outcome, calls, _ = _run_recorded([_resp(self.TRUNCATED, stop_reason=stop),
                                                   _resp(_good_payload())])
                self.assertIsInstance(outcome, dict)
                second = calls[1]["messages"]
                self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
                self.assertEqual(second[1]["content"], self.TRUNCATED)
                self.assertNoEmptyContent(calls[1])

    def test_empty_then_truncated_then_good_keeps_alternation_where_it_can(self) -> None:
        """空→途中切れ→成功。空の回はuserだけ、途中切れの回はassistant+userが積まれる。"""
        outcome, calls, _ = _run_recorded([_thinking_only_resp(), _resp(self.TRUNCATED),
                                           _resp(_good_payload())])
        self.assertIsInstance(outcome, dict)
        self.assertEqual([m["role"] for m in calls[2]["messages"]],
                         ["user", "user", "assistant", "user"])
        self.assertNoEmptyContent(calls[2])


class TestJsonFailureDebugOutput(unittest.TestCase):
    """JSON解析失敗時の[debug]出力は、解析した文字列(e.doc)上の位置から切る。"""

    PREAMBLE = "以下が台本です。ご確認ください。\n" * 12   # 位置ずれを起こすための長い前置き
    BAD_BODY = ('{"title": "T", "lines": [{"speaker": "eme", "text": "MARKERBEFORE"} '
                '{"speaker": "ruje", "text": "MARKERAFTER"}]}')   # 1つ目の要素の後に「,」が無い
    TRUNCATED_BODY = '{"title": "T", "lines": [{"speaker": "eme", "text": "a"}, {"speaker": "ruje", "te'

    def _debug_lines(self, text: str) -> list[str]:
        outcome, calls, out = _run_recorded([_resp(text), _resp(_good_payload())])
        self.assertIsInstance(outcome, dict)
        self.assertEqual(len(calls), 2)
        return out.splitlines()

    def test_snippet_is_cut_from_the_extracted_json_not_from_the_whole_response(self) -> None:
        text = self.PREAMBLE + "```json\n" + self.BAD_BODY + "\n```"
        with self.assertRaises(json.JSONDecodeError) as cm:
            json.loads(self.BAD_BODY)
        e = cm.exception
        expected = e.doc[max(0, e.pos - 80):e.pos + 80]
        lines = self._debug_lines(text)
        snippet_lines = [ln for ln in lines if ln.startswith("[debug] 失敗箇所付近")]
        self.assertEqual(snippet_lines, [f"[debug] 失敗箇所付近: ...{expected}..."])
        self.assertIn("MARKERBEFORE", snippet_lines[0])
        self.assertIn("MARKERAFTER", snippet_lines[0])
        self.assertNotIn("以下が台本です", snippet_lines[0])   # 位置ずれていれば前置きが出る

    def test_no_truncation_hint_when_the_error_is_in_the_middle(self) -> None:
        text = self.PREAMBLE + "```json\n" + self.BAD_BODY + "\n```"
        self.assertEqual([ln for ln in self._debug_lines(text) if ln.startswith("[hint]")], [])

    def test_truncation_hint_when_the_parse_position_is_the_end_of_the_output(self) -> None:
        text = self.PREAMBLE + "```json\n" + self.TRUNCATED_BODY
        lines = self._debug_lines(text)
        hints = [ln for ln in lines if ln.startswith("[hint]")]
        self.assertEqual(hints, ["[hint] 出力が途中で切れている可能性があります（解析位置が出力の末尾）"])
        snippet = [ln for ln in lines if ln.startswith("[debug] 失敗箇所付近")][0]
        self.assertIn('"text": "a"}', snippet)
        self.assertNotIn("以下が台本です", snippet)

    def test_no_debug_snippet_or_hint_for_non_json_decode_errors(self) -> None:
        """JSONが見つからない（ValueError）場合は snippet も hint も出さない。"""
        lines = self._debug_lines("JSONではない普通の文章です")
        self.assertEqual([ln for ln in lines if ln.startswith(("[hint]", "[debug] 失敗箇所付近"))], [])


class TestHolidayBlock(unittest.TestCase):
    JST = _JST

    def test_block_is_rendered_the_day_before_a_long_holiday(self) -> None:
        block = _holiday_block(datetime(2026, 9, 18, 7, 0, tzinfo=self.JST))
        self.assertTrue(block.startswith("## 大型連休の案内（本日限定）\n"))
        self.assertIn("明日から5連休（9月19日（土）〜9月23日（水））に入る。", block)
        self.assertIn("このパートのセリフも section は opening とすること。", block)
        self.assertNotIn("{", block)
        self.assertNotIn("}", block)
        self.assertTrue(block.endswith("\n"))

    def test_block_says_today_on_the_first_day(self) -> None:
        block = _holiday_block(datetime(2026, 5, 2, 7, 0, tzinfo=self.JST))
        self.assertIn("今日から5連休（5月2日（土）〜5月6日（水））に入る。", block)

    def test_date_label_format(self) -> None:
        block = _holiday_block(datetime(2026, 12, 28, 7, 0, tzinfo=self.JST))
        self.assertIn("明日から6連休（12月29日（火）〜1月3日（日））に入る。", block)

    def test_no_block_on_ordinary_days_and_inside_the_holiday(self) -> None:
        for d in [datetime(2026, 10, 3, 7, tzinfo=self.JST),     # 通常の土曜
                  datetime(2026, 10, 9, 7, tzinfo=self.JST),     # 10/10〜12は3連休
                  datetime(2026, 9, 20, 7, tzinfo=self.JST),     # 連休2日目
                  datetime(2026, 9, 23, 7, tzinfo=self.JST),     # 連休最終日
                  datetime(2026, 9, 24, 7, tzinfo=self.JST)]:    # 連休明け
            with self.subTest(d=d):
                self.assertEqual(_holiday_block(d), "")

    def test_jst_is_used_even_for_utc_input(self) -> None:
        # UTC 2026-09-17 22:00 は JST 2026-09-18 07:00（連休の前日）
        self.assertIn("明日から5連休", _holiday_block(datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)))
        # UTC 2026-09-18 22:00 は JST 2026-09-19 07:00（連休の初日）
        self.assertIn("今日から5連休", _holiday_block(datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)))

    def test_naive_datetime_is_treated_as_jst(self) -> None:
        self.assertIn("明日から5連休", _holiday_block(datetime(2026, 9, 18, 7, 0)))

    def test_default_uses_the_current_jst_date(self) -> None:
        fixed = {"when": "今日から", "n": 4, "start": date(2026, 10, 10), "end": date(2026, 10, 13)}
        with patch("src.write_script.long_holiday_info", return_value=fixed) as m:
            block = _holiday_block()
        self.assertEqual(m.call_count, 1)
        self.assertIn("今日から4連休（10月10日（土）〜10月13日（火））に入る。", block)

    def test_calendar_failure_means_no_block_and_no_crash(self) -> None:
        with patch("src.holiday_jp.jpholiday", None), redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(_holiday_block(datetime(2026, 9, 18, 7, tzinfo=self.JST)), "")
        self.assertEqual(len([ln for ln in buf.getvalue().splitlines() if ln.startswith("[warn]")]), 1)

    def test_block_reaches_user_prompt_only_and_the_fixed_prompt_stays_stable(self) -> None:
        block = _holiday_block(datetime(2026, 9, 18, 7, tzinfo=self.JST))
        with_block = _run_recorded([_resp(_good_payload())], holiday_block=block)[1][0]
        without = _run_recorded([_resp(_good_payload())], holiday_block="")[1][0]
        self.assertIn("大型連休の案内（本日限定）", _user_text(with_block))
        self.assertNotIn("大型連休の案内（本日限定）", _user_text(without))
        self.assertNotIn("大型連休の案内", _system_text(with_block))
        self.assertEqual(with_block["system"], without["system"])

    def test_write_script_survives_a_broken_calendar(self) -> None:
        """jpholiday が壊れていても台本生成は止まらず、案内ブロックだけが出ない。"""
        broken = MagicMock()
        broken.is_holiday.side_effect = RuntimeError("boom")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _resp(_good_payload())
        with patch("src.write_script.Anthropic", return_value=mock_client), \
             patch("src.write_script._check_glossary_topic_safety", return_value=[]), \
             patch("src.holiday_jp.jpholiday", broken), \
             redirect_stdout(io.StringIO()):
            result = write_script(_DEFAULT_NEWS, {"model": "m", "chars_per_minute": 320}, minutes=1)
        self.assertEqual(result["glossary_term"], "OCR")
        self.assertNotIn("大型連休の案内", _user_text(mock_client.messages.create.call_args.kwargs))
