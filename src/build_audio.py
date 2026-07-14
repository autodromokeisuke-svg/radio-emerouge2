"""台本を1行ずつ音声合成し、1本の放送音源(MP3)に組み立てる。

- セリフ間に短い「間」を入れる
- assets/jingle.mp3 があれば冒頭と末尾に流す（任意）
- 音量をざっくり揃えてから書き出す
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from pydub import AudioSegment

from .tts import get_engine

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TARGET_DBFS = -16.0

# 英字略語＋カタカナ読みの二重読み防止パターン
# 例: "TSMC（ティーエスエムシー）" / "GPT-4(ジーピーティーフォー)" -> カタカナ読みだけに置換
_ALPHA_READING_GLOSS_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9\-]*[（(]([ァ-ヴー・]+)[）)]"
)


def _strip_alpha_reading_gloss(text: str) -> str:
    """「英字（カタカナ読み）」を、英字を消してカタカナ読みだけに置換する。

    括弧内がカタカナ以外の文字を含む場合（例: AI（人工知能））は対象外。
    """
    return _ALPHA_READING_GLOSS_RE.sub(r"\1", text)


def _normalize(seg: AudioSegment) -> AudioSegment:
    if seg.dBFS == float("-inf"):
        return seg
    return seg.apply_gain(TARGET_DBFS - seg.dBFS)


def build(lines: list[dict[str, str]], tts_cfg: dict[str, Any]) -> AudioSegment:
    engine = get_engine(tts_cfg)
    engine.prepare()

    pause = AudioSegment.silent(duration=int(tts_cfg.get("pause_ms", 350)))
    show = AudioSegment.silent(duration=300)

    jingle_path = ASSETS / "jingle.mp3"
    jingle = None
    if jingle_path.exists():
        jingle = _normalize(AudioSegment.from_file(jingle_path))
        show += jingle + pause

    total = len(lines)
    failed = 0
    for i, ln in enumerate(lines, 1):
        try:
            text = _strip_alpha_reading_gloss(ln["text"])
            seg = engine.synth(ln["speaker"], text)
            show += _normalize(seg) + pause
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[warn] セリフ{i}の合成をスキップ: {e}")
        if i % 10 == 0 or i == total:
            print(f"[..] 収録中 {i}/{total}")

    if failed > total // 4:
        raise RuntimeError(f"合成失敗が多すぎる ({failed}/{total})。エンジン状態を確認して。")

    if jingle is not None:
        show += jingle

    minutes = len(show) / 1000 / 60
    print(f"[ok] 収録完了: 約{minutes:.1f}分")
    return show


def _embed_cover_art(mp3_path: Path, cover_path: Path) -> None:
    try:
        try:
            tags = ID3(mp3_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,  # front cover
            desc="Cover",
            data=cover_path.read_bytes(),
        ))
        tags.save(mp3_path)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] カバー画像の埋め込みに失敗: {e}")


def export_mp3(show: AudioSegment, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    show.export(out_path, format="mp3", bitrate="96k",
                tags={"artist": "えめるーじぇ"})

    cover_path = ASSETS / "cover.jpg"
    if cover_path.exists():
        _embed_cover_art(out_path, cover_path)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"[ok] 書き出し: {out_path} ({size_mb:.1f} MB)")
