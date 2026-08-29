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
from datetime import datetime
from pathlib import Path

import yaml

from .build_audio import build, export_mp3
from .collect_news import collect, filter_recent
from .make_feed import (
    JST,
    update_site,
    load_recent_glossary_terms,
    load_recent_news_titles,
    record_used_news,
)
from .upload_drive import upload_to_drive
from .upload_youtube import upload_to_youtube
from .write_script import write_script

ROOT = Path(__file__).resolve().parent.parent


def _today_mp3_name(site: Path) -> str | None:
    """本日分の放送ファイルが site/episodes/ に既にあれば、そのファイル名を返す。無ければNone。

    date_key の求め方は make_feed.update_site() と同一（JSTで %Y%m%d）にすること。
    ここでは make_feed.JST をそのままインポートして使い、定義のズレを防いでいる。
    """
    date_key = datetime.now(JST).strftime("%Y%m%d")
    mp3_name = f"radio-{date_key}.mp3"
    return mp3_name if (site / "episodes" / mp3_name).exists() else None


def _should_skip_scheduled_run(site: Path) -> bool:
    """schedule起動（cron）で、かつ本日分が配信済みならTrueを返す。

    2本のcron（1本目が遅延した場合の保険として2本目を用意）が両方成功すると
    Claude APIを二重消費してしまうため、2本目以降は早期にスキップする。
    workflow_dispatch（手動実行）やローカル実行（GITHUB_EVENT_NAME未設定）では
    常にFalseを返す＝スキップしない。ケイスケが手動で今日の分を作り直したい場合があるため。
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return False
    return _today_mp3_name(site) is not None


def main() -> None:
    site = ROOT / "site"
    if _should_skip_scheduled_run(site):
        mp3_name = _today_mp3_name(site)
        print(f"[ok] 本日分は配信済みのためスキップします ({mp3_name})")
        return

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
                          recent_terms=recent_terms, recent_news=recent_news,
                          show_cfg=show_cfg)

    print("=== 3/4 収録 ===")
    audio = build(script["lines"], cfg["tts"], reading_check_model=cfg["script"]["model"])
    out_mp3 = ROOT / "out" / "today.mp3"
    export_mp3(audio, out_mp3)

    now = datetime.now(JST)
    covered_indices = script.get("covered_news_indices") or []
    used_news = [news[i - 1] for i in covered_indices if 1 <= i <= len(news)]
    if not used_news:
        used_news = news[: cfg["script"].get("max_news", 4)]
    picked = [n["title"] for n in used_news]
    episode_title = f"{script['title']}（{now.month}/{now.day}）"
    episode_description = ("今日の話題: " + " / ".join(picked) if picked
                           else show_cfg["description"])[:400]

    drive_cfg = cfg.get("drive", {})
    if drive_cfg.get("upload_enabled") and drive_cfg.get("folder_id"):
        upload_to_drive(out_mp3, drive_cfg["folder_id"])

    print("=== 4/4 配信更新 ===")
    update_site(
        site=ROOT / "site",
        mp3_src=out_mp3,
        title=episode_title,
        description=episode_description,
        base_url=base_url,
        show_cfg=show_cfg,
        glossary_term=script.get("glossary_term", ""),
    )
    record_used_news(ROOT / "site", now.strftime("%Y%m%d"),
                     [{"title": n["title"], "link": n.get("link", "")} for n in used_news])

    # YouTubeは従。動画の変換とアップロードに数分かかるため、主軸である
    # ポッドキャスト配信(update_site)を先に終わらせてから実行する
    youtube_cfg = cfg.get("youtube", {})
    if youtube_cfg.get("enabled"):
        # YouTube側の説明欄にも音声モデルのクレジット表示（利用規約で必須）とフィードURLを添える
        yt_description = f"{episode_description}\n\n{show_cfg.get('credit', '')}\n\n{base_url}/feed.xml"
        video_id = upload_to_youtube(out_mp3, episode_title, yt_description, cfg)
        if video_id:
            print(f"[ok] YouTubeアップロード完了: https://youtu.be/{video_id}")
        else:
            print("[skip] YouTubeアップロードはスキップ、または失敗しました（放送生成は継続）")

    print("=== 放送完了！いってらっしゃい ===")


if __name__ == "__main__":
    main()
