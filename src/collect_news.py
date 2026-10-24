"""RSSフィードから直近のAIニュースを集める。

- 直近26時間の記事を対象（フィード全体で日付が取れない場合のみ先頭数件を採用。
  日付付きフィード内で個別記事だけ日付が取れない場合は鮮度不明として除外する）
- keyword_filter のいずれかをタイトル/概要に含むものだけ残す
- タイトルの重複を除去して最大24件返す
- スパム対策（2026-10-23）。Googleニュース検索フィードに、官公庁・大学の出典を
  装ったAI彼女・AIポルノ系の記事が大量に混ざるため、次の2つで抑える
  1. タイトルに成人向け・出会い系の語（_BLOCKED_TITLE_TERMS）を含む記事を除外する。
     判定はタイトルのみ（概要は見ない）。「彼女」を含むため、本物のニュースが落ちる
     トレードオフは承知の上（官公庁/大学の出典を一律で除外はしない）
  2. 同じ出典の記事は collect() 全体（全フィード通算）で MAX_PER_PUBLISHER 件まで
- スパム対策の追加（2026-10-24）。上の2つをすり抜ける、語を含まないスパム
  （例「生成 ai 画像 生成 - 小豆島町」）への対策
  3. 洪水検知: 1つのフィード内（日付フィルタ後・keyword_filter 前）で同じ出典が
     FLOOD_THRESHOLD 件以上あれば、その出典の記事をそのフィードから全件除外する。
     フィードごとに数える（フィードをまたいで合算しない）。出典を特定できない記事は対象外。
     正当な出典は1フィードに最大2件程度、実スパムは数十件並ぶため、閾値は9件
  4. タイトルが先頭のゼロ幅文字（U+200B/200C/200D/2060/FEFF）で始まる記事を除外する。
     スパムの目印。語中・末尾のゼロ幅文字（絵文字のZWJ列など）は対象外
"""
from __future__ import annotations

import calendar
import re
import time
import unicodedata
from typing import Any

import feedparser

WINDOW_SEC = 26 * 3600
MAX_ITEMS = 24
PER_FEED_FALLBACK = 6
MAX_PER_PUBLISHER = 3
FLOOD_THRESHOLD = 9  # 1フィード内で同一出典がこの件数以上なら、その出典を全件除外する

# タイトルにこれらを含む記事は除外する（NFKC+casefold で正規化した部分一致）
_BLOCKED_TITLE_TERMS = (
    "彼女", "彼氏", "ガールフレンド", "ボーイフレンド", "ポルノ", "セックス",
    "エロ", "ヘンタイ", "ミルフ", "nsfw", "アダルト", "成人向け", "18+",
    "出会い系", "セクスティング", "ヌード", "脱衣", "undress", "porn", "sex",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(html: str, limit: int = 280) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()
    return text[:limit]


def _entry_epoch(entry: Any) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return calendar.timegm(t)
    return None


# 末尾の「 - 情報源名」。区切りの前後に空白が要る。空白を要求しないと「GPT-5.5 is out」と
# 「GPT-4o launch today」の語中のハイフン以降が出典扱いで削られ、どちらも「gpt」になって
# 別の記事が同じ記事として消える（2026-10-23発覚）
_SOURCE_SUFFIX_RE = re.compile(r"[\s　]+[-–—][\s　]+[^-–—]{1,30}$")


def _norm_title(title: str) -> str:
    """比較用にタイトルを正規化する。

    Google Newsなどでは同一記事が末尾の「 - 情報源名」だけ違う形で
    複数エントリになることがあるため、正規化前に末尾の情報源表記を除く。
    """
    title = _SOURCE_SUFFIX_RE.sub("", title)
    return re.sub(r"[\s　【】\[\]（）()「」|｜:：\-–—・、。!！?？]", "", title).lower()


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


# タイトル先頭に付くとスパムの目印になるゼロ幅文字（ZWSP/ZWNJ/ZWJ/WORD JOINER/BOM）
_LEADING_ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"

_BLOCKED_FOLDED = tuple(_fold(t) for t in _BLOCKED_TITLE_TERMS)
_SOURCE_PREFIX_RE = re.compile(r"^[\s　]*[-–—][\s　]*")


def _has_blocked_term(title: str) -> bool:
    folded = _fold(title)
    return any(t in folded for t in _BLOCKED_FOLDED)


def _starts_with_zero_width(title: str) -> bool:
    """通常の空白を除いた先頭がゼロ幅文字か。語中・末尾は見ない。"""
    t = title.lstrip()
    return bool(t) and t[0] in _LEADING_ZERO_WIDTH


def _publisher(entry: Any, title: str) -> str:
    """出典名を返す。entry.source.title → タイトル末尾の「 - 出典名」の順。特定できなければ空文字。

    空文字（出典を特定できない）の記事は、出典上限の対象にしない。ITmedia・Publickey・
    TechCrunch のような単独出典のフィードは、フィード名で数えると1日3件に絞られてしまう
    （絞りたいのは、1つのフィードの中に偽の出典が大量に混ざる集約フィードだけ）。
    """
    src = entry.get("source")
    name = src.get("title") if isinstance(src, dict) else None
    if not (name and str(name).strip()):
        m = _SOURCE_SUFFIX_RE.search(title)
        name = _SOURCE_PREFIX_RE.sub("", m.group(0)) if m else ""
    return str(name).strip()


def _publisher_key(name: str) -> str:
    return _WS_RE.sub("", _fold(name))


def filter_recent(items: list[dict[str, str]],
                  recent: list[dict[str, str]]) -> list[dict[str, str]]:
    """直近で使用済みのニュース（タイトル完全一致 or リンク完全一致）を除外する。"""
    seen_titles = {_norm_title(r.get("title", "")) for r in recent}
    seen_links = {r["link"] for r in recent if r.get("link")}
    return [it for it in items
            if _norm_title(it["title"]) not in seen_titles
            and (not it.get("link") or it["link"] not in seen_links)]


def collect(feeds: list[str], keywords: list[str]) -> list[dict[str, str]]:
    now = time.time()
    kws = [k.lower() for k in keywords]
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    pub_counts: dict[str, int] = {}
    blocked = 0
    capped: dict[str, int] = {}
    capped_names: dict[str, str] = {}
    zero_width = 0
    flooded: dict[str, int] = {}
    flooded_names: dict[str, str] = {}

    for url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] フィード取得失敗: {url} ({e})")
            continue
        source = _clean(parsed.feed.get("title", url), 60)
        entries = parsed.entries[:40]
        dated = any(_entry_epoch(e) is not None for e in entries)
        if dated:
            # 日付を持つエントリだけを対象にする。同じフィード内で日付が
            # 取れない個別記事は鮮度を保証できないため除外する
            fresh = [e for e in entries
                    if (ep := _entry_epoch(e)) is not None and 0 <= now - ep <= WINDOW_SEC]
        else:
            fresh = entries[:PER_FEED_FALLBACK]

        # 洪水検知: このフィード内で同一出典が FLOOD_THRESHOLD 件以上ならその出典を全件除外
        feed_pub_counts: dict[str, int] = {}
        for e in fresh:
            k = _publisher_key(_publisher(e, _clean(e.get("title", ""), 400)))
            if k:
                feed_pub_counts[k] = feed_pub_counts.get(k, 0) + 1
        flood_keys = {k for k, n in feed_pub_counts.items() if n >= FLOOD_THRESHOLD}

        for e in fresh:
            title = _clean(e.get("title", ""), 120)
            summary = _clean(e.get("summary", e.get("description", "")))
            blob = f"{title} {summary}".lower()
            if kws and not any(k in blob for k in kws):
                continue
            pub = _publisher(e, _clean(e.get("title", ""), 400))
            pkey = _publisher_key(pub)
            if pkey in flood_keys:
                flooded[pkey] = flooded.get(pkey, 0) + 1
                flooded_names.setdefault(pkey, pub)
                continue
            if _starts_with_zero_width(title):
                zero_width += 1
                continue
            if _has_blocked_term(title):
                blocked += 1
                continue
            key = _norm_title(title)
            if not title or key in seen:
                continue
            seen.add(key)
            if pkey and pub_counts.get(pkey, 0) >= MAX_PER_PUBLISHER:
                capped[pkey] = capped.get(pkey, 0) + 1
                capped_names.setdefault(pkey, pub)
                continue
            if pkey:
                pub_counts[pkey] = pub_counts.get(pkey, 0) + 1
            items.append({"title": title, "summary": summary,
                          "source": source, "link": e.get("link", "")})

    if blocked or capped or flooded or zero_width:
        top = sorted(capped.items(), key=lambda kv: -kv[1])[:3]
        detail = ", ".join(f"{capped_names[k]}: {n}" for k, n in top)
        msg = (f"[ok] 除外: 成人向け・出会い系の語 {blocked}件 / "
               f"出典上限超過 {sum(capped.values())}件" + (f"（出典名: {detail}）" if detail else ""))
        if flooded:
            ftop = sorted(flooded.items(), key=lambda kv: -kv[1])[:3]
            fdetail = ", ".join(f"{flooded_names[k]}: {n}" for k, n in ftop)
            msg += f" / 洪水検知 {sum(flooded.values())}件（出典名: {fdetail}）"
        if zero_width:
            msg += f" / 先頭ゼロ幅 {zero_width}件"
        print(msg)
    print(f"[ok] ニュース {len(items)} 件収集")
    return items[:MAX_ITEMS]


if __name__ == "__main__":
    import yaml

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    for i, it in enumerate(collect(cfg["news_feeds"], cfg["keyword_filter"]), 1):
        print(f"{i:2d}. [{it['source']}] {it['title']}")
