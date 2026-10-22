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


_SOURCE_SUFFIX_RE = re.compile(r"[\s　]*[-–—][\s　]*[^-–—]{1,30}$")


def _norm_title(title: str) -> str:
    """比較用にタイトルを正規化する。

    Google Newsなどでは同一記事が末尾の「 - 情報源名」だけ違う形で
    複数エントリになることがあるため、正規化前に末尾の情報源表記を除く。
    """
    title = _SOURCE_SUFFIX_RE.sub("", title)
    return re.sub(r"[\s　【】\[\]（）()「」|｜:：\-–—・、。!！?？]", "", title).lower()


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


_BLOCKED_FOLDED = tuple(_fold(t) for t in _BLOCKED_TITLE_TERMS)
_SOURCE_PREFIX_RE = re.compile(r"^[\s　]*[-–—][\s　]*")
# 出典名の推定に使う「 - 出典名」。区切りの前後に空白が要る（「GPT-5.5」等の語中のハイフンを出典扱いしない）
_PUBLISHER_SUFFIX_RE = re.compile(r"[\s　]+[-–—][\s　]+[^-–—]{1,30}$")


def _has_blocked_term(title: str) -> bool:
    folded = _fold(title)
    return any(t in folded for t in _BLOCKED_FOLDED)


def _publisher(entry: Any, title: str) -> str:
    """出典名を返す。entry.source.title → タイトル末尾の「 - 出典名」の順。特定できなければ空文字。

    空文字（出典を特定できない）の記事は、出典上限の対象にしない。ITmedia・Publickey・
    TechCrunch のような単独出典のフィードは、フィード名で数えると1日3件に絞られてしまう
    （絞りたいのは、1つのフィードの中に偽の出典が大量に混ざる集約フィードだけ）。
    """
    src = entry.get("source")
    name = src.get("title") if isinstance(src, dict) else None
    if not (name and str(name).strip()):
        m = _PUBLISHER_SUFFIX_RE.search(title)
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

        for e in fresh:
            title = _clean(e.get("title", ""), 120)
            summary = _clean(e.get("summary", e.get("description", "")))
            blob = f"{title} {summary}".lower()
            if kws and not any(k in blob for k in kws):
                continue
            if _has_blocked_term(title):
                blocked += 1
                continue
            key = _norm_title(title)
            if not title or key in seen:
                continue
            seen.add(key)
            pub = _publisher(e, _clean(e.get("title", ""), 400))
            pkey = _publisher_key(pub)
            if pkey and pub_counts.get(pkey, 0) >= MAX_PER_PUBLISHER:
                capped[pkey] = capped.get(pkey, 0) + 1
                capped_names.setdefault(pkey, pub)
                continue
            if pkey:
                pub_counts[pkey] = pub_counts.get(pkey, 0) + 1
            items.append({"title": title, "summary": summary,
                          "source": source, "link": e.get("link", "")})

    if blocked or capped:
        top = sorted(capped.items(), key=lambda kv: -kv[1])[:3]
        detail = ", ".join(f"{capped_names[k]}: {n}" for k, n in top)
        print(f"[ok] 除外: 成人向け・出会い系の語 {blocked}件 / "
              f"出典上限超過 {sum(capped.values())}件" + (f"（出典名: {detail}）" if detail else ""))
    print(f"[ok] ニュース {len(items)} 件収集")
    return items[:MAX_ITEMS]


if __name__ == "__main__":
    import yaml

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    for i, it in enumerate(collect(cfg["news_feeds"], cfg["keyword_filter"]), 1):
        print(f"{i:2d}. [{it['source']}] {it['title']}")
