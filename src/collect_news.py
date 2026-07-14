"""RSSフィードから直近のAIニュースを集める。

- 直近26時間の記事を対象（日付が取れないフィードは先頭数件を採用）
- keyword_filter のいずれかをタイトル/概要に含むものだけ残す
- タイトルの重複を除去して最大24件返す
"""
from __future__ import annotations

import calendar
import re
import time
from typing import Any

import feedparser

WINDOW_SEC = 26 * 3600
MAX_ITEMS = 24
PER_FEED_FALLBACK = 6

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


def _norm_title(title: str) -> str:
    return re.sub(r"[\s　【】\[\]（）()「」|｜:：\-–—・、。!！?？]", "", title).lower()


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

    for url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] フィード取得失敗: {url} ({e})")
            continue
        source = _clean(parsed.feed.get("title", url), 60)
        fresh, dated = [], False
        for e in parsed.entries[:40]:
            ep = _entry_epoch(e)
            if ep is not None:
                dated = True
                if now - ep > WINDOW_SEC:
                    continue
            fresh.append(e)
        if not dated:
            fresh = parsed.entries[:PER_FEED_FALLBACK]

        for e in fresh:
            title = _clean(e.get("title", ""), 120)
            summary = _clean(e.get("summary", e.get("description", "")))
            blob = f"{title} {summary}".lower()
            if kws and not any(k in blob for k in kws):
                continue
            key = _norm_title(title)
            if not title or key in seen:
                continue
            seen.add(key)
            items.append({"title": title, "summary": summary,
                          "source": source, "link": e.get("link", "")})

    print(f"[ok] ニュース {len(items)} 件収集")
    return items[:MAX_ITEMS]


if __name__ == "__main__":
    import yaml

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    for i, it in enumerate(collect(cfg["news_feeds"], cfg["keyword_filter"]), 1):
        print(f"{i:2d}. [{it['source']}] {it['title']}")
