"""_format_recent_terms_block() の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.write_script import (_check_glossary_term, _check_glossary_topic_safety,
                              _drop_before_publish,
                              _format_recent_terms_block, _validate, write_script)


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
        problems = _check_glossary_term(data, recent_terms=[], news=self._news())
        self.assertEqual(problems, [])

    def test_term_not_in_news_is_flagged(self) -> None:
        data = {"glossary_term": "フィジカルAI"}
        problems = _check_glossary_term(data, recent_terms=[], news=self._news())
        self.assertTrue(any("見当たりません" in p for p in problems))

    def test_recently_used_term_is_flagged(self) -> None:
        data = {"glossary_term": "OCR"}
        recent = [{"date": "20260701", "term": "OCR"}]
        problems = _check_glossary_term(data, recent_terms=recent, news=self._news())
        self.assertTrue(any("使用済み" in p for p in problems))

    def test_empty_term_has_no_problems(self) -> None:
        problems = _check_glossary_term({"glossary_term": ""}, recent_terms=[], news=self._news())
        self.assertEqual(problems, [])


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


def _base_lines() -> list[dict[str, str]]:
    return [{"speaker": "eme" if i % 2 == 0 else "ruje", "text": f"セリフ{i}" * 20}
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
