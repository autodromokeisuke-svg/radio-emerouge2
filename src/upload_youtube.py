"""生成した放送MP3を、カバー画像の静止画動画にしてYouTubeへアップロードする（任意機能）。

環境変数 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN の
いずれかが未設定の場合は何もせずNoneを返す。ローカル実行時など未設定の環境でも
エラーにならない。動画化・認証・アップロードのいずれかに失敗しても、放送生成全体は
失敗させず警告のみで続行する（src/upload_drive.py と同じ思想）。

事前にリフレッシュトークンを取得するには tools/youtube_auth.py をローカルで実行すること。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

ROOT = Path(__file__).resolve().parent.parent
COVER_IMAGE = ROOT / "assets" / "cover.jpg"


def _build_video(mp3_path: Path, cover_path: Path, out_path: Path) -> bool:
    """カバー画像を全画面表示した静止画動画をffmpegで作る。成功したらTrueを返す。"""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(cover_path),
        "-i", str(mp3_path),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"[warn] ffmpegでの動画化に失敗しました (code={result.returncode}): "
                  f"{result.stderr[-2000:]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[warn] ffmpegの実行に失敗しました: {e}")
        return False


def upload_to_youtube(mp3_path: Path, title: str, description: str, cfg: dict) -> str | None:
    """mp3を静止画動画にしてYouTubeへアップロードする。成功したら動画IDを返す。

    失敗時（認証情報未設定・ffmpeg失敗・APIエラーなど、いかなる場合でも）例外を
    投げずNoneを返す。放送本体の生成を止めないため。
    """
    try:
        client_id = os.environ.get("YOUTUBE_CLIENT_ID")
        client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
        refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
        if not (client_id and client_secret and refresh_token):
            print("[skip] YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN "
                  "のいずれかが未設定のためYouTubeアップロードをスキップ")
            return None

        if not COVER_IMAGE.exists():
            print(f"[warn] カバー画像が見つからないためYouTubeアップロードをスキップ: {COVER_IMAGE}")
            return None

        youtube_cfg = cfg.get("youtube", {}) if isinstance(cfg, dict) else {}
        privacy_status = youtube_cfg.get("privacy_status", "private")
        tags = youtube_cfg.get("tags", [])

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=_SCOPES,
        )

        with tempfile.TemporaryDirectory(prefix="radio-emerouge-yt-") as tmpdir:
            video_path = Path(tmpdir) / "episode.mp4"
            if not _build_video(mp3_path, COVER_IMAGE, video_path):
                return None

            service = build("youtube", "v3", credentials=creds)
            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": "28",  # 科学と技術
                    "tags": tags,
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "selfDeclaredMadeForKids": False,
                },
            }
            media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                                     chunksize=-1, resumable=True)
            request = service.videos().insert(part="snippet,status", body=body, media_body=media)

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    print(f"[..] YouTubeアップロード中: {int(status.progress() * 100)}%")

            video_id = response.get("id") if response else None
            if not video_id:
                print("[warn] YouTubeアップロードのレスポンスから動画IDを取得できませんでした")
                return None
            print(f"[ok] YouTube公開: https://youtu.be/{video_id}")
            return video_id
    except Exception as e:  # noqa: BLE001
        print(f"[warn] YouTubeへのアップロードに失敗: {e}")
        return None
