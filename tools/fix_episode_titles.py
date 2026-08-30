"""既存エピソードJSONのタイトル表記揺れを一括で正規表記に直すツール。

台本生成AIが自由に付けていたタイトル（曜日が入ったり「号」が抜けたり、
日付を誤記したりと表記が日によってブレていた）を、各JSONの `date`
フィールド（YYYYMMDD）から機械的に組み立てた正規表記で上書きする。

タイトル生成ロジックは src/run_daily.py と共通の
src.make_feed.build_episode_title() を使う（二重定義するとまたズレるため）。

使い方:
  python tools/fix_episode_titles.py            # dry-run（既定・書き換えない）
  python tools/fix_episode_titles.py --apply    # 実際にJSONを書き換える
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.make_feed import build_episode_title  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="指定した場合のみ実際にJSONを書き換える（既定はdry-run）")
    args = parser.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    show_title = cfg["show"]["title"]

    episodes_dir = ROOT / "site" / "episodes"
    files = sorted(episodes_dir.glob("radio-*.json"))
    if not files:
        print(f"[warn] エピソードJSONが見つかりません: {episodes_dir}")
        return

    changed = 0
    for path in files:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[skip] {path.name}: 読み込み失敗 ({e})")
            continue
        if not isinstance(meta, dict) or "date" not in meta or "title" not in meta:
            print(f"[skip] {path.name}: date/titleが無い")
            continue

        date_key = str(meta["date"])
        try:
            dt = datetime.strptime(date_key, "%Y%m%d")
        except ValueError:
            print(f"[skip] {path.name}: dateの形式が不正 ({date_key})")
            continue

        old_title = meta["title"]
        new_title = build_episode_title(show_title, dt)
        if old_title == new_title:
            continue

        print(f"{path.name}: 「{old_title}」 -> 「{new_title}」")
        changed += 1
        if args.apply:
            meta["title"] = new_title
            path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    if changed == 0:
        print("[ok] 変更対象なし")
    elif args.apply:
        print(f"[ok] {changed}件を書き換えました")
    else:
        print(f"[dry-run] {changed}件が変更対象です（--applyで実際に書き換え）")


if __name__ == "__main__":
    main()
