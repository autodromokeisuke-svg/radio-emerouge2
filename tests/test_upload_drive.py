"""upload_to_drive() の単体テスト（標準ライブラリ unittest + unittest.mock のみ使用）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.upload_drive import upload_to_drive


class TestUploadToDrive(unittest.TestCase):
    def test_skips_silently_when_env_var_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            # 例外を投げず、何も起きずに正常終了すること
            upload_to_drive(Path("dummy.mp3"), "folder123")

    def test_calls_drive_api_when_configured(self) -> None:
        fake_creds_info = {
            "type": "service_account",
            "client_email": "test@example.iam.gserviceaccount.com",
        }
        mock_service = MagicMock()
        with patch.dict("os.environ", {"GDRIVE_SERVICE_ACCOUNT_JSON": json.dumps(fake_creds_info)}), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info",
                   return_value="fake-creds") as mock_from_info, \
             patch("googleapiclient.discovery.build", return_value=mock_service) as mock_build, \
             patch("googleapiclient.http.MediaFileUpload") as mock_media:
            upload_to_drive(Path("radio-20260101.mp3"), "folder123")

        mock_from_info.assert_called_once()
        mock_build.assert_called_once_with("drive", "v3", credentials="fake-creds")
        mock_service.files.return_value.create.assert_called_once()
        _, kwargs = mock_service.files.return_value.create.call_args
        self.assertEqual(kwargs["body"], {"name": "radio-20260101.mp3", "parents": ["folder123"]})

    def test_api_error_does_not_raise(self) -> None:
        with patch.dict("os.environ", {"GDRIVE_SERVICE_ACCOUNT_JSON": "{not valid json"}):
            # JSON解析失敗でも例外を外に投げず、警告のみで終わること
            upload_to_drive(Path("dummy.mp3"), "folder123")


if __name__ == "__main__":
    unittest.main()
