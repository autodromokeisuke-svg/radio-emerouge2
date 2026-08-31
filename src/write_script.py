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
_MAX_ATTEMPTS = 3

# 一般公開初日（config.yaml の show.debut_date）だけプロンプトに差し込む、
# 番組紹介パート向けの指示ブロック。通常日は write_script() が空文字を渡すので出力されない。
_DEBUT_INTRO_BLOCK = """## 初回放送の案内（本日限定）
今日はこの番組の記念すべき初回放送日。オープニング（挨拶・日付）の直後、最初のニュースに入る前に、
エメとルジェの自然な掛け合いで次の5点を短く紹介すること（目安2〜3分程度。尺を圧迫しすぎないこと）。
1. エメとルジェの自己紹介（名前と、ふたりの掛け合いの雰囲気が伝わるように）
2. 番組の趣旨（毎朝、AI・テクノロジーのニュースを20分ほどで届ける番組であること）
3. 毎朝配信していること
4. 「今日のひとこと」など定番コーナーがあることの紹介
5. ポッドキャスト（Apple Podcasts）とYouTubeの両方で聴けること
今日が記念すべき初回放送であることが伝わるようにすること。
リスナーにとって今日が初めての放送なので、「以前もお話ししましたが」「前回も少し触れましたが」
のような、過去の放送を前提にした前置きはこのパートにも他のパートにも入れないこと。
「自分専用で試験運用していた」といった過去の経緯には一切触れないこと。
自己紹介では、自分が「AIアシスタントである」ことや使用しているAIモデル名（ChatGPT・Claudeなど）には
一切触れず、あくまで番組パーソナリティとしての名前・キャラクターだけを紹介すること。
このパート以外のニュース本数や他のコーナー構成は通常どおり維持すること。
"""


def _today_label() -> str:
    now = datetime.now(JST)
    return f"{now.year}年{now.month}月{now.day}日 {_WEEKDAYS[now.weekday()]}曜日"


def _debut_block(show_cfg: dict[str, Any] | None) -> str:
    """今日が show_cfg['debut_date']（%Y%m%d, JST基準）と一致する日だけ指示ブロックを返す。

    debut_date が未設定・空文字なら常に空文字（＝通常運用）。
    """
    if not show_cfg:
        return ""
    debut_date = str(show_cfg.get("debut_date") or "").strip()
    if not debut_date:
        return ""
    today_key = datetime.now(JST).strftime("%Y%m%d")
    return _DEBUT_INTRO_BLOCK if today_key == debut_date else ""


def _tomorrow_label() -> str:
    """次回放送日（＝翌日）の曜日を日本語表記で返す（例: 土曜日）。

    この番組は毎日放送のため、次回放送は常に「実行日（＝今日の放送日）の翌日」。
    AIに曜日計算をさせると「金曜の次は週明け」のような誤りをするため、
    コード側で計算してプロンプトに渡す。
    """
    tomorrow = datetime.now(JST) + timedelta(days=1)
    return f"{_WEEKDAYS[tomorrow.weekday()]}曜日"


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


# パーソナリティ（エメ・ルジェ）が自分の正体（使用AIモデル・「中の人」）に一人称で言及していないかの検出。
# 近接パターンで検出しつつ、「ChatGPTの新機能が発表された」のような第三者的なニュース紹介は
# 誤検知しないよう、一人称のすぐそばに自己申告的な言い回し（コピュラ等）が続く場合のみ拾う。
_PRONOUN = r"(?:私たち|僕ら|わたしたち|あたしたち|自分たち|私|僕|わたし|ぼく|あたし|自分)"
_MODEL_NAME = r"(?:ChatGPT|chatgpt|GPT|Claude|クロード|チャットジーピーティー|チャットGPT|Gemini|ジェミニ|Copilot|コパイロット)"
_AI_TERM = r"(?:AIアシスタント|言語モデル|LLM|えるえるえむ)"
_SEP = r"[、,\s]*"
_SELF_CUE = r"(?:です|だ|なんです|なんだ|なんだよね|なの|なので|ベース)"

_IDENTITY_LEAK_PATTERNS = [
    re.compile(r"中の人"),
    # 一人称＋は/も/って＋（実は等）＋モデル名＋自己申告的な語尾（例:「私はChatGPTなので」）
    re.compile(rf"{_PRONOUN}(?:たち|ら)?{_SEP}(?:は|も|って){_SEP}(?:実は|本当は|じつは)?{_SEP}"
               rf"{_MODEL_NAME}[-\d.]*(?![のをにへとやも])[^。、]{{0,6}}?{_SELF_CUE}"),
    # 一人称、実は（本当は/じつは）＋モデル名＋自己申告的な語尾
    # （例:「僕たち、実はClaudeがベースなんだ」）
    # モデル名の直後に「の話」「を使う」等が続く場合は、自分の正体ではなく
    # 話題としてモデルに触れているだけなので検出しない
    # モデル名の直後に「の」「を」等の助詞が来る場合は、自分の正体ではなく
    # 話題としてモデルに触れているだけ（例:「実はClaudeの使い方を勉強中」）なので除外する
    re.compile(rf"{_PRONOUN}(?:たち|ら)?{_SEP}(?:実は|本当は|じつは){_SEP}"
               rf"{_MODEL_NAME}[-\d.]*(?![のをにへとやも]){_SEP}(?:が|は)?{_SEP}"
               rf"(?:ベース|そのもの)?{_SEP}{_SELF_CUE}"),
    # 一人称＋は/も＋AI・AIアシスタント・言語モデル＋です/だ等（例:「私たちはAIなんだ」）
    re.compile(rf"{_PRONOUN}(?:たち|ら)?{_SEP}(?:は|も){_SEP}(?:AI|{_AI_TERM}){_SEP}"
               rf"(?:です|だ|なんです|なんだ|なの)"),
]


def _find_identity_leak_lines(lines: list[dict[str, str]]) -> list[str]:
    """自分の正体（AIモデル・中の人）への言及と疑われるセリフ本文の一覧を返す（無ければ空）。"""
    hits = []
    for ln in lines:
        text = ln.get("text", "")
        if not text:
            continue
        if any(pat.search(text) for pat in _IDENTITY_LEAK_PATTERNS):
            hits.append(text)
    return hits


def _check_glossary_term(data: dict[str, Any], recent_terms: list[dict[str, str]],
                         news: list[dict[str, str]]) -> list[str]:
    """「今日のひとこと用語」の妥当性を確認し、問題があれば説明文のリストを返す（例外は投げない）。"""
    term = (data.get("glossary_term") or "").strip()
    if not term:
        return []
    problems = []
    haystack = " ".join(f"{n.get('title', '')} {n.get('summary', '')}" for n in news).lower()
    if term.lower() not in haystack:
        problems.append(f"「{term}」が今日のニュース候補の中に見当たりません")
    recent_norm = {t.get("term", "").strip().lower() for t in recent_terms}
    if term.lower() in recent_norm:
        problems.append(f"「{term}」は直近使用済みです")
    return problems


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    lines = data.get("lines", [])
    if not isinstance(lines, list) or len(lines) < 8:
        raise ValueError("セリフが少なすぎる")
    clean = []
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        sp, tx = ln.get("speaker"), (ln.get("text") or "").strip()
        if sp not in ("eme", "ruje") or not tx:
            continue
        clean.append({"speaker": sp, "text": tx})
    if len(clean) < 8:
        raise ValueError("有効なセリフが少なすぎる")
    identity_hits = _find_identity_leak_lines(clean)
    if identity_hits:
        raise ValueError(
            "以下のセリフで、パーソナリティが自分の正体（使用AIモデル・「中の人」）に言及しています: "
            + " / ".join(f"「{h}」" for h in identity_hits[:3])
            + "。エメとルジェは番組パーソナリティとしてのみ振る舞い、"
              "自分自身をChatGPT・Claude・GPT・Gemini等のAIモデル名や「AIアシスタント」「言語モデル」"
              "「中の人」と結びつける発言を一切せずに書き直してください"
              "（一人称でモデル名を自称する表現や、正体を匂わせる遠回しな表現も禁止）。"
              "なお、ChatGPTやClaude、OpenAI・Anthropicなどをニュースの話題として"
              "第三者的に紹介するのは問題ありません。"
        )
    glossary_term = data.get("glossary_term")
    if not isinstance(glossary_term, str):
        glossary_term = ""
    covered_news_indices = data.get("covered_news_indices")
    if not isinstance(covered_news_indices, list):
        covered_news_indices = []
    covered_news_indices = [i for i in covered_news_indices if isinstance(i, int) and not isinstance(i, bool)]
    return {"title": (data.get("title") or "RADIOえめるーじぇ").strip(),
            "glossary_term": glossary_term.strip(),
            "covered_news_indices": covered_news_indices,
            "lines": clean}


def write_script(news: list[dict[str, str]], script_cfg: dict[str, Any],
                 minutes: int, recent_terms: list[dict[str, str]] | None = None,
                 recent_news: list[dict[str, str]] | None = None,
                 show_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    target_chars = minutes * int(script_cfg.get("chars_per_minute", 320))
    prompt = PROMPT_PATH.read_text(encoding="utf-8").format(
        today=_today_label(),
        tomorrow_label=_tomorrow_label(),
        minutes=minutes,
        target_chars=target_chars,
        max_news=script_cfg.get("max_news", 4),
        news_block=_news_block(news),
        recent_terms_block=_format_recent_terms_block(recent_terms or []),
        recent_news_block=_format_recent_news_block(recent_news or []),
        news_reuse_avoid_days=script_cfg.get("news_reuse_avoid_days", 7),
        debut_block=_debut_block(show_cfg),
    )
    client = Anthropic()
    messages = [{"role": "user", "content": prompt}]
    last_err: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        resp = client.messages.create(
            model=script_cfg["model"],
            max_tokens=16000,
            system=SYSTEM,
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = _validate(_extract_json(text))
            problems = _check_glossary_term(data, recent_terms or [], news)
            if problems and attempt < _MAX_ATTEMPTS - 1:
                raise ValueError("; ".join(problems) + "。別の用語を選び直してください。")
            if problems:
                print(f"[warn] 今日のひとこと用語に懸念あり(最終試行のため受容): {'; '.join(problems)}")
            total = sum(len(ln["text"]) for ln in data["lines"])
            print(f"[ok] 台本生成: {data['title']} / {len(data['lines'])}セリフ "
                  f"/ 約{total}文字 (目標{target_chars})")
            return data
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            print(f"[warn] 台本の検証に失敗 (試行{attempt + 1}): {e}")
            if isinstance(e, json.JSONDecodeError):
                snippet = text[max(0, e.pos - 80):e.pos + 80]
                print(f"[debug] 失敗箇所付近: ...{snippet}...")
            debug_path = Path(__file__).resolve().parent.parent / "out" / f"last_script_error_{attempt + 1}.txt"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(text, encoding="utf-8")
            print(f"[debug] 生テキストを保存: {debug_path}")
            retry_hint = (str(e) if isinstance(e, ValueError) and not isinstance(e, json.JSONDecodeError)
                         else "出力が指定のJSON形式ではありません。指定のJSONのみを出力し直してください。")
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": retry_hint},
            ]
    raise RuntimeError(f"台本生成に失敗: {last_err}")
