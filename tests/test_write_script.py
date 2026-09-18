"""_format_recent_terms_block() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.write_script import (_check_glossary_term, _check_glossary_topic_safety,
                              _check_ordinal_references, _check_sections,
                              _drop_before_publish,
                              _format_recent_terms_block, _resolve_used_news,
                              _validate, write_script)


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
        return mock_client.messages.create.call_args.kwargs["messages"][0]["content"]

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
