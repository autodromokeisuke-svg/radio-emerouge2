"""生成したMP3をGoogle Driveの指定フォルダにアップロードする（任意機能）。

環境変数 GDRIVE_SERVICE_ACCOUNT_JSON （サービスアカウントの認証JSON）が
無い場合は何もしない。ローカル実行時など未設定の環境でもエラーにならない。
アップロード自体に失敗しても、放送生成全体は失敗させず警告のみで続行する。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def upload_to_drive(mp3_path: Path, folder_id: str) -> None:
    creds_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        print("[skip] GDRIVE_SERVICE_ACCOUNT_JSON未設定のためGoogle Driveアップロードをスキップ")
        return
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        service = build("drive", "v3", credentials=creds)
        media = MediaFileUpload(str(mp3_path), mimetype="audio/mpeg", resumable=False)
        service.files().create(
            body={"name": mp3_path.name, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()
        print(f"[ok] Google Driveへアップロード完了: {mp3_path.name}")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Google Driveへのアップロードに失敗: {e}")
