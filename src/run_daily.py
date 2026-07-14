"""毎朝の放送を1本作る: ニュース収集 → 台本生成 → 音声収録 → 配信サイト更新。

GitHub Actions から `python -m src.run_daily` で実行される。
ローカルで試す時は、先にエンジンを起動してから同じコマンドでOK。

必要な環境変数:
  ANTHROPIC_API_KEY … 台本生成用
  SITE_BASE_URL     … 配信URL (例: https://<user>.github.io/<repo>)
                      未設定ならローカルテスト用のダミーURLを使う
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .build_audio import build, export_mp3
from .collect_news import collect, filter_recent
from .make_feed import (
    update_site,
    load_recent_glossary_terms,
    load_recent_news_titles,
    record_used_news,
)
from .write_script import write_script

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    show_cfg = cfg["show"]
    base_url = os.environ.get("SITE_BASE_URL", "http://localhost:8000").rstrip("/")

    print("=== 1/4 ニュース収集 ===")
    news = collect(cfg["news_feeds"], cfg.get("keyword_filter", []))
    news_days = int(cfg["script"].get("news_reuse_avoid_days", 7))
    recent_news = load_recent_news_titles(ROOT / "site", days=news_days)
    news = filter_recent(news, recent_news)

    print("=== 2/4 台本生成 ===")
    script_cfg = dict(cfg["script"])
    script_cfg["chars_per_minute"] = cfg["script"].get("chars_per_minute", 320)
    glossary_days = int(cfg["script"].get("glossary_reuse_avoid_days", 30))
    recent_terms = load_recent_glossary_terms(ROOT / "site", days=glossary_days)
    script = write_script(news, script_cfg, minutes=int(show_cfg["minutes"]),
                          recent_terms=recent_terms, recent_news=recent_news)

    print("=== 3/4 収録 ===")
    audio = build(script["lines"], cfg["tts"])
    out_mp3 = ROOT / "out" / "today.mp3"
    export_mp3(audio, out_mp3)

    print("=== 4/4 配信更新 ===")
    now = datetime.now(JST)
    covered_titles = script.get("covered_news_titles") or []
    used_news = [n for n in news if n["title"] in covered_titles]
    if not used_news:
        used_news = news[: cfg["script"].get("max_news", 4)]
    picked = [n["title"] for n in used_news]
    description = "今日の話題: " + " / ".join(picked) if picked else show_cfg["description"]
    update_site(
        site=ROOT / "site",
        mp3_src=out_mp3,
        title=f"{script['title']}（{now.month}/{now.day}）",
        description=description[:400],
        base_url=base_url,
        show_cfg=show_cfg,
        glossary_term=script.get("glossary_term", ""),
    )
    record_used_news(ROOT / "site", now.strftime("%Y%m%d"),
                     [{"title": n["title"], "link": n.get("link", "")} for n in used_news])
    print("=== 放送完了！いってらっしゃい ===")


if __name__ == "__main__":
    main()
