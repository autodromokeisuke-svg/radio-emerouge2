"""BGMの聴き比べ・音量調整用プレビュー（ローカルPC用）。

パイロット台本（assets/pilot_script_00.md）の見出しを section ラベルに対応づけ、
本番と同じ build() を通してBGM付きの音源を作る。config.yaml の bgm: を変えながら
繰り返し聴いて、声とBGMのバランスを耳で決めるための道具。

使い方:
  1) AivisSpeechのアプリを起動しておく（127.0.0.1:10101 でエンジンが立つ）
  2) python tools/bgm_preview.py
       → audition_out/bgm_preview.mp3
  3) 一時的に値を変えて試す（config.yamlは書き換えない）:
       python tools/bgm_preview.py --bed-db -6 --solo-db 0 --intro-boost-db 8
       python tools/bgm_preview.py --repeat-news 6   # ニュース区間を6回繰り返して長尺にし、ループの継ぎ目を確認
       python tools/bgm_preview.py --no-bgm          # BGM無し（比較用）

声の合成結果は audition_out/_voice_cache/ に保存し、同じ台本・同じリードインなら
2回目以降は再合成せずにBGMだけ重ね直す（音量の聴き比べを速くするため）。
--fresh を付けるとキャッシュを使わず作り直す（声や台本を変えたとき）。

パイロット台本は約5分と短いので、本番の20分規模でのループ（ニュース曲は約3分周期）を
確認したいときは --repeat-news を使う。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from pydub import AudioSegment  # noqa: E402

from src import bgm as bgm_mod  # noqa: E402
from src.build_audio import _prepare_bgm, add_bgm, synthesize, export_mp3  # noqa: E402

PILOT = ROOT / "assets" / "pilot_script_00.md"
LINE_RE = re.compile(r"\*\*(エメ|ルジェ)\*\*[：:]\s*(.+)")
ROLE = {"エメ": "eme", "ルジェ": "ruje"}
# 見出し → section
HEADING_SECTION = {"オープニング": "opening", "今日のニュース": "news",
                   "今日のひとこと": "glossary", "エンディング": "ending"}


def parse_pilot_with_sections() -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    section = "opening"
    for raw in PILOT.read_text(encoding="utf-8").splitlines():
        if raw.startswith("## "):
            for key, sec in HEADING_SECTION.items():
                if key in raw:
                    section = sec
            continue
        m = LINE_RE.search(raw)
        if m:
            lines.append({"speaker": ROLE[m.group(1)], "section": section,
                          "text": m.group(2).strip().replace("――", "、")})
    if not lines:
        raise RuntimeError("台本のセリフが読み取れなかった")
    return lines


def repeat_news(lines: list[dict[str, str]], times: int) -> list[dict[str, str]]:
    """news区間だけを times 回繰り返して長尺にする（ループの継ぎ目を確認するため）。"""
    if times <= 1:
        return lines
    news = [ln for ln in lines if ln["section"] == "news"]
    first = next(i for i, ln in enumerate(lines) if ln["section"] == "news")
    last = max(i for i, ln in enumerate(lines) if ln["section"] == "news")
    return lines[:first] + news * times + lines[last + 1:]


def _cached_voice(lines: list[dict[str, str]], tts_cfg: dict, lead_in_ms: int,
                  fresh: bool) -> tuple[AudioSegment, list[int]]:
    """声だけの放送を合成して返す。同じ台本・リードイン・声の設定ならキャッシュを使う。"""
    key = hashlib.sha1(json.dumps([lines, lead_in_ms, tts_cfg], ensure_ascii=False,
                                  sort_keys=True).encode("utf-8")).hexdigest()[:16]
    cache_dir = ROOT / "audition_out" / "_voice_cache"
    wav, meta = cache_dir / f"{key}.wav", cache_dir / f"{key}.json"
    if not fresh and wav.exists() and meta.exists():
        print(f"[ok] 声の合成結果をキャッシュから使用: {key}")
        return AudioSegment.from_wav(wav), json.loads(meta.read_text(encoding="utf-8"))
    show, starts_ms = synthesize(lines, tts_cfg, reading_check_model=None, lead_in_ms=lead_in_ms)
    cache_dir.mkdir(parents=True, exist_ok=True)
    show.export(wav, format="wav")
    meta.write_text(json.dumps(starts_ms), encoding="utf-8")
    return show, starts_ms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bed-db", type=float, help="声の下に敷く音量（dB）")
    ap.add_argument("--solo-db", type=float, help="曲だけの区間の音量（dB）")
    ap.add_argument("--intro-boost-db", type=float, help="冒頭の小さいイントロを持ち上げる量（dB）")
    ap.add_argument("--lead-in-ms", type=int, help="冒頭の曲だけの長さ（ms）")
    ap.add_argument("--repeat-news", type=int, default=1, help="ニュース区間の繰り返し回数")
    ap.add_argument("--no-bgm", action="store_true", help="BGM無しで作る（比較用）")
    ap.add_argument("--fresh", action="store_true", help="声のキャッシュを使わず作り直す")
    ap.add_argument("--out", default="bgm_preview.mp3", help="audition_out/ 以下の出力ファイル名")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    bgm_cfg = dict(cfg.get("bgm", {}))
    for arg, key in [(args.bed_db, "bed_db"), (args.solo_db, "solo_db"),
                     (args.intro_boost_db, "intro_boost_db"), (args.lead_in_ms, "lead_in_ms")]:
        if arg is not None:
            bgm_cfg[key] = arg

    lines = repeat_news(parse_pilot_with_sections(), args.repeat_news)
    bgm_ctx = None if args.no_bgm else _prepare_bgm(lines, bgm_cfg)
    lead_in_ms = int(bgm_ctx[0]["lead_in_ms"]) if bgm_ctx else 0
    shown = "無し" if not bgm_ctx else {k: bgm_ctx[0][k] for k in
            ("bed_db", "solo_db", "intro_boost_db", "lead_in_ms", "fade_in_ms", "fade_out_ms")}
    print(f"台本: パイロット#0（{len(lines)}セリフ） / BGM: {shown}")

    show, starts_ms = _cached_voice(lines, cfg["tts"], lead_in_ms, args.fresh)
    if bgm_ctx:
        show = add_bgm(show, starts_ms, bgm_ctx)
    out = ROOT / "audition_out" / args.out
    export_mp3(show, out)
    print(f"\nBGMプレビュー: {out}")


if __name__ == "__main__":
    main()
