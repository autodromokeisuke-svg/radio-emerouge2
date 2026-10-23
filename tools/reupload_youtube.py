"""公開済みの放送を、同じ音声・タイトル・説明でYouTubeへ上げ直す（手動実行専用）。

YouTube側で動画を誤って削除した場合の復旧用。site/（gh-pagesのチェックアウト）に残っている
episodes/radio-YYYYMMDD.mp3 と .json から、本番(run_daily.py)と同じ組み立てで説明欄を作り、
src/upload_youtube.py の upload_to_youtube() で上げる。新しい動画IDとアップロード日になる。
site/latest.json 等は書き換えない。

  python tools/reupload_youtube.py --date 20261022 --base-url https://<owner>.github.io/<repo>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.upload_youtube import upload_to_youtube  # noqa: E402


def build_description(meta: dict[str, Any], show_cfg: dict[str, Any], base_url: str) -> str:
    """本番(src/run_daily.py)のYouTube説明欄と同じ組み立て。"""
    return f"{meta['description']}\n\n{show_cfg.get('credit', '')}\n\n{base_url.rstrip('/')}/feed.xml"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="上げ直す放送日 YYYYMMDD")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--site", default=str(ROOT / "site"))
    args = ap.parse_args()

    if not re.fullmatch(r"\d{8}", args.date):
        print("[error] --date は YYYYMMDD 形式で指定してください")
        return 2
    episodes = Path(args.site) / "episodes"
    meta_path = episodes / f"radio-{args.date}.json"
    if not meta_path.exists():
        print(f"[error] 放送のメタ情報が見つかりません: {meta_path}")
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    mp3 = episodes / meta["file"]
    if not mp3.exists():
        print(f"[error] 音声ファイルが見つかりません: {mp3}")
        return 2

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    description = build_description(meta, cfg["show"], args.base_url)
    print(f"[ok] 上げ直し対象: {meta['title']} / {mp3.name}")
    video_id = upload_to_youtube(mp3, meta["title"], description, cfg)
    if not video_id:
        print("[error] YouTubeへのアップロードに失敗しました")
        return 1
    print(f"[ok] 上げ直し完了: https://youtu.be/{video_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
