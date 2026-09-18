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

from . import bgm as bgm_mod
from .reading_check import extract_reading, verify_readings
from .tts import get_engine

# 読み検証ループの最大周回数（1周目: 全セリフ検証、2周目: 修正行のみ再検証）
_MAX_READING_CHECK_PASSES = 2

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


# 音声合成エンジンが繰り返し誤読する語の固定置換（プロンプト指示だけでは
# 再発したため保険として追加）。「重め/重い」は「じゅうめ/ちょう」等に
# 誤読されるが、この番組の文脈では常に「おも」と読ませたいので安全に置換できる
_KNOWN_MISREADINGS = {
    "重め": "おもめ",
    "重い": "おもい",
}


def _fix_known_misreadings(text: str) -> str:
    for wrong, right in _KNOWN_MISREADINGS.items():
        text = text.replace(wrong, right)
    return text


def _normalize(seg: AudioSegment) -> AudioSegment:
    if seg.dBFS == float("-inf"):
        return seg
    return seg.apply_gain(TARGET_DBFS - seg.dBFS)


def _run_reading_check(engine: Any, lines: list[dict[str, str]],
                       prepared_texts: list[str], model: str) -> list[dict | None]:
    """全セリフの読みをエンジンに問い合わせ、Claude APIで原文と照合して直す。

    最大 _MAX_READING_CHECK_PASSES 周（1周目: 全セリフ検証、2周目: 修正行のみ再検証）。
    戻り値は合成にそのまま使えるクエリJSONのリスト（seriesとlinesは同じ長さ・順序）。
    """
    texts = list(prepared_texts)
    queries: list[dict | None] = [None] * len(lines)

    # 1周目: 全セリフを検証
    pairs = []
    for i, ln in enumerate(lines):
        q = engine.query(ln["speaker"], texts[i])
        queries[i] = q
        pairs.append({"index": i + 1, "text": texts[i], "reading": extract_reading(q)})

    corrections = verify_readings(pairs, model)
    if not corrections:
        return queries

    for idx, fixed_text in corrections.items():
        pos = idx - 1
        if 0 <= pos < len(lines):
            texts[pos] = fixed_text

    # 2周目: 修正した行だけ再検証（無限ループ防止のためここで打ち切り）
    pairs2 = []
    for idx in sorted(corrections.keys()):
        pos = idx - 1
        if not (0 <= pos < len(lines)):
            continue
        q = engine.query(lines[pos]["speaker"], texts[pos])
        queries[pos] = q
        pairs2.append({"index": idx, "text": texts[pos], "reading": extract_reading(q)})

    corrections2 = verify_readings(pairs2, model) if pairs2 else {}
    if corrections2:
        still_wrong = ", ".join(f"{idx}行目" for idx in sorted(corrections2.keys()))
        print(f"[warn] 読み検証: {_MAX_READING_CHECK_PASSES}周後も修正提案が残る行はそのまま採用します ({still_wrong})")

    return queries


def _prepare_bgm(lines: list[dict[str, str]], bgm_cfg: dict[str, Any] | None):
    """BGMを付けられるか判定し、(設定, 区間計画, メイン曲, ニュース曲) を返す。

    付けない（無効・ラベル不正・素材が読めない）場合は None。BGMは付加価値なので、
    どの理由でも例外にせず、声だけの放送を成立させる。
    """
    if bgm_cfg is None:
        return None
    settings = bgm_mod.resolve_config(bgm_cfg)
    if not settings["enabled"]:
        return None
    plan = bgm_mod.plan_spans([ln.get("section", "") for ln in lines])
    if plan is None:
        print("[warn] セリフのsectionラベルが不正なため、BGM無しで続行します")
        return None
    main = bgm_mod.load_track(settings["main_file"])
    news = bgm_mod.load_track(settings["news_file"])
    if main is None or news is None:
        return None
    return settings, plan, main, news


def synthesize(lines: list[dict[str, str]], tts_cfg: dict[str, Any],
               reading_check_model: str | None = None,
               lead_in_ms: int = 0) -> tuple[AudioSegment, list[int]]:
    """全セリフを合成して声だけの放送を作り、(放送, 各セリフの開始位置ms) を返す。

    lead_in_ms は先頭に足す無音（BGMの曲だけ流すリードイン用）の長さ。
    BGMを重ねる処理(add_bgm)とは分けてあり、BGMの音量調整時は声の合成結果を使い回せる。
    """
    engine = get_engine(tts_cfg)
    engine.prepare()

    pause = AudioSegment.silent(duration=int(tts_cfg.get("pause_ms", 350)))
    show = AudioSegment.silent(duration=300 + lead_in_ms)

    jingle_path = ASSETS / "jingle.mp3"
    jingle = None
    if jingle_path.exists():
        jingle = _normalize(AudioSegment.from_file(jingle_path))
        show += jingle + pause

    total = len(lines)
    prepared_texts = [_fix_known_misreadings(_strip_alpha_reading_gloss(ln["text"]))
                      for ln in lines]
    queries: list[dict | None] = [None] * total

    if reading_check_model and getattr(engine, "supports_reading_check", False):
        try:
            queries = _run_reading_check(engine, lines, prepared_texts, reading_check_model)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 読み検証フェーズに失敗。通常合成にフォールバックします: {e}")
            queries = [None] * total

    failed = 0
    starts_ms = [0] * total  # 各セリフが show の中で始まる位置（BGMの区間切り替えに使う）
    for i, ln in enumerate(lines, 1):
        starts_ms[i - 1] = len(show)
        try:
            q = queries[i - 1]
            if q is not None:
                seg = engine.synth_from_query(ln["speaker"], q)
            else:
                seg = engine.synth(ln["speaker"], prepared_texts[i - 1])
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
    return show, starts_ms


def add_bgm(show: AudioSegment, starts_ms: list[int], bgm_ctx) -> AudioSegment:
    """声だけの放送に区間ごとのBGMを重ねる。失敗しても声だけの放送をそのまま返す。"""
    settings, plan, main_track, news_track = bgm_ctx
    tail_ms = int(settings["tail_ms"])
    show = show + AudioSegment.silent(duration=tail_ms)  # 最後の声のあとの余韻ぶん
    try:
        mixed = bgm_mod.mix_bgm(show, plan, starts_ms, main_track, news_track,
                                settings, tail_ms)
        print("[ok] BGMを重ねました（メイン: オープニング/エンディング、ニュース: 本編〜今日のひとこと）")
        return mixed
    except Exception as e:  # noqa: BLE001
        print(f"[warn] BGMのミキシングに失敗（声だけで続行）: {e}")
        return show


def build(lines: list[dict[str, str]], tts_cfg: dict[str, Any],
         reading_check_model: str | None = None,
         bgm_cfg: dict[str, Any] | None = None) -> AudioSegment:
    bgm_ctx = _prepare_bgm(lines, bgm_cfg)
    lead_in_ms = int(bgm_ctx[0]["lead_in_ms"]) if bgm_ctx else 0

    show, starts_ms = synthesize(lines, tts_cfg, reading_check_model, lead_in_ms)
    if bgm_ctx:
        show = add_bgm(show, starts_ms, bgm_ctx)

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
