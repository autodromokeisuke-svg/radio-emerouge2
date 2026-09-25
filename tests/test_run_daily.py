"""試し放送向けの環境変数フック（_apply_script_model_override / _uploads_disabled）の単体テスト。

標準ライブラリ unittest + unittest.mock のみ使用。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.run_daily import _apply_script_model_override, _uploads_disabled


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


if __name__ == "__main__":
    unittest.main()
