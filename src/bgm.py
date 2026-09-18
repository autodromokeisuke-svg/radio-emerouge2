"""番組BGM（メイン曲／ニュース曲）の区間計画とミキシング。

BGMを流す区間は、台本の各セリフに付けた section ラベルで決める。

  opening  … 番組の最初 〜 1本目のニュース開始の直前        → メイン曲
  news     … 1本目のニュース開始 〜 今日のひとこと終了        → ニュース曲
  glossary … 今日のひとこと（ニュース曲の区間に含める）
  ending   … 今日の振り返り 〜 番組終了                       → メイン曲

各区間の頭でフェードイン、終わりでフェードアウトする。声が乗っている間は
小さく敷き（bed）、曲だけの区間（冒頭のリードイン・末尾の余韻）は少し大きくする。

BGMは付加価値であって放送の本体ではない。ラベル不正・素材欠落・処理失敗のいずれでも
例外を投げず、BGM無し（声だけ）の放送を返す（放送を止めない）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydub import AudioSegment

SECTION_ORDER = ("opening", "news", "glossary", "ending")
_RANK = {name: i for i, name in enumerate(SECTION_ORDER)}

ROOT = Path(__file__).resolve().parent.parent

# config.yaml に bgm: が無い/一部欠けている場合の既定値
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "main_file": "assets/bgm_main.mp3",
    "news_file": "assets/bgm_news.mp3",
    "bed_db": -8.0,             # メイン曲を声の下に敷く音量（曲本来の音量からの相対dB）
    "news_bed_db": -11.0,       # ニュース曲を声の下に敷く音量（メインより小さめ。長時間流れるため）
    "solo_db": 0.0,             # 曲だけの区間（盛り上がり・余韻）の音量（同上）
    "intro_boost_db": 8.0,      # 冒頭の小さいイントロを持ち上げる量（盛り上がりの直前に0へ戻す）
    "swell_ms": 1200,           # イントロの持ち上げを戻す（＝盛り上がりに入る）ための時間
    "fade_in_ms": 3000,
    "fade_out_ms": 4000,
    "lead_in_ms": 8000,         # 番組冒頭、声が始まる前に曲だけ流す長さ（曲の盛り上がりまで）
    "tail_ms": 5000,            # 最後のセリフの後に曲だけ流す余韻の長さ
    "duck_ms": 1200,            # 曲だけ⇄声ありの音量の切り替えにかける時間
    "loop_head_ms": 8000,       # ループ2周目以降は、曲の静かなイントロをこの長さ飛ばす
    "loop_tail_ms": 8000,       # 曲末尾のフェードアウト（＋無音）をこの長さ切り落とす
    "loop_crossfade_ms": 3000,  # ループの継ぎ目のクロスフェード
}


def resolve_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    merged.update(cfg or {})
    return merged


def plan_spans(sections: list[str]) -> dict[str, int] | None:
    """行ごとの section ラベルから、BGMの切り替わり位置（行番号）を返す。不正ならNone。

    戻り値: {"news_first": 最初のnews行, "ending_first": 最初のending行}
    次を満たさない台本はBGMを付けられないのでNone（呼び出し側はBGM無しで続行する）。
      - 全行が SECTION_ORDER のいずれかのラベルを持つ
      - opening → news → glossary → ending の順で、逆戻りしない
      - opening / news / ending が1行以上ある（glossaryは無くてもよい）
    """
    if not sections:
        return None
    ranks = []
    for s in sections:
        if s not in _RANK:
            return None
        ranks.append(_RANK[s])
    if any(b < a for a, b in zip(ranks, ranks[1:])):
        return None
    present = set(sections)
    if not {"opening", "news", "ending"} <= present:
        return None
    return {"news_first": sections.index("news"),
            "ending_first": sections.index("ending")}


def _sync(seg: AudioSegment, like: AudioSegment) -> AudioSegment:
    return seg.set_frame_rate(like.frame_rate).set_sample_width(like.sample_width)


def build_loop(track: AudioSegment, length_ms: int, head_ms: int, tail_ms: int,
               crossfade_ms: int, use_intro: bool = True) -> AudioSegment:
    """曲を length_ms 以上になるまで繋げて、ちょうど length_ms に切り出す。

    use_intro=True の1周目は曲の頭から（自然なイントロつき）、False なら最初から
    静かなイントロを飛ばした本体部分で始める（区間の頭から音がしっかり聞こえる）。
    2周目以降は常に本体部分を、クロスフェードで繋ぐ。曲末尾のフェードアウトは切り落とす。
    曲の頭尾をそのまま繰り返すと、継ぎ目ごとに音量が凹んで「呼吸する」ように
    聞こえるため。曲が短すぎて切り出せない場合は、曲全体をそのまま繰り返す。
    """
    if length_ms <= 0:
        return track[:0]
    body_end = len(track) - tail_ms
    if body_end - head_ms <= crossfade_ms * 2:  # 切り出すと短すぎる曲
        first = body = track
        crossfade_ms = min(crossfade_ms, max(len(track) // 4, 0))
    else:
        body = track[head_ms:body_end]
        first = track[:body_end] if use_intro else body
    out = first
    guard = 0
    while len(out) < length_ms + crossfade_ms:
        out = out.append(body, crossfade=crossfade_ms)
        guard += 1
        if guard > 500:  # 想定外の無限ループ防止（20分でも高々10周程度）
            break
    return out[:length_ms]


def shape_span(bed: AudioSegment, length_ms: int, *, solo_db: float, bed_db: float,
               lead_in_ms: int, tail_ms: int, duck_ms: int,
               fade_in_ms: int, fade_out_ms: int,
               intro_boost_db: float = 0.0, swell_at_ms: int = 0,
               swell_ms: int = 0) -> AudioSegment:
    """ループ済みの曲を、1つの区間の音量カーブに整形する。

    lead_in_ms は「声が始まる位置（区間の頭からの時間）」。0なら区間の最初から声がある。
    冒頭（lead_in_ms>0）:
      - 曲の小さいイントロを intro_boost_db だけ持ち上げて聞こえるようにする。曲本体
        （盛り上がり）が始まる swell_at_ms の手前 swell_ms かけて元の音量（solo_db）へ戻す。
        本体まで持ち上げたままだとクリップするため。
      - 声が始まったら duck_ms かけて bed_db へ下げる
    末尾（tail_ms>0）:
      - 最後の声が終わったら duck_ms かけて solo_db へ戻す（余韻）
    区間の頭でフェードイン、終わりでフェードアウト。
    """
    seg = bed[:length_ms]
    up = solo_db - bed_db  # 「声の下」から「曲だけ」へ戻すときの相対ゲイン（正）
    voice_end = length_ms - tail_ms

    if lead_in_ms > 0 and length_ms > lead_in_ms + duck_ms:
        seg = seg.apply_gain(solo_db + intro_boost_db)
        if intro_boost_db != 0 and swell_at_ms > 0:
            ramp_start = max(0, swell_at_ms - swell_ms)
            seg = seg.fade(from_gain=0, to_gain=-intro_boost_db,
                           start=ramp_start, end=swell_at_ms)
        elif intro_boost_db != 0:
            seg = seg.apply_gain(-intro_boost_db)
        seg = seg.fade(from_gain=0, to_gain=-up,
                       start=lead_in_ms, end=lead_in_ms + duck_ms)
    else:
        seg = seg.apply_gain(bed_db)
    if tail_ms > 0 and voice_end > lead_in_ms:
        # 最後の声が終わったら、曲だけの音量へ戻す（余韻）。範囲は区間内に収める
        seg = seg.fade(from_gain=0, to_gain=up,
                       start=voice_end, end=min(voice_end + duck_ms, length_ms))

    fi = min(fade_in_ms, length_ms // 2)
    fo = min(fade_out_ms, length_ms // 2)
    if fi > 0:
        seg = seg.fade_in(fi)
    if fo > 0:
        seg = seg.fade_out(fo)
    return seg


def mix_bgm(show: AudioSegment, plan: dict[str, int], starts_ms: list[int],
            main: AudioSegment, news: AudioSegment, cfg: dict[str, Any],
            tail_ms: int) -> AudioSegment:
    """声だけの放送 show に、区間ごとのBGMを重ねて返す。

    starts_ms[i] は i 番目のセリフが show の中で始まる位置（ミリ秒）。starts_ms[0] より
    前は曲だけの区間（リードイン）。show は末尾に tail_ms の無音（余韻）を含むこと。
    オープニングだけ曲の頭（イントロ→盛り上がり）から流し、ニュースとエンディングは
    小さいイントロを飛ばして本体から流す。
    """
    total = len(show)
    voice_start = starts_ms[0]
    news_start = starts_ms[plan["news_first"]]
    ending_start = starts_ms[plan["ending_first"]]

    common = dict(
        solo_db=float(cfg["solo_db"]),
        duck_ms=int(cfg["duck_ms"]),
        fade_in_ms=int(cfg["fade_in_ms"]), fade_out_ms=int(cfg["fade_out_ms"]),
    )
    main_bed = float(cfg["bed_db"])
    news_bed = float(cfg.get("news_bed_db", cfg["bed_db"]))
    loop_kw = dict(head_ms=int(cfg["loop_head_ms"]), tail_ms=int(cfg["loop_tail_ms"]),
                   crossfade_ms=int(cfg["loop_crossfade_ms"]))

    main = _sync(main, show)
    news = _sync(news, show)

    spans = [
        # (曲, 開始, 終了, 声の開始位置(区間内), 末尾の曲だけ区間, イントロから流すか, 敷き音量)
        (main, 0, news_start, voice_start, 0, True, main_bed),
        (news, news_start, ending_start, 0, 0, False, news_bed),
        (main, ending_start, total, 0, tail_ms, False, main_bed),
    ]
    music = None
    for track, start, end, lead, tail, use_intro, bed_db in spans:
        length = end - start
        if length <= 0:
            continue
        looped = build_loop(track, length, use_intro=use_intro, **loop_kw)
        extra = {}
        if use_intro:
            extra = dict(intro_boost_db=float(cfg["intro_boost_db"]),
                         swell_at_ms=int(cfg["loop_head_ms"]),
                         swell_ms=int(cfg["swell_ms"]))
        piece = shape_span(looped, length, lead_in_ms=lead, tail_ms=tail, bed_db=bed_db,
                           **common, **extra)
        music = piece if music is None else music + piece
    if music is None:
        return show
    return show.overlay(music[:total])


def load_track(path_str: str) -> AudioSegment | None:
    path = Path(path_str)
    if not path.is_absolute():
        path = ROOT / path
    try:
        return AudioSegment.from_file(path)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] BGM素材を読み込めません（BGM無しで続行）: {path} ({e})")
        return None
