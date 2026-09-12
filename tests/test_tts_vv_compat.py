"""VoicevoxCompatTTS._sync_reading_dict() の単体テスト
（標準ライブラリ unittest + unittest.mock のみ使用）。

読み間違い対策の辞書登録が、実機で確認した意図（新規追加・差分更新・
既存と一致なら何もしない・失敗しても放送を止めない）どおりに動くこと。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tts.vv_compat import VoicevoxCompatTTS


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
        with patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
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
            "uuid-1": {"surface": "次", "pronunciation": "ツギ", "accent_type": 0},
        }
        with patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
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
        with patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
             patch("requests.get", return_value=mock_get), \
             patch("requests.post") as post, \
             patch("requests.put", return_value=mock_put) as put:
            engine._sync_reading_dict()

        post.assert_not_called()
        put.assert_called_once()
        self.assertIn("uuid-1", put.call_args.args[0])
        self.assertEqual(put.call_args.kwargs["params"]["pronunciation"], "ツギ")

    def test_get_failure_is_fail_soft(self) -> None:
        """辞書取得自体が失敗しても例外を投げず、放送を止めないこと。"""
        engine = _make_engine()
        import requests
        with patch.object(Path, "read_text", return_value=_READING_DICT_YAML), \
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
        with patch.object(Path, "read_text", return_value=two_words_yaml), \
             patch("requests.get", return_value=mock_get), \
             patch("requests.post", side_effect=[requests.RequestException("fail"), MagicMock()]) as post:
            engine._sync_reading_dict()  # 1件目が失敗しても例外は外に出ない

        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
