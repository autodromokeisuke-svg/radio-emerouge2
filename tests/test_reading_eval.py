"""tools/reading_eval.py の run_dict_checks() の単体テスト（標準ライブラリ unittest のみ使用）。

9/26本番でAivisSpeechのaudio_queryが500を返し、CIのreading-eval（dict_only=true）が
1件の失敗で全体を落とす作りだと切り分けができない問題を再現する。engine.query()が
例外を投げても他の項目の検証を続け、passed=False・エラーには例外の型とHTTPステータス
だけを記録すること（台本本文やURLを含めないこと）を確認する。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import reading_eval  # noqa: E402


class TestRunDictChecks(unittest.TestCase):
    def test_one_query_failure_does_not_abort_other_checks(self) -> None:
        checks = [
            {"text": "壊れる文", "must_contain": "コワレル", "note": "repro-500"},
            {"text": "普通の文", "must_contain": "フツウ", "note": "ok"},
        ]

        def fake_query(role: str, text: str):
            if text == "壊れる文":
                raise RuntimeError("audio_queryに失敗: 壊れる文... (RequestException)")
            return {"accent_phrases": [{"moras": [{"text": "フ"}, {"text": "ツ"}, {"text": "ウ"}]}]}

        engine = MagicMock()
        engine.query.side_effect = fake_query

        results = reading_eval.run_dict_checks(engine, checks)

        self.assertEqual(len(results), 2)
        self.assertFalse(results[0]["passed"])
        self.assertEqual(results[0]["reading"], "")
        self.assertIn("RuntimeError", results[0]["error"])
        self.assertNotIn("壊れる文", results[0]["error"])
        self.assertTrue(results[1]["passed"])
        self.assertEqual(results[1]["error"], "")

    def test_http_error_status_is_recorded_without_url_or_script_text(self) -> None:
        checks = [{"text": "台本の行はここに入る想定", "must_contain": "ダメ"}]

        class FakeResponse:
            status_code = 500

        def fake_query(role: str, text: str):
            err = RuntimeError("wrapped")
            err.response = FakeResponse()
            raise err

        engine = MagicMock()
        engine.query.side_effect = fake_query

        results = reading_eval.run_dict_checks(engine, checks)

        self.assertEqual(results[0]["error"], "RuntimeError(status=500)")
        self.assertNotIn("台本の行はここに入る想定", results[0]["error"])

    def test_roles_key_runs_check_for_each_speaker(self) -> None:
        checks = [{"text": "両方で読む文", "must_contain": "リョウホウ", "roles": ["eme", "ruje"]}]
        calls = []

        def fake_query(role: str, text: str):
            calls.append(role)
            return {"accent_phrases": [{"moras": [{"text": "リ"}, {"text": "ョ"}, {"text": "ウ"},
                                                    {"text": "ホ"}, {"text": "ウ"}]}]}

        engine = MagicMock()
        engine.query.side_effect = fake_query

        results = reading_eval.run_dict_checks(engine, checks)

        self.assertEqual(calls, ["eme", "ruje"])
        self.assertEqual([r["role"] for r in results], ["eme", "ruje"])
        self.assertTrue(all(r["passed"] for r in results))


class TestRunFixtureRulesOnly(unittest.TestCase):
    def _engine(self) -> MagicMock:
        readings = {
            "その人はすごい": "ソノジンワスゴイ",
            "そのひとはすごい": "ソノヒトワスゴイ",
            "他にもある": "タニモアル",
            "ほかにもある": "ホカニモアル",
        }

        def fake_query(role: str, text: str):
            r = readings[text]
            return {"accent_phrases": [{"moras": [{"text": c} for c in r]}]}

        engine = MagicMock()
        engine.query.side_effect = fake_query
        return engine

    def _data(self) -> dict:
        return {
            "lines": [{"speaker": "eme", "text": "その人はすごい"},
                      {"speaker": "ruje", "text": "他にもある"}],
            "expected": [{"line": 1, "surface": "人", "heard": "ジン", "correct": "ヒト"}],
        }

    def test_fixed_by_rule_counted_without_llm(self) -> None:
        engine = self._engine()
        with patch("src.build_audio.find_misreadings") as fm:
            res = reading_eval.run_fixture(engine, "t", self._data(), [reading_eval.RULES_ONLY])
        fm.assert_not_called()
        m = res["per_model"][reading_eval.RULES_ONLY]
        self.assertEqual(res["baseline_misread"], 1)
        self.assertEqual(m["fixed"], 0)
        self.assertEqual(m["fixed_by_rule"], 1)  # 二重に数えない
        # ラベル外の行(2行目)への規則適用が残る
        self.assertEqual([d["index"] for d in m["unlabelled_rule_fixes"]], [2])

    def test_markdown_shows_rule_columns_and_label(self) -> None:
        engine = self._engine()
        with patch("src.build_audio.find_misreadings"):
            res = reading_eval.run_fixture(engine, "t", self._data(), [reading_eval.RULES_ONLY])
        md = reading_eval._build_markdown([res], [reading_eval.RULES_ONLY])
        self.assertIn("規則のみ", md)
        self.assertIn("規則で修正", md)
        self.assertIn("ラベル外の規則適用", md)
        self.assertIn("1/1（100%）", md)  # 再現率 = (fixed + fixed_by_rule) / 現状誤読


class TestMustKeep(unittest.TestCase):
    """must_keep（直してはいけない語）の「誤った書き換え」「元から誤読」の数え方。"""

    def _engine(self) -> MagicMock:
        readings = {
            "その人はすごい": "ソノジンワスゴイ",      # 規則で人→ひと に変わる（ジン→ヒト）
            "そのひとはすごい": "ソノヒトワスゴイ",
            "他にもある": "タニモアル",                # 規則で他→ほか に変わる。元から「ホカ」ではない
            "ほかにもある": "ホカニモアル",
            "ふつうの文": "フツウノブン",              # 何も変わらない
        }

        def fake_query(role: str, text: str):
            return {"accent_phrases": [{"moras": [{"text": c} for c in readings[text]]}]}

        engine = MagicMock()
        engine.query.side_effect = fake_query
        return engine

    def _data(self) -> dict:
        return {
            "lines": [{"speaker": "eme", "text": "その人はすごい"},
                      {"speaker": "ruje", "text": "他にもある"},
                      {"speaker": "eme", "text": "ふつうの文"}],
            "expected": [],
            "must_keep": [{"line": 1, "surface": "人", "reading": "ジン"},
                          {"line": 2, "surface": "他", "reading": "ホカ"},
                          {"line": 3, "surface": "文", "reading": "フツウ"}],
        }

    def _run(self, data: dict | None = None) -> dict:
        with patch("src.build_audio.find_misreadings") as fm:
            res = reading_eval.run_fixture(self._engine(), "t", data or self._data(),
                                           [reading_eval.RULES_ONLY])
        fm.assert_not_called()
        return res

    def test_broken_keep_and_already_wrong_are_counted_separately(self) -> None:
        m = self._run()["per_model"][reading_eval.RULES_ONLY]
        # 守る対象は「元から期待の読みを含む」1行目と3行目。2行目は元から誤読（対象外）
        self.assertEqual(m["keep_total"], 2)
        self.assertEqual(m["broken_keep"], [{"line": 1, "surface": "人", "expected": "ジン"}])
        self.assertEqual(m["keep_already_wrong"], [{"line": 2, "surface": "他", "expected": "ホカ"}])

    def test_fixture_without_must_keep_is_zero(self) -> None:
        data = self._data()
        del data["must_keep"]
        m = self._run(data)["per_model"][reading_eval.RULES_ONLY]
        self.assertEqual((m["keep_total"], m["broken_keep"], m["keep_already_wrong"]), (0, [], []))
        self.assertIn("| 0/0 |", reading_eval._build_markdown([self._run(data)], [reading_eval.RULES_ONLY]))

    def test_markdown_has_the_column_and_the_lists(self) -> None:
        md = reading_eval._build_markdown([self._run()], [reading_eval.RULES_ONLY])
        self.assertIn("誤った書き換え |", md)
        self.assertIn("| 1/2 |", md)
        self.assertIn("誤った書き換え 1/2", md)
        self.assertIn("1行目 「人」 期待の読み ジン", md)
        self.assertIn("元から誤読", md)
        self.assertIn("2行目 「他」 期待の読み ホカ", md)

    def test_unchanged_reading_is_not_broken(self) -> None:
        data = self._data()
        data["must_keep"] = [data["must_keep"][2]]
        m = self._run(data)["per_model"][reading_eval.RULES_ONLY]
        self.assertEqual((m["keep_total"], m["broken_keep"]), (1, []))


class TestGuardKeepFixture(unittest.TestCase):
    def test_guard_keep_json_has_the_agreed_shape(self) -> None:
        import json
        path = Path(__file__).resolve().parent / "fixtures" / "reading_eval" / "guard_keep.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["lines"]), 12)
        self.assertEqual(data["expected"], [])
        self.assertEqual(len(data["must_keep"]), 12)
        for k in data["must_keep"]:
            self.assertTrue(1 <= k["line"] <= 12)
            self.assertIn(k["surface"], data["lines"][k["line"] - 1]["text"])
            self.assertTrue(k["reading"])
        self.assertEqual([k["line"] for k in data["must_keep"]], list(range(1, 13)))
        self.assertEqual([ln["speaker"] for ln in data["lines"]], ["eme", "ruje"] * 6)
        self.assertNotIn(path.name, [reading_eval.DICT_CHECKS_FILENAME])
        names = [n for n, _ in reading_eval._load_fixtures(path.parent)]
        self.assertIn("guard_keep", names)


if __name__ == "__main__":
    unittest.main()
