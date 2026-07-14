"""声の聴き比べツール（ローカルPC用・1回きりのセットアップ作業）。

パイロット台本#0を、config.yaml の話者設定で読み上げてMP3にする。
エンジンやconfigの話者を変えながら何度か実行して、耳で決める。

使い方:
  1) エンジンを起動しておく
     - AivisSpeech: 公式アプリを起動するだけ（エンジンが127.0.0.1:10101で立つ）
     - VOICEVOX  : 公式アプリを起動（127.0.0.1:50021）
  2) 話者一覧を見る:   python tools/audition.py --list-speakers
  3) 聴き比べ音源作成: python tools/audition.py
     → audition_out/<エンジン名>.mp3 ができる

エンジンを変えたい時は config.yaml の tts.engine を書き換えるか、
`--engine voicevox` のように指定する。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
import yaml  # noqa: E402

from src.build_audio import build, export_mp3  # noqa: E402

PILOT = ROOT / "assets" / "pilot_script_00.md"
LINE_RE = re.compile(r"\*\*(エメ|ルジェ)\*\*[：:]\s*(.+)")
ROLE = {"エメ": "eme", "ルジェ": "ruje"}


def parse_pilot() -> list[dict[str, str]]:
    lines = []
    for m in LINE_RE.finditer(PILOT.read_text(encoding="utf-8")):
        text = m.group(2).strip().replace("――", "、")
        lines.append({"speaker": ROLE[m.group(1)], "text": text})
    if not lines:
        raise RuntimeError("台本のセリフが読み取れなかった")
    return lines


def list_speakers(base_url: str) -> None:
    speakers = requests.get(f"{base_url}/speakers", timeout=30).json()
    print(f"--- {base_url} の話者一覧 ---")
    for s in speakers:
        styles = " / ".join(st["name"] for st in s.get("styles", []))
        print(f"  {s['name']}: {styles}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["aivis", "voicevox", "elevenlabs"],
                    help="config.yaml の設定を一時的に上書き")
    ap.add_argument("--list-speakers", action="store_true",
                    help="エンジン内の話者・スタイル一覧を表示して終了")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    tts_cfg = cfg["tts"]
    if args.engine:
        tts_cfg["engine"] = args.engine
    engine_name = tts_cfg["engine"]

    if args.list_speakers:
        if engine_name == "elevenlabs":
            print("ElevenLabsの声はWebのVoicesページで確認してね")
            return
        list_speakers(tts_cfg[engine_name]["base_url"])
        return

    print(f"エンジン: {engine_name} / 台本: パイロット#0")
    audio = build(parse_pilot(), tts_cfg)
    out = ROOT / "audition_out" / f"{engine_name}.mp3"
    export_mp3(audio, out)
    print(f"\n聴き比べ音源: {out}\n"
          "話者を変えるには config.yaml の tts.<エンジン名>.eme/ruje を編集して再実行。")


if __name__ == "__main__":
    main()
