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
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
