"""毎朝の放送を1本作る: ニュース収集 → 台本生成 → 音声収録 → 配信サイト更新。

GitHub Actions から `python -m src.run_daily` で実行される。
ローカルで試す時は、先にエンジンを起動してから同じコマンドでOK。

必要な環境変数:
  ANTHROPIC_API_KEY … 台本生成用
  SITE_BASE_URL     … 配信URL (例: https://<user>.github.io/<repo>)
                      未設定ならローカルテスト用のダミーURLを使う

試し放送（公開しない検証実行）向けの任意の環境変数:
  RADIO_SCRIPT_MODEL   … 台本生成モデルをconfig.yamlのscript.modelより優先して上書き
  RADIO_DISABLE_UPLOADS… "1"ならGoogle Drive・YouTubeへのアップロードを強制的に止める
  RADIO_DUMP_SCRIPT    … "1"なら生成した台本一式をout/script.jsonへ書き出す
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from .build_audio import _truncate_log, build, export_mp3
from .collect_news import collect, filter_recent
from .make_feed import (
    JST,
    build_episode_description,
    build_episode_title,
    update_site,
    load_character_additions,
    load_recent_glossary_terms,
    load_recent_news_titles,
    reading_fix_items,
    recurring_reading_candidates,
    record_character_additions,
    record_reading_fixes,
    record_used_news,
    write_latest_json,
)
from .upload_drive import upload_to_drive
from .upload_youtube import upload_to_youtube
from .write_script import todays_banter_topic, write_script

ROOT = Path(__file__).resolve().parent.parent


def _today_mp3_name(site: Path) -> str | None:
    """本日分の放送ファイルが site/episodes/ に既にあれば、そのファイル名を返す。無ければNone。

    date_key の求め方は make_feed.update_site() と同一（JSTで %Y%m%d）にすること。
    ここでは make_feed.JST をそのままインポートして使い、定義のズレを防いでいる。
    """
    date_key = datetime.now(JST).strftime("%Y%m%d")
    mp3_name = f"radio-{date_key}.mp3"
    return mp3_name if (site / "episodes" / mp3_name).exists() else None


def _apply_script_model_override(cfg: dict) -> None:
    """環境変数 RADIO_SCRIPT_MODEL が設定されていれば script.model を上書きする。

    試し放送（台本生成モデルを変えた検証）用。未設定なら何もしない＝本番の挙動は不変。
    ここでcfg["script"]["model"]自体を書き換えるため、write_script()への台本生成、
    glossaryの話題安全性チェック、reading_check_modelのフォールバック
    （cfg["script"]["model"]を直接参照している）にも一律で反映される。
    """
    override = os.environ.get("RADIO_SCRIPT_MODEL")
    if override:
        cfg["script"]["model"] = override


def _uploads_disabled() -> bool:
    """環境変数 RADIO_DISABLE_UPLOADS=1 なら、Google Drive・YouTubeへのアップロードを
    config.yamlの設定に関わらず止める（試し放送用の保険。通常はSecrets自体を渡さない
    運用で止めるが、二重の安全策としてこのフラグも用意する）。
    """
    return os.environ.get("RADIO_DISABLE_UPLOADS") == "1"


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


def _validated_publish_from(show_cfg: dict) -> str:
    """show.publish_from を検証して返す。空・未設定なら ""（＝制限なし）。

    YYYYMMDD（8桁の半角数字で、実在する日付）ならそのまま返す。それ以外
    （例: "2026-09-01"）は ValueError にして放送を始める前に止める（fail-fast）。
    履歴の絞り込みは日付の文字列比較で行うため、形式が違うと比較が狂い、
    非公開の試験運用期間（2026年8月）の放送履歴が台本生成AIへ渡ってしまうため。
    """
    raw = show_cfg.get("publish_from", "")
    if not raw:
        return ""
    value = str(raw)
    if re.fullmatch(r"[0-9]{8}", value):
        try:
            datetime.strptime(value, "%Y%m%d")
            return value
        except ValueError:
            pass
    raise ValueError("show.publish_from は YYYYMMDD 形式で指定してください"
                     f"（例: 20260901）。現在値: {raw!r}")


def _warn_continue(what: str, e: Exception) -> None:
    """記録まわりの処理の失敗を、放送を止めずに警告として残す（例外は型名のみ。セリフを含みうるため）。"""
    print(f"[warn] {what}（放送は続行）: {type(e).__name__}")


def _build_episode_record(script: dict, tts_record: dict, now: datetime, audio: object,
                          script_model: str, reading_check_model: str) -> dict:
    """out/episode_record.json の中身を組み立てる（台本の原文と、実際にエンジンへ渡した文・読みを並べる）。"""
    tts_by_index = {t["index"]: t for t in tts_record.get("tts_lines") or []}
    lines = []
    for i, ln in enumerate(script.get("lines") or [], 1):
        t = tts_by_index.get(i, {})
        lines.append({"index": i, "speaker": ln.get("speaker", ""), "section": ln.get("section", ""),
                      "text": ln.get("text", ""), "tts_text": t.get("tts_text", ""),
                      "reading": t.get("reading", "")})
    report = tts_record.get("reading_report")
    if isinstance(report, dict):
        # 差し替え後の全文は lines[].tts_text にあるので、ここには重ねて持たない
        report = {k: v for k, v in report.items() if k != "final_texts"}
    return {
        "date": now.strftime("%Y%m%d"),
        "script_model": script_model,
        "reading_check_model": reading_check_model,
        "title": script.get("title", ""),
        "glossary_term": script.get("glossary_term", ""),
        "covered_news_indices": script.get("covered_news_indices") or [],
        "banter_topic": todays_banter_topic(now)["topic"],
        "duration_sec": round(len(audio) / 1000, 1),
        "lines": lines,
        "reading_report": report,
        "synth_failed": tts_record.get("synth_failed", 0),
    }


def _write_step_summary(info: dict) -> None:
    """GITHUB_STEP_SUMMARY があれば、放送の要約をMarkdownで追記する。セリフ全文は出さない。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    out = [f"## 放送サマリー {info['date']}", ""]
    out.append(f"- 収録尺: {info['duration_sec'] / 60:.1f}分")
    out.append(f"- セリフ: {info['n_lines']}行 / {info['n_chars']}文字")
    out.append(f"- 台本モデル: {info['script_model']} / 読み検証モデル: {info['reading_check_model']}")
    out.append(f"- お題: {info['banter_topic']}")
    report = info.get("reading_report")
    if isinstance(report, dict):
        out.append(f"- 読み検証: 規則で修正{report.get('rule_applied', 0)}件 / "
                   f"AIの指摘{report.get('pointed', 0)}件 / 修正{report.get('applied', 0)}件 / "
                   f"検証不能{report.get('unverified', 0)}件 / 範囲不審{report.get('rejected_scope', 0)}件 / "
                   f"修正できず{report.get('unfixable', 0)}件")
        fixes = [i for i in reading_fix_items(report) if i["outcome"] in ("applied", "rule_applied")]
        if fixes:
            out += ["", "### 書き換えた語", ""]
            for i in fixes:
                who = "規則" if i["kind"] == "rule" else "AI"
                out.append(f"- 「{_truncate_log(i['surface'])}」{_truncate_log(i['heard'])}"
                           f"→{_truncate_log(i['correct'])}（{who}）")
    else:
        out.append("- 読み検証: 実施なし（または失敗して通常合成）")
    cands = info.get("candidates") or []
    if cands:
        out += ["", "### 辞書・規則の候補", ""]
        for c in cands:
            out.append(f"- 「{_truncate_log(c['surface'])}」→{_truncate_log(c['correct'])}"
                       f"（{c['days']}日: 修正{c['applied_days']} / 見送り{c['other_days']}）")
    out.append("")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main() -> None:
    site = ROOT / "site"
    if _should_skip_scheduled_run(site):
        mp3_name = _today_mp3_name(site)
        print(f"[ok] 本日分は配信済みのためスキップします ({mp3_name})")
        return

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    _apply_script_model_override(cfg)
    show_cfg = cfg["show"]
    # 参照可能な放送履歴は公開日以降のみ（非公開の試験運用期間へ言及させないため）。
    # 形式が不正なら、ここ（ニュース収集の前）で止める
    publish_from = _validated_publish_from(show_cfg)
    base_url = os.environ.get("SITE_BASE_URL", "http://localhost:8000").rstrip("/")

    print("=== 1/4 ニュース収集 ===")
    news = collect(cfg["news_feeds"], cfg.get("keyword_filter", []))
    news_days = int(cfg["script"].get("news_reuse_avoid_days", 14))
    recent_news = load_recent_news_titles(ROOT / "site", days=news_days,
                                          since=publish_from)
    news = filter_recent(news, recent_news)

    print("=== 2/4 台本生成 ===")
    script_cfg = dict(cfg["script"])
    script_cfg["chars_per_minute"] = cfg["script"].get("chars_per_minute", 320)
    # 0（または未設定）= 日数の窓を使わず、公開開始日(publish_from)以降の全期間と
    # 突き合わせる。正の整数ならその日数以内だけ
    glossary_days = int(cfg["script"].get("glossary_reuse_avoid_days", 0) or 0)
    recent_terms = load_recent_glossary_terms(ROOT / "site", days=glossary_days or None,
                                              since=publish_from)
    # 台本生成AIへ渡す放送履歴の範囲を毎回ログに残す。非公開の試験運用期間へ
    # 言及してしまった2026-09-03の事故を再発させないための日々の証跡
    # （write_script側の再フィルタは、ここで既に除外済みだと無言で通るため）
    terms_scope = f"直近{glossary_days}日" if glossary_days > 0 else "全期間"
    print(f"[ok] 参照する放送履歴: 公開開始日={publish_from or '(制限なし)'} / "
          f"ニュース{len(recent_news)}件・用語{len(recent_terms)}件({terms_scope})")
    # キャラ設定シートの追加分（これまでの放送で新しく出た設定）。今日の日付の分は除いて読む
    # （同日の再実行は号を上書きするので、上書きされる前の放送の設定を確定扱いしない）
    character_additions = load_character_additions(
        ROOT / "site", exclude_date=datetime.now(JST).strftime("%Y%m%d"))
    print(f"[ok] キャラ設定シートの追加分: {len(character_additions)}件")
    script = write_script(news, script_cfg, minutes=int(show_cfg["minutes"]),
                          recent_terms=recent_terms, recent_news=recent_news,
                          show_cfg=show_cfg, character_additions=character_additions)

    if os.environ.get("RADIO_DUMP_SCRIPT") == "1":
        # 試し放送でモデルの出力を見比べられるよう、生成した台本一式を書き出す
        # （本番では使わない。site/や配信物には含めず out/ のみに置く）
        (ROOT / "out").mkdir(parents=True, exist_ok=True)
        (ROOT / "out" / "script.json").write_text(
            json.dumps({"script_model": script_cfg["model"], **script},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    print("=== 3/4 収録 ===")
    # 読み検証だけ別モデルを使いたい場合はconfig.yamlに script.reading_check_model を
    # 足す（未設定なら従来どおり台本生成と同じモデル）。config.yaml自体は変更しない
    # （番組の中身に関わる設定はケイスケの承認が必要なため、今回はフォールバックのみ実装）
    reading_check_model = cfg["script"].get("reading_check_model") or cfg["script"]["model"]
    tts_record: dict = {}   # 収録の記録（build が埋める。失敗しても放送には影響しない）
    audio = build(script["lines"], cfg["tts"], reading_check_model=reading_check_model,
                  bgm_cfg=cfg.get("bgm", {}), record=tts_record)
    out_mp3 = ROOT / "out" / "today.mp3"
    export_mp3(audio, out_mp3)

    # 放送ごとの記録（台本つき）。out/ は配信されない。ワークフローがartifactとして14日だけ保存する
    summary_info: dict = {}
    try:
        record = _build_episode_record(script, tts_record, datetime.now(JST), audio,
                                       script_cfg["model"], reading_check_model)
        (ROOT / "out").mkdir(parents=True, exist_ok=True)
        (ROOT / "out" / "episode_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        summary_info = {
            "date": record["date"], "duration_sec": record["duration_sec"],
            "n_lines": len(record["lines"]),
            "n_chars": sum(len(ln["text"]) for ln in record["lines"]),
            "script_model": record["script_model"],
            "reading_check_model": record["reading_check_model"],
            "banter_topic": record["banter_topic"],
            "reading_report": tts_record.get("reading_report"),
        }
    except Exception as e:  # noqa: BLE001
        _warn_continue("放送記録の書き出しに失敗", e)

    now = datetime.now(JST)
    covered_indices = script.get("covered_news_indices") or []
    used_news = [news[i - 1] for i in covered_indices if 1 <= i <= len(news)]
    if not used_news:
        used_news = news[: cfg["script"].get("max_news", 4)]
    picked = [n["title"] for n in used_news]
    # 配信タイトルはAIが出力するscript['title']に依存せず、放送日から機械的に組み立てる
    # （AIが日によって表記を変えたり日付を誤記したりする揺れを根絶するため）
    episode_title = build_episode_title(
        show_cfg.get("episode_title_prefix") or show_cfg["title"], now)
    episode_description = build_episode_description(picked, show_cfg["description"])

    drive_cfg = cfg.get("drive", {})
    if not _uploads_disabled() and drive_cfg.get("upload_enabled") and drive_cfg.get("folder_id"):
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
    # 今回の放送で新しく出た設定を、シートの追加分としてサイトに記録する（翌日以降の
    # 台本生成と照合に使う。公開サイトの一部として次回以降の実行にも引き継がれる）
    new_character_facts = script.get("character_additions") or []
    record_character_additions(ROOT / "site", now.strftime("%Y%m%d"), new_character_facts)
    for f in new_character_facts:
        print(f"[ok] キャラ設定シートに追加: {f.get('fact', '')}")
    if not new_character_facts:
        print("[ok] キャラ設定シートに追加: なし")

    # 読みの修正履歴（毎日同じ語を直している/直せていないことに気づくため。公開サイトの一部として
    # 翌日以降へ引き継がれる）。読み検証をしなかった日は記録しない
    try:
        if isinstance(tts_record.get("reading_report"), dict):
            record_reading_fixes(ROOT / "site", now.strftime("%Y%m%d"),
                                 reading_fix_items(tts_record["reading_report"]))
    except Exception as e:  # noqa: BLE001
        _warn_continue("読みの修正履歴の記録に失敗", e)
    try:
        candidates = recurring_reading_candidates(ROOT / "site", now.strftime("%Y%m%d"))
        summary_info["candidates"] = candidates
        for c in candidates:
            print(f"[hint] 辞書・規則の候補: 「{_truncate_log(c['surface'])}」→"
                  f"{_truncate_log(c['correct'])}（{c['days']}日: 修正{c['applied_days']} / "
                  f"見送り{c['other_days']}）")
    except Exception as e:  # noqa: BLE001
        _warn_continue("読みの候補の集計に失敗", e)

    # 外部の自動化（X告知投稿の自動生成等）が「今日の放送内容」を確実に読み取れる
    # よう、site/latest.json を書く。YouTubeのURLはまだ分からないのでNoneのまま
    # 先に書き、アップロードが成功したら下で上書きする（失敗しても放送は成立する）
    date_key = now.strftime("%Y%m%d")
    write_latest_json(
        ROOT / "site", date_key=date_key, title=episode_title, news=picked,
        glossary_term=script.get("glossary_term", ""),
        episode_url=f"{base_url}/episodes/radio-{date_key}.mp3",
        program_page=f"{base_url}/", apple_podcasts_url=show_cfg.get("apple_podcasts_url", ""),
    )

    # YouTubeは従。動画の変換とアップロードに数分かかるため、主軸である
    # ポッドキャスト配信(update_site)を先に終わらせてから実行する
    youtube_cfg = cfg.get("youtube", {})
    if not _uploads_disabled() and youtube_cfg.get("enabled"):
        # YouTube側の説明欄にも音声モデルのクレジット表示（利用規約で必須）とフィードURLを添える
        yt_description = f"{episode_description}\n\n{show_cfg.get('credit', '')}\n\n{base_url}/feed.xml"
        video_id = upload_to_youtube(out_mp3, episode_title, yt_description, cfg)
        if video_id:
            print(f"[ok] YouTubeアップロード完了: https://youtu.be/{video_id}")
            write_latest_json(
                ROOT / "site", date_key=date_key, title=episode_title, news=picked,
                glossary_term=script.get("glossary_term", ""),
                episode_url=f"{base_url}/episodes/radio-{date_key}.mp3",
                program_page=f"{base_url}/", apple_podcasts_url=show_cfg.get("apple_podcasts_url", ""),
                youtube_url=f"https://youtu.be/{video_id}",
            )
        else:
            print("[skip] YouTubeアップロードはスキップ、または失敗しました（放送生成は継続）")

    try:
        if summary_info.get("date"):
            _write_step_summary(summary_info)
    except Exception as e:  # noqa: BLE001
        _warn_continue("実行サマリーの出力に失敗", e)

    print("=== 放送完了！いってらっしゃい ===")


if __name__ == "__main__":
    main()
