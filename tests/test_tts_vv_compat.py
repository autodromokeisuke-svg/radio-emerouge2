"""VoicevoxCompatTTS._sync_reading_dict() の単体テスト
（標準ライブラリ unittest + unittest.mock のみ使用）。

読み間違い対策の辞書登録が、実機で確認した意図（新規追加・差分更新・
既存と一致なら何もしない・失敗しても放送を止めない）どおりに動くこと。

同期はCI（GITHUB_ACTIONS=true）か RADIO_SYNC_READING_DICT=1 のときだけ
実行される（ローカルPCの個人辞書を保護するガード）ため、実際の同期処理を
見るテストは _sync_env() でそのどちらかを立てた状態にする。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tts.vv_compat import VoicevoxCompatTTS


def _sync_env():
    """ローカルPC保護ガードを開けて同期処理本体をテストするための環境変数パッチ。"""
    return patch.dict(os.environ, {"RADIO_SYNC_READING_DICT": "1"})


def _make_engine() -> VoicevoxCompatTTS:
    return VoicevoxCompatTTS(
        base_url="http://127.0.0.1:10101",
        roles={"eme": {"speaker": "s", "style": "st"}, "ruje": {"speaker": "s2", "style": "st"}},
    )


_READING_DICT_YAML = """
words:
  - surface: "次"
    pronunciation: "ツギ"
    accent_type: 0
    word_type: "COMMON_NOUN"
"""


class TestSyncReadingDict(unittest.TestCase):
    def test_adds_new_word_when_not_present(self) -> None:
        engine = _make_engine()
        mock_get = MagicMock()
        mock_get.json.return_value = {}  # ユーザー辞書は空
        mock_post = MagicMock()
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get) as g, \
             patch("requests.post", return_value=mock_post) as p, \
             patch("requests.put") as put:
            engine._sync_reading_dict()

        g.assert_called_once_with("http://127.0.0.1:10101/user_dict", timeout=30)
        p.assert_called_once()
        self.assertEqual(p.call_args.kwargs["params"]["surface"], "次")
        self.assertEqual(p.call_args.kwargs["params"]["pronunciation"], "ツギ")
        put.assert_not_called()

    def test_skips_when_already_registered_identically(self) -> None:
        engine = _make_engine()
        mock_get = MagicMock()
        mock_get.json.return_value = {
            "uuid-1": {"surface": "次", "pronunciation": "ツギ", "accent_type": 0, "priority": 8},
        }
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get), \
             patch("requests.post") as post, \
             patch("requests.put") as put:
            engine._sync_reading_dict()

        post.assert_not_called()
        put.assert_not_called()

    def test_updates_when_registered_with_different_reading(self) -> None:
        engine = _make_engine()
        mock_get = MagicMock()
        mock_get.json.return_value = {
            "uuid-1": {"surface": "次", "pronunciation": "ジ", "accent_type": 0},  # 古い誤った内容
        }
        mock_put = MagicMock()
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get), \
             patch("requests.post") as post, \
             patch("requests.put", return_value=mock_put) as put:
            engine._sync_reading_dict()

        post.assert_not_called()
        put.assert_called_once()
        self.assertIn("uuid-1", put.call_args.args[0])
        self.assertEqual(put.call_args.kwargs["params"]["pronunciation"], "ツギ")

    def test_updates_when_priority_differs_even_if_reading_matches(self) -> None:
        """F(l): pronunciation/accent_typeが一致していても、priorityだけが
        古い場合は更新対象とみなす（以前はpriorityを比較していなかったため、
        yaml側でpriorityだけ上げても反映されない事故があった）。"""
        engine = _make_engine()
        mock_get = MagicMock()
        mock_get.json.return_value = {
            "uuid-1": {"surface": "次", "pronunciation": "ツギ", "accent_type": 0, "priority": 5},
        }
        mock_put = MagicMock()
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get), \
             patch("requests.post") as post, \
             patch("requests.put", return_value=mock_put) as put:
            engine._sync_reading_dict()

        post.assert_not_called()
        put.assert_called_once()
        self.assertEqual(put.call_args.kwargs["params"]["priority"], 8)

    def test_get_failure_is_fail_soft(self) -> None:
        """辞書取得自体が失敗しても例外を投げず、放送を止めないこと。"""
        engine = _make_engine()
        import requests
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", side_effect=requests.RequestException("timeout")):
            engine._sync_reading_dict()  # 例外を投げなければOK

    def test_one_word_failure_does_not_block_others(self) -> None:
        """1語の登録に失敗しても、残りの語の登録は続行すること。"""
        engine = _make_engine()
        two_words_yaml = _READING_DICT_YAML + """  - surface: "花王"
    pronunciation: "カオー"
    accent_type: 0
    word_type: "PROPER_NOUN"
"""
        mock_get = MagicMock()
        mock_get.json.return_value = {}
        import requests
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=two_words_yaml), \
             patch("requests.get", return_value=mock_get), \
             patch("requests.post", side_effect=[requests.RequestException("fail"), MagicMock()]) as post:
            engine._sync_reading_dict()  # 1件目が失敗しても例外は外に出ない

        self.assertEqual(post.call_count, 2)


class TestSyncReadingDictGuard(unittest.TestCase):
    """ローカルPC保護ガード（CI以外では実行しない）の単体テスト。"""

    def test_skips_without_ci_or_opt_in_env(self) -> None:
        engine = _make_engine()
        env = dict(os.environ)
        env.pop("GITHUB_ACTIONS", None)
        env.pop("RADIO_SYNC_READING_DICT", None)
        with patch.dict(os.environ, env, clear=True), \
             patch("requests.get") as get, patch("requests.post") as post, patch("requests.put") as put:
            engine._sync_reading_dict()

        get.assert_not_called()
        post.assert_not_called()
        put.assert_not_called()

    def test_runs_when_github_actions_env_is_true(self) -> None:
        engine = _make_engine()
        mock_get = MagicMock()
        mock_get.json.return_value = {}
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get) as get, \
             patch("requests.post") as post:
            engine._sync_reading_dict()

        get.assert_called_once()
        post.assert_called_once()

    def test_runs_when_opt_in_env_is_set(self) -> None:
        engine = _make_engine()
        mock_get = MagicMock()
        mock_get.json.return_value = {}
        with _sync_env(), \
             patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get) as get, \
             patch("requests.post") as post:
            engine._sync_reading_dict()

        get.assert_called_once()
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
