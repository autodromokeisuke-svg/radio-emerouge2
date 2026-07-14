"""Claude API で エメ×ルジェ の掛け合い台本を生成する。

環境変数 ANTHROPIC_API_KEY が必要（GitHub Actions では Secrets から注入）。
出力: {"title": str, "lines": [{"speaker": "eme"|"ruje", "text": str}, ...]}
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

JST = timezone(timedelta(hours=9))
PROMPT_PATH = Path(__file__).resolve().parent.parent / "assets" / "prompt_script.md"

SYSTEM = (
    "あなたは日本語ラジオ番組の放送作家です。"
    "指定されたJSON形式のみを出力し、それ以外の文字を一切出力しません。"
)

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


def _today_label() -> str:
    now = datetime.now(JST)
    return f"{now.year}年{now.month}月{now.day}日 {_WEEKDAYS[now.weekday()]}曜日"


def _news_block(items: list[dict[str, str]]) -> str:
    if not items:
        return ("(今日はニュース収集に失敗。ニュースの代わりに、"
                "初心者向けのAI活用小ネタを2〜3個、エメとルジェの掛け合いで紹介する回にする)")
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. [{it['source']}] {it['title']}\n   概要: {it['summary']}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSONが見つからない")
    return json.loads(text[start:end + 1])


def _format_recent_terms_block(recent_terms: list[dict[str, str]]) -> str:
    if not recent_terms:
        return "（まだ無し）"
    ordered = sorted(recent_terms, key=lambda t: t.get("date", ""), reverse=True)
    lines = []
    for t in ordered:
        date = t.get("date", "")
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        lines.append(f"- {date}: {t.get('term', '')}")
    return "\n".join(lines)


def _format_recent_news_block(recent_news: list[dict[str, str]]) -> str:
    if not recent_news:
        return "（まだ無し）"
    ordered = sorted(recent_news, key=lambda t: t.get("date", ""), reverse=True)
    lines = []
    for t in ordered:
        date = t.get("date", "")
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        lines.append(f"- {date}: {t.get('title', '')}")
    return "\n".join(lines)


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    lines = data.get("lines", [])
    if not isinstance(lines, list) or len(lines) < 8:
        raise ValueError("セリフが少なすぎる")
    clean = []
    for ln in lines:
        sp, tx = ln.get("speaker"), (ln.get("text") or "").strip()
        if sp not in ("eme", "ruje") or not tx:
            continue
        clean.append({"speaker": sp, "text": tx})
    if len(clean) < 8:
        raise ValueError("有効なセリフが少なすぎる")
    glossary_term = data.get("glossary_term")
    if not isinstance(glossary_term, str):
        glossary_term = ""
    covered_news_titles = data.get("covered_news_titles")
    if not isinstance(covered_news_titles, list):
        covered_news_titles = []
    covered_news_titles = [t for t in covered_news_titles if isinstance(t, str)]
    return {"title": (data.get("title") or "RADIOえめるーじぇ").strip(),
            "glossary_term": glossary_term.strip(),
            "covered_news_titles": covered_news_titles,
            "lines": clean}


def write_script(news: list[dict[str, str]], script_cfg: dict[str, Any],
                 minutes: int, recent_terms: list[dict[str, str]] | None = None,
                 recent_news: list[dict[str, str]] | None = None) -> dict[str, Any]:
    target_chars = minutes * int(script_cfg.get("chars_per_minute", 320))
    prompt = PROMPT_PATH.read_text(encoding="utf-8").format(
        today=_today_label(),
        minutes=minutes,
        target_chars=target_chars,
        max_news=script_cfg.get("max_news", 4),
        news_block=_news_block(news),
        recent_terms_block=_format_recent_terms_block(recent_terms or []),
        recent_news_block=_format_recent_news_block(recent_news or []),
        news_reuse_avoid_days=script_cfg.get("news_reuse_avoid_days", 7),
    )
    client = Anthropic()
    messages = [{"role": "user", "content": prompt}]
    last_err: Exception | None = None
    for attempt in range(2):
        resp = client.messages.create(
            model=script_cfg["model"],
            max_tokens=16000,
            system=SYSTEM,
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = _validate(_extract_json(text))
            total = sum(len(ln["text"]) for ln in data["lines"])
            print(f"[ok] 台本生成: {data['title']} / {len(data['lines'])}セリフ "
                  f"/ 約{total}文字 (目標{target_chars})")
            return data
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            print(f"[warn] 台本のJSON解析に失敗 (試行{attempt + 1}): {e}")
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user",
                 "content": "出力が指定のJSON形式ではありません。"
                            "指定のJSONのみを出力し直してください。"},
            ]
    raise RuntimeError(f"台本生成に失敗: {last_err}")
