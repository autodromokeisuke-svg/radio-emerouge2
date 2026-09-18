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

from .bgm import SECTION_ORDER, plan_spans

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


# BGMが初めて入る回（config.yaml の show.bgm_intro_date）だけプロンプトに差し込む指示ブロック。
# 通常日は空文字。エメとルジェの正体（使用AI・BGMの作り手）には触れさせない。
_BGM_INTRO_BLOCK = """## BGM導入の案内（本日限定）
今日から番組にBGMが入った。オープニング（挨拶・日付）の直後、最初のニュースに入る前に、
エメとルジェの自然な掛け合いで「番組にBGMが入りました！」と軽く紹介すること（2〜4往復、
30秒程度。尺を圧迫しすぎないこと）。
- 例: エメがテンション高く気づいて嬉しそうに知らせ、ルジェが落ち着いて「言われてみれば後ろで
  流れてるね」「通勤中に聴いてもらうのにちょうどいいかも」と受ける、のようなふたりらしい温度差で
- BGMがあることで通勤・朝の時間がちょっと楽しくなる、というリスナー目線の一言を入れる
- BGMを誰が・何で作ったか、AIで作ったか等には触れない。エメとルジェは番組パーソナリティとして、
  「番組にBGMが付いた」という事実だけを喜ぶ
- 「初めて」「今日から」であることが伝わるようにする。ただし過去の放送を前提にした
  「以前は無音でしたが」のような言い方はしない（リスナーは前日までの放送を聴いているので、
  「今日から」は問題ないが、試験運用期間などの過去の経緯には触れない）
このパート以外の構成（ニュース本数・今日のひとこと・エンディング）は通常どおり維持すること。
このパートのセリフも section は opening とすること。
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


def _bgm_intro_block(show_cfg: dict[str, Any] | None) -> str:
    """今日が show_cfg['bgm_intro_date']（%Y%m%d, JST基準）と一致する日だけ指示ブロックを返す。

    未設定・空文字なら常に空文字（＝通常運用）。BGMが初めて入る回の冒頭で軽く紹介するため。
    """
    if not show_cfg:
        return ""
    intro_date = str(show_cfg.get("bgm_intro_date") or "").strip()
    if not intro_date:
        return ""
    today_key = datetime.now(JST).strftime("%Y%m%d")
    return _BGM_INTRO_BLOCK if today_key == intro_date else ""


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


def _drop_before_publish(entries: list[dict[str, str]], publish_from: str,
                        label: str) -> list[dict[str, str]]:
    """公開開始日(publish_from, YYYYMMDD)より前の放送履歴を落とす。

    ★この関数を消したり呼び出しを外したりしないこと。
    番組は2026-08-01から約1ヶ月、非公開で試験運用していた。その期間の放送は
    show.publish_from によってフィード・番組ページから隠されており、リスナーは
    存在自体を知らない。にもかかわらず放送履歴（ニュース・今日のひとこと用語）は
    日数だけで絞っていたため、非公開期間の放送内容が台本生成AIへ渡っていた。
    プロンプトは重複した話題に「これ、前にも少し触れたんだけど」と断りを入れるよう
    指示しているため、AIはその指示に忠実に従い、2026-09-03の放送で「先週も話し
    ましたが」とリスナーの知らない放送へ言及してしまった（公開3日目に「先週」は
    存在しない）。

    したがって台本生成時にAIが参照してよい放送履歴は「公開日以降」に限る。
    """
    if not publish_from:
        return list(entries)
    kept = [e for e in entries if str(e.get("date", "")) >= publish_from]
    dropped = len(entries) - len(kept)
    if dropped:
        print(f"[ok] {label}: 公開開始日({publish_from})より前の{dropped}件を"
              f"参照範囲から除外しました")
    return kept


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
# 「です」は「ですら」「ですべて」「ですごく」等の先頭にも一致してしまうため、
# 文末・読点・接続表現が続く場合だけを自己申告とみなす
_SELF_CUE = (r"(?:なんです|なんだよね|なんだ|なので|なの|ベース"
             r"|です(?![らべごか])|だ(?![けがろ]))")

_IDENTITY_LEAK_PATTERNS = [
    # 「中の人」は「企業アカウントの中の人」等ニュース内で一般語としても出るため、
    # 自分たちを指している場合（一人称・番組名が近くにある）だけを検出する
    re.compile(rf"(?:{_PRONOUN}|えめるーじぇ|エメ|ルジェ|この番組|うち)[^。]{{0,12}}中の人"),
    re.compile(rf"中の人[^。]{{0,12}}(?:{_PRONOUN}|えめるーじぇ|エメ|ルジェ)"),
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


def _resolve_used_news(data: dict[str, Any], news: list[dict[str, str]],
                       max_news: int) -> list[dict[str, str]]:
    """AIが実際に本編で扱ったと申告したニュース（covered_news_indices）を、
    候補リスト(news、最大24件)から引き当てる。

    run_daily.pyの同名ロジック（used_newsの組み立て）と必ず一致させること。
    ここでズレると、「今日のひとこと」が参照してよい範囲の判定が
    本編の実際の内容と食い違う（2026-09-17発覚の原因）。
    """
    indices = data.get("covered_news_indices") or []
    used = [news[i - 1] for i in indices if 1 <= i <= len(news)]
    if not used:
        used = news[:max_news]
    return used


def _check_glossary_term(data: dict[str, Any], recent_terms: list[dict[str, str]],
                         used_news: list[dict[str, str]]) -> list[str]:
    """「今日のひとこと用語」の妥当性を確認し、問題があれば説明文のリストを返す（例外は投げない）。

    used_news は候補全体(最大24件)ではなく、本編で実際に扱った分（通常6件）に
    限定すること。候補全体を渡すと、選ばれなかったニュースの用語まで
    「今日のひとこと」で使えてしまう（2026-09-17発覚）。
    """
    term = (data.get("glossary_term") or "").strip()
    if not term:
        return []
    problems = []
    haystack = " ".join(f"{n.get('title', '')} {n.get('summary', '')}" for n in used_news).lower()
    if term.lower() not in haystack:
        problems.append(f"「{term}」が本編で実際に扱ったニュースの中に見当たりません")
    recent_norm = {t.get("term", "").strip().lower() for t in recent_terms}
    if term.lower() in recent_norm:
        problems.append(f"「{term}」は直近使用済みです")
    return problems


def _check_sections(lines: list[dict[str, str]]) -> list[str]:
    """各セリフの section ラベル（BGMを流す区間の判定に使う）が正しいか確認する。

    opening → news → glossary → ending の順で、opening/news/ending が1行以上あること。
    不正でも放送は止めない（write_scriptの最終試行では受容し、build_audioがBGM無しで
    続行する）。リトライで直せるなら直すために、問題としては報告する。
    """
    if plan_spans([ln.get("section", "") for ln in lines]) is not None:
        return []
    return [
        "各セリフの section が不正です。全セリフに opening / news / glossary / ending の"
        "いずれかを付け、opening → news → glossary → ending の順に並べてください"
        "（opening・news・ending は必須、glossary は今日のひとこと用語があるときのみ）"
    ]


_KANJI_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_NEWS_RE = re.compile(r"([0-9０-９一二三四五六七八九十]+)\s*(?:本目|番目)")
_ZEN_TO_HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def _parse_ordinal(token: str) -> int | None:
    han = token.translate(_ZEN_TO_HAN)
    if han.isdigit():
        return int(han)
    return _KANJI_DIGITS.get(token)


def _check_ordinal_references(lines: list[dict[str, str]], covered_count: int) -> list[str]:
    """セリフ中の「○本目」「○番目」という順序参照が、実際に本編で扱った
    ニュース本数の範囲内かを確認する（例外は投げない）。

    ニュース候補は最大24件をAIへ提示するが、本編で実際に扱うのはmax_news本
    （通常6本）だけ。2026-09-17発覚: 「今日のひとこと」内で「7本目のニュース」
    と、選ばれなかった候補（ソフトバンクのニュース）を本編で扱ったかのように
    参照していた（本編は6本までしか扱っていない）。
    """
    problems = []
    for ln in lines:
        text = ln.get("text", "")
        for m in _ORDINAL_NEWS_RE.finditer(text):
            n = _parse_ordinal(m.group(1))
            if n is not None and (n > covered_count or n <= 0):
                problems.append(
                    f"セリフ「{text}」の「{m.group(0)}」が、実際に本編で扱った"
                    f"ニュース{covered_count}本の範囲外を参照しています"
                )
    return problems


# 「今日のひとこと」はXへの投稿を前提としたコーナーのため、X投稿ルール（Drive
# 「投稿指示文」正本、2026-09-12にケイスケが共有）の「5. 全面回避・慎重テーマ」を
# テーマ選定に適用する。RADIO本編のニュース選定には適用しない（依頼範囲外）。
_GLOSSARY_AVOID_CATEGORIES = """- 政治、選挙、軍事、防衛
- 医療、法律の具体的助言
- 株価、決算、M&Aを主題にした内容
- 自傷、他害、精神的危機
- 事件・災害への軽率な便乗"""

_GLOSSARY_SAFETY_PROMPT = """次の語は、AIニュースラジオ番組内の「今日のひとこと」コーナーで
取り上げようとしている用語です。このコーナーはXへの投稿を前提としています。

用語: {term}
今日のニュース一覧（文脈）:
{news_block}

以下のカテゴリのいずれかを主題としている場合は blocked を true にしてください。
{categories}

判定基準:
- 用語そのものがこれらのカテゴリの主体（政治家・軍事組織・武装勢力・政党・
  選挙など）である場合や、今日のニュース文脈の中でこの用語が主にこれらの
  主題として登場している場合は blocked。
- AI・テクノロジーの話題として登場しており、上記カテゴリを主題としていない
  場合（単に関連ニュースの中に一度触れられているだけ等）は blocked にしない。
- 迷う場合は blocked にする（判断に迷うこと自体がリスクのシグナル）。

出力は次のJSON形式のみとしてください（説明文やコードフェンスは不要）:
{{"blocked": true または false, "category": "該当カテゴリ名（非該当ならから文字列）", "reason": "短い理由"}}
"""


def _check_glossary_topic_safety(term: str, news: list[dict[str, str]], model: str) -> list[str]:
    """「今日のひとこと用語」がX投稿ルールの全面回避テーマに該当しないかを判定する。

    「フーシ派」のような固有名詞は、政治・軍事関連であることがキーワード一致では
    判定できない（2026-09-12発覚）。reading_check.pyと同じ設計（小さな分類専用の
    API呼び出し・fail-soft）で、意味判断が必要なこの種の分類にはAIを使う。
    API呼び出しやJSONパースに失敗した場合は警告を表示し、ブロックせずに
    空リストを返す（放送を止めない。デイリー番組を毎回確実に出すことを優先し、
    最終防波堤は人間の日次確認に委ねる）。
    """
    if not term:
        return []
    try:
        news_block = "\n".join(f"- {n.get('title', '')}" for n in news[:10])
        prompt = _GLOSSARY_SAFETY_PROMPT.format(
            term=term, news_block=news_block or "（なし）",
            categories=_GLOSSARY_AVOID_CATEGORIES,
        )
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=500,
            system=("あなたはSNS投稿のテーマ選定を判定するアシスタントです。"
                    "指定されたJSON形式のみを出力し、それ以外の文字を一切出力しません。"),
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = _extract_json(text)
        if data.get("blocked"):
            category = data.get("category") or "該当カテゴリ不明"
            reason = data.get("reason") or ""
            return [f"「{term}」はX投稿ルールの全面回避テーマ（{category}）に該当します: {reason}"]
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 今日のひとこと用語のテーマ安全性チェックに失敗（ブロックせず続行）: {e}")
        return []


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
        section = ln.get("section")
        clean.append({"speaker": sp, "text": tx,
                      "section": section if section in SECTION_ORDER else ""})
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

    # 参照可能な放送履歴＝公開日(show.publish_from)以降のみ。呼び出し側でも
    # 絞っているが、ここでも必ず絞る（片方の修正漏れで非公開期間が漏れないように）。
    publish_from = str((show_cfg or {}).get("publish_from", "") or "")
    recent_terms = _drop_before_publish(recent_terms or [], publish_from,
                                        "今日のひとこと用語の履歴")
    recent_news = _drop_before_publish(recent_news or [], publish_from,
                                       "ニュース履歴")

    prompt = PROMPT_PATH.read_text(encoding="utf-8").format(
        today=_today_label(),
        tomorrow_label=_tomorrow_label(),
        minutes=minutes,
        target_chars=target_chars,
        max_news=script_cfg.get("max_news", 4),
        news_block=_news_block(news),
        recent_terms_block=_format_recent_terms_block(recent_terms),
        recent_news_block=_format_recent_news_block(recent_news),
        news_reuse_avoid_days=script_cfg.get("news_reuse_avoid_days", 7),
        debut_block=_debut_block(show_cfg),
        bgm_intro_block=_bgm_intro_block(show_cfg),
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
            used_news = _resolve_used_news(data, news, int(script_cfg.get("max_news", 4)))
            problems = _check_glossary_term(data, recent_terms, used_news)
            problems += _check_glossary_topic_safety(
                data.get("glossary_term", ""), used_news, script_cfg["model"])
            problems += _check_ordinal_references(data["lines"], len(used_news))
            problems += _check_sections(data["lines"])
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
