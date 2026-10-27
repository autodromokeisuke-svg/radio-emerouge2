"""Claude API で エメ×ルジェ の掛け合い台本を生成する。

環境変数 ANTHROPIC_API_KEY が必要（GitHub Actions では Secrets から注入）。
出力: {"title": str, "lines": [{"speaker": "eme"|"ruje", "text": str}, ...]}
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from anthropic import Anthropic, BadRequestError

from .bgm import SECTION_ORDER, plan_spans
from .glossary_terms import (aliases_for, find_duplicate, find_similar, load_alias_groups,
                             normalize_term)
from .holiday_jp import long_holiday_info
from .reading_check import _extract_first_json_object

JST = timezone(timedelta(hours=9))
# プロンプトは2分割。固定部（毎日同一。API の system に入れ、プロンプトキャッシュの接頭辞にする）と、
# 可変部（放送日・履歴・ニュース候補など毎日変わる入力。1通目の user メッセージ）。
# なお、プロンプトキャッシュが効くのは同一実行内のリトライ（5分TTL内）に限られ、日をまたぐ
# 呼び出しでは効かない（実行は1日1回のため）。この分割の主目的は、system部を日ごとに
# 変えない構造にしておくこと（固定部に日付やニュースなど日ごとの値を混ぜない）。
PROMPT_PATH = Path(__file__).resolve().parent.parent / "assets" / "prompt_script.md"
DAILY_PROMPT_PATH = Path(__file__).resolve().parent.parent / "assets" / "prompt_daily.md"
# オープニングの雑談のお題（放送日ごとに1つ）と、キャラ設定シート本体。どちらも assets 配下。
# シートは固定部（system）に入れるので、日ごとに変わる値（追加分・設定変更の案内）は混ぜない
BANTER_TOPICS_PATH = Path(__file__).resolve().parent.parent / "assets" / "banter_topics.yaml"
CHARACTER_SHEET_PATH = Path(__file__).resolve().parent.parent / "assets" / "character_sheet.yaml"

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


# 大型連休（土日・祝日・年末年始が4日以上続く）に入る日だけプロンプトに差し込む指示ブロック。
# 連休の初日、またはその前日の平日だけ出す（連休中の2日目以降は出さない）。通常日は空文字。
# {when} {n} {start} {end} は _holiday_block() が置換で埋める。
_LONG_HOLIDAY_BLOCK = """## 大型連休の案内（本日限定）
{when}{n}連休（{start}〜{end}）に入る。オープニング（挨拶・日付）の直後に、エメとルジェの自然な掛け合いで一言（1〜2往復、20秒程度。尺を圧迫しすぎないこと）触れること。
- 例: エメがテンション高く「連休だー！」と触れ、ルジェが落ち着いて「出かける人も多いよね」と受ける、のようなふたりらしい温度差で
- 連休中の天気・混雑・イベントなどの具体的な事実は、ニュースリストにある内容だけを使い、創作しない
- 過去の放送を前提にした言い方（「去年の連休は」など）はしない
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


def _holiday_date_label(d: Any) -> str:
    """例: 10月10日（土）"""
    return f"{d.month}月{d.day}日（{_WEEKDAYS[d.weekday()]}）"


def _holiday_block(now: datetime | None = None) -> str:
    """今日（JST）が大型連休の初日、または初日の前日の平日なら指示ブロックを返す。該当しない日は空文字。

    判定は src/holiday_jp.py（jpholiday）。そこで例外が出ても None が返るだけなので、
    放送は止まらず、案内ブロックが出ないだけになる。
    """
    now = now or datetime.now(JST)
    if now.tzinfo is not None:
        now = now.astimezone(JST)
    info = long_holiday_info(now.date())
    if not info:
        return ""
    return (_LONG_HOLIDAY_BLOCK
            .replace("{when}", info["when"])
            .replace("{n}", str(info["n"]))
            .replace("{start}", _holiday_date_label(info["start"]))
            .replace("{end}", _holiday_date_label(info["end"])))


# 話す人の表示名（雑談のお題の「話を振る人」と、照合に渡す台本の話者表記）
_SPEAKER_LABEL = {"eme": "エメ", "ruje": "ルジェ"}

# キャラ設定シートの節（YAMLのキー, プロンプトに出す見出し）。_render_character_sheet が上から順に出す。
# changes（設定変更の履歴）は別扱い（_character_change_block）なので、ここには含めない
_CHARACTER_SECTIONS = [
    ("eme", "エメ"),
    ("ruje", "ルジェ"),
    ("both", "ふたり"),
    ("past_only", "過去の放送で話したが、今後は自分から話題にしない設定（聞かれても否定はしない）"),
    ("not_mentioned", "決めていないこと（番組では触れない）"),
]
# changes の who の表示名
_CHARACTER_WHO_LABEL = {"eme": "エメ", "ruje": "ルジェ", "both": "ふたり"}

# 設定を変える日（シートの changes の date が今日）だけプロンプトに差し込む指示ブロック。
# 通常日は空文字。変更内容の箇条書き（- エメ: 「変更前」→「変更後」）は _character_change_block が後ろに足す
_CHARACTER_CHANGE_BLOCK = """## 設定変更の案内（本日限定）
今日から次の設定が変わる。オープニングかニュースの合間で、もう一方が「あれ？前は〇〇って言ってなかったっけ？」と気づき、本人が理由を一言で説明する掛け合いを入れること（1〜2往復）。このやり取りに限り、過去に言っていたことに触れてよい。
"""


def _load_banter_topics() -> list[dict[str, str]]:
    """assets/banter_topics.yaml のお題一覧（各要素 {"lead": "eme"|"ruje", "topic": str}）を返す。

    形式が不正（topics が無い・空、lead が eme/ruje 以外、topic が空）なら、PROMPT_PATH の
    読み込みと同じく黙って続行せず ValueError にする（お題が出ないまま放送すると、
    マンネリ対策が効かなくなるため。同じ形式検査を tests/test_write_script.py もしている）。
    """
    data = yaml.safe_load(BANTER_TOPICS_PATH.read_text(encoding="utf-8"))
    topics = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(topics, list) or not topics:
        raise ValueError(f"{BANTER_TOPICS_PATH.name} の形式が不正です（topics のリストがありません）")
    result = []
    for i, t in enumerate(topics, 1):
        lead = t.get("lead") if isinstance(t, dict) else None
        topic = t.get("topic") if isinstance(t, dict) else None
        if (not isinstance(lead, str) or lead not in _SPEAKER_LABEL
                or not isinstance(topic, str) or not topic.strip()):
            raise ValueError(f"{BANTER_TOPICS_PATH.name} の{i}件目が不正です"
                             "（lead は eme / ruje、topic は空でない文字列にしてください）")
        result.append({"lead": lead, "topic": topic.strip()})
    return result


def _banter_block(now: datetime | None = None) -> str:
    """今日（JST）のオープニング用「雑談のお題」の指示ブロックを返す。

    お題は、放送日 d（JST）の通し日数 d.toordinal() をお題の件数で割った余りで選ぶ。
    日付だけで決まるので、同じ日は何度実行しても同じお題になり、1日1つ番号順に進んで、
    最後まで行ったら先頭に戻る。件数が7の倍数だと同じお題が毎回同じ曜日に来てしまうため、
    yaml 側は7の倍数にしない（その旨は yaml の先頭コメントにも書いてある）。
    """
    now = now or datetime.now(JST)
    if now.tzinfo is not None:
        now = now.astimezone(JST)
    topics = _load_banter_topics()
    item = topics[now.date().toordinal() % len(topics)]
    return ("## 今日の雑談のお題（オープニングのワンシーン用）\n"
            f"- お題: {item['topic']}\n"
            f"- 話を振る人: {_SPEAKER_LABEL[item['lead']]}\n")


def _load_character_sheet() -> dict[str, Any]:
    """assets/character_sheet.yaml（キャラ設定シート本体）を読んで返す。

    各節（eme / ruje / both / past_only / not_mentioned / changes）はリスト。キーが無い・空の
    節は許す。辞書でない、または節がリストでない場合は _load_banter_topics と同じ理由で ValueError。
    """
    data = yaml.safe_load(CHARACTER_SHEET_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{CHARACTER_SHEET_PATH.name} の形式が不正です（キーと値の辞書ではありません）")
    for key in [k for k, _ in _CHARACTER_SECTIONS] + ["changes"]:
        if data.get(key) is not None and not isinstance(data[key], list):
            raise ValueError(f"{CHARACTER_SHEET_PATH.name} の {key} がリストではありません")
    return data


def _render_character_sheet(sheet: dict[str, Any]) -> str:
    """キャラ設定シートを、固定部（system）に埋め込むテキストにする。

    節ごとに「### 見出し」＋「- 項目」を並べる。項目が空の節は出さない。出典メモなどの
    YAMLコメントは読み込み時に落ちているので、プロンプトには出ない。
    """
    blocks = []
    for key, heading in _CHARACTER_SECTIONS:
        items = [str(i).strip() for i in (sheet.get(key) or []) if str(i).strip()]
        if items:
            blocks.append(f"### {heading}\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(blocks)


def _character_change_block(sheet: dict[str, Any], now: datetime | None = None) -> str:
    """今日（JST）が、シートの changes に書かれた設定変更の日なら、その案内ブロックを返す。

    該当する changes が無い日は空文字。その日の放送だけ、エメかルジェが「あれ？前は〇〇って
    言ってなかったっけ？」と気づいて理由を説明する掛け合いを入れさせる。今日の日付で
    項目が不正（who が eme/ruje/both 以外、before/after が空）なら警告してその項目だけ除く。
    """
    now = now or datetime.now(JST)
    if now.tzinfo is not None:
        now = now.astimezone(JST)
    today_key = now.strftime("%Y%m%d")
    bullets = []
    for c in sheet.get("changes") or []:
        # YAML で引用符なしの 20261105 は整数になるので、文字列にしてから比べる
        if not isinstance(c, dict) or str(c.get("date", "")).strip() != today_key:
            continue
        who_key = c.get("who")
        who = _CHARACTER_WHO_LABEL.get(who_key) if isinstance(who_key, str) else None
        before, after = str(c.get("before") or "").strip(), str(c.get("after") or "").strip()
        if not who or not before or not after:
            print(f"[warn] キャラ設定シートの changes に不正な項目があるため、設定変更の案内から除きました"
                  f"（date={today_key}。who は eme/ruje/both、before/after は必須）")
            continue
        bullets.append(f"- {who}: 「{before}」→「{after}」")
    if not bullets:
        return ""
    return _CHARACTER_CHANGE_BLOCK + "\n".join(bullets) + "\n"


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
    alias_groups = load_alias_groups()
    lines = []
    for t in ordered:
        date = t.get("date", "")
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        term = t.get("term", "")
        line = f"- {date}: {term}"
        # 英語名・略称・カタカナ表記の揺れも「使用済み」と分かるよう別表記を添える
        alts = aliases_for(term, alias_groups)
        if alts:
            line += f"（別表記: {' / '.join(alts)}）"
        lines.append(line)
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


def _format_character_additions_block(additions: list[dict[str, str]]) -> str:
    """キャラ設定シートの追加分（これまでの放送で新しく出た設定）を「- 設定」の箇条書きにする。

    日付は出さない（シート本体と同じく「ふたりがもともと知っている設定」として守らせるため）。
    1項目は必ず1行にする（改行を含むと、箇条書きの外にプロンプトの指示が紛れ込めてしまう）。
    """
    lines = []
    for a in additions:
        fact = " ".join(str(a.get("fact") or "").split()) if isinstance(a, dict) else ""
        if fact:
            lines.append(f"- {fact}")
    return "\n".join(lines) if lines else "（まだ無し）"


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


def _term_in_news(term: str, used_news: list[dict[str, str]]) -> bool:
    """用語が、扱ったニュースのタイトルまたは概要のどれかに含まれるか。

    全角半角・大小・ひらカナ・空白・記号の揺れは normalize_term で吸収する。
    normalize_term は空白を除くため、記事を連結してから正規化すると記事の境界をまたいだ
    偽の一致が起きる。タイトル・概要のそれぞれを別々に正規化して判定する。
    用語が空に正規化される場合（記号だけ等）は、何にでも含まれてしまうので一致扱いにしない。
    """
    norm_term = normalize_term(term)
    if not norm_term:
        return False
    for n in used_news:
        for field in ("title", "summary"):
            if norm_term in normalize_term(n.get(field) or ""):
                return True
    return False


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
    if not _term_in_news(term, used_news):
        problems.append(f"「{term}」が本編で実際に扱ったニュースの中に見当たりません")
    # 重複判定は表記揺れ（全角/半角・大小・ひらカナ・英語名・略称）を吸収して行う。
    # recent_terms は公開開始日以降・全期間の履歴（run_daily が渡す）
    dup = find_duplicate(term, recent_terms, load_alias_groups())
    if dup is not None:
        date = dup.get("date", "")
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        problems.append(f"「{term}」は{date}に使用済みの用語「{dup.get('term', '')}」と"
                        f"同じです（公開開始以降は再利用禁止）")
    else:
        similar = find_similar(term, recent_terms)
        if similar:
            # ソフト判定。関連語を誤って弾かないよう、問題にはせずログに残すだけ
            print(f"[info] 「{term}」は過去の用語と似ています(重複扱いにはしません): "
                  + " / ".join(f"{s.get('date', '')} {s.get('term', '')}" for s in similar[:3]))
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


# 台本とキャラ設定シートの照合プロンプト。{sheet}{additions}{change}{lines} を埋める
# （JSON例の波かっこは str.format 用に二重にしてある）
_CHARACTER_CHECK_PROMPT = """あなたはラジオ番組の台本チェック係です。指定されたJSON形式のみを出力し、それ以外の文字を一切出力しません。

次は、ラジオ番組のパーソナリティ「エメ」と「ルジェ」のキャラ設定シートと、今日の台本です。

## キャラ設定シート（本体）
{sheet}

## キャラ設定シートの追加分（これまでの放送で新しく出た設定）
{additions}

## 今日の設定変更
{change}

## 今日の台本（番号. 話す人: セリフ）
{lines}

次の3つを調べてください。
1. contradictions: シート（本体・追加分）と矛盾するセリフ。例: シートで「朝の一杯はあったかいお茶」なのに「毎朝コーヒーを飲む」と言う。「決めていないこと」にある項目（家族・ペット・住まい・年齢・経歴）を具体的に話しているセリフもここに入れる。「今日の設定変更」に書かれた変更を説明するやり取りの中の「前は〜だった」という発言は矛盾ではない。
2. banned: 「過去の放送で話したが、今後は自分から話題にしない設定」を話題にしているセリフ（エメがルジェにメッセージや連絡を何度も送った話、朝から待ちきれずに連絡した話など）。
3. new_facts: シート（本体・追加分）に無い、エメ・ルジェ自身やふたりの関係についての新しい設定で、次の放送以降も変わらないはずのもの（好き嫌い・趣味・習慣・考え方・持ち物・ふたりの思い出など）。シートと矛盾するものは new_facts に入れず contradictions に入れる。シートに同じ意味のものがすでにあれば入れない。

次は対象外です（どれにも入れない）:
- ニュースへの感想・意見、その日の出来事や気分（「今朝は寝坊した」など）
- クイズ・ゲーム・空想・役になりきる遊び・冗談の中の発言
- リスナーへの呼びかけや一般論

new_facts の fact は「エメは〜」「ルジェは〜」「ふたりは〜」の形で、40文字以内で簡潔に書いてください。who は eme / ruje / both のいずれかです。

出力形式（このJSONだけ。説明文やコードフェンスは不要。該当なしの項目は空のリスト）:
{{"contradictions": [{{"index": 12, "reason": "理由"}}], "banned": [{{"index": 5, "reason": "理由"}}], "new_facts": [{{"who": "eme", "fact": "エメは〜", "index": 30}}]}}
"""

# 照合の出力上限。思考（adaptive）も max_tokens を消費する
_CHARACTER_CHECK_MAX_TOKENS = 16000
# new_facts の fact の最大文字数（プロンプトでは40文字以内と指示している。それより長いものは
# 「設定」ではなく感想や出来事の可能性が高いので捨てる）
_CHARACTER_FACT_MAX_LEN = 60


def _fact_key(text: str) -> str:
    """設定が既出かどうかを比べるためのキー（NFKC＋前後空白除去）。"""
    return unicodedata.normalize("NFKC", str(text)).strip()


def _check_character_consistency(lines: list[dict[str, str]], sheet_text: str, additions_text: str,
                                 change_text: str, model: str
                                 ) -> tuple[list[str], list[dict[str, str]]]:
    """台本をキャラ設定シートと照合し、(書き直し用の指摘, 新しく出た設定) を返す。例外は投げない。

    AIを1回呼び、次の3つを調べさせる（プロンプトは _CHARACTER_CHECK_PROMPT）。
      - 矛盾: シート（本体・追加分）と食い違うセリフ
      - 禁止ネタ: 「今後は自分から話題にしない設定」を話題にしているセリフ
      - 新しい設定: シートに無く、次回以降も守らせたいふたりの設定
    1つ目の戻り値は「N行目: ...」形式の指摘文（リトライのヒント文に使う）。2つ目は
    {"who": "eme"|"ruje"|"both", "fact": str} のリストで、放送後に run_daily が
    site/character_sheet_additions.json へ追記する。who が不正・fact が空か長すぎるもの、
    シート本体・追加分と（NFKC＋前後空白除去で）同じものは除く。

    読み検証が思考なしで空振りした反省から、思考（adaptive）を有効にして呼ぶ
    （claude-sonnet-4-6 は明示しないと思考しない）。思考の指定が 400 で拒否されるモデルなら、
    思考なしで1回だけ呼び直す。temperature 等のサンプリング引数は付けない（2026-10-21以降は400）。

    fail-soft: API呼び出しやJSON解析の失敗、応答が完了していない（max_tokens / refusal）場合は、
    警告を表示して ([], []) を返す（放送を止めない）。ログにはセリフの行全体を出さず、
    行番号と切り詰めた理由だけを出す。
    """
    try:
        numbered = "\n".join(
            f"{i}. {_SPEAKER_LABEL.get(ln.get('speaker'), '')}: {ln.get('text', '')}"
            for i, ln in enumerate(lines, 1))
        prompt = _CHARACTER_CHECK_PROMPT.format(
            sheet=sheet_text, additions=additions_text,
            change=change_text.strip() or "（なし）", lines=numbered)
        client = Anthropic()
        request = {"model": model, "max_tokens": _CHARACTER_CHECK_MAX_TOKENS,
                   "messages": [{"role": "user", "content": prompt}]}
        try:
            resp = client.messages.create(**request, thinking={"type": "adaptive"})
        except BadRequestError:
            resp = client.messages.create(**request)
        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason in ("max_tokens", "refusal"):
            print(f"[warn] キャラ照合: 応答が完了していないため結果を使いません (stop_reason={stop_reason})")
            return [], []
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = _extract_first_json_object(text)

        def flagged(key: str) -> list[tuple[int, str]]:
            """contradictions / banned の (行番号, 理由)。行番号が台本の範囲外のものは捨てる。"""
            raw = data.get(key)
            found = []
            for item in raw if isinstance(raw, list) else []:
                idx = item.get("index") if isinstance(item, dict) else None
                if isinstance(idx, int) and not isinstance(idx, bool) and 1 <= idx <= len(lines):
                    reason = item.get("reason")
                    found.append((idx, reason.strip() if isinstance(reason, str) else ""))
            return found

        contradictions, banned = flagged("contradictions"), flagged("banned")
        problems = [f"{idx}行目: キャラ設定シートと矛盾" + (f"（{reason}）" if reason else "")
                    for idx, reason in contradictions]
        problems += [f"{idx}行目: 話題にしない設定を話題にしている" + (f"（{reason}）" if reason else "")
                     for idx, reason in banned]

        # シート本体・追加分に既にある項目（「- 項目」の行）と同じものは、新しい設定にしない
        known = set()
        for block in (sheet_text, additions_text):
            for line in block.splitlines():
                line = line.strip()
                known.add(_fact_key(line[2:] if line.startswith("- ") else line))
        new_facts = []
        raw_facts = data.get("new_facts")
        for item in raw_facts if isinstance(raw_facts, list) else []:
            if not isinstance(item, dict):
                continue
            who, fact = item.get("who"), item.get("fact")
            if not isinstance(who, str) or who not in _CHARACTER_WHO_LABEL or not isinstance(fact, str):
                continue
            fact = fact.strip()
            if not fact or len(fact) > _CHARACTER_FACT_MAX_LEN or _fact_key(fact) in known:
                continue
            known.add(_fact_key(fact))
            new_facts.append({"who": who, "fact": fact})

        usage = getattr(resp, "usage", None)

        def tokens(name: str) -> str:
            n = getattr(usage, name, None)
            return str(n) if isinstance(n, int) and not isinstance(n, bool) else "?"

        print(f"[ok] キャラ照合: 矛盾{len(contradictions)}件 / 禁止ネタ{len(banned)}件 / "
              f"新しい設定{len(new_facts)}件 / 入力{tokens('input_tokens')}トークン / "
              f"出力{tokens('output_tokens')}トークン")
        for idx, reason in contradictions + banned:
            print(f"[warn] キャラ照合: {idx}行目 {reason[:60]}")
        return problems, new_facts
    except Exception as e:  # noqa: BLE001
        print(f"[warn] キャラ照合に失敗（そのまま続行）: {e}")
        return [], []


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


def _usage_summary(resp: Any) -> str:
    """[usage] ログ用の1行（input / cache_write / cache_read / output / stop）。

    値が無い・None・整数でない（MagicMockなど）ものは「?」にする。
    ログのためだけの処理なので、どんな応答オブジェクトでも絶対に例外を投げない。
    """
    def num(obj: Any, name: str) -> str:
        v = getattr(obj, name, None)
        return str(v) if isinstance(v, int) and not isinstance(v, bool) else "?"

    try:
        usage = getattr(resp, "usage", None)
        stop = getattr(resp, "stop_reason", None)
        stop = stop if isinstance(stop, str) and stop else "?"
        return (f"input={num(usage, 'input_tokens')} "
                f"cache_write={num(usage, 'cache_creation_input_tokens')} "
                f"cache_read={num(usage, 'cache_read_input_tokens')} "
                f"output={num(usage, 'output_tokens')} stop={stop}")
    except Exception:  # noqa: BLE001
        return "input=? cache_write=? cache_read=? output=? stop=?"


def _refusal_category(resp: Any) -> str:
    """stop_reason が refusal のとき、stop_details.category（cyber / bio 等）を返す。取れなければ「不明」。"""
    try:
        details = getattr(resp, "stop_details", None)
        category = details.get("category") if isinstance(details, dict) else getattr(details, "category", None)
        return category if isinstance(category, str) and category else "不明"
    except Exception:  # noqa: BLE001
        return "不明"


def write_script(news: list[dict[str, str]], script_cfg: dict[str, Any],
                 minutes: int, recent_terms: list[dict[str, str]] | None = None,
                 recent_news: list[dict[str, str]] | None = None,
                 show_cfg: dict[str, Any] | None = None,
                 character_additions: list[dict[str, str]] | None = None) -> dict[str, Any]:
    target_chars = minutes * int(script_cfg.get("chars_per_minute", 320))

    # 参照可能な放送履歴＝公開日(show.publish_from)以降のみ。呼び出し側でも
    # 絞っているが、ここでも必ず絞る（片方の修正漏れで非公開期間が漏れないように）。
    publish_from = str((show_cfg or {}).get("publish_from", "") or "")
    recent_terms = _drop_before_publish(recent_terms or [], publish_from,
                                        "今日のひとこと用語の履歴")
    recent_news = _drop_before_publish(recent_news or [], publish_from,
                                       "ニュース履歴")

    # キャラ設定シート本体は固定部（system）へ。追加分と設定変更の案内は日ごとに変わるので可変部へ。
    # 照合チェック（_check_character_consistency）にも同じ文面を渡す
    character_sheet = _load_character_sheet()
    sheet_text = _render_character_sheet(character_sheet)
    additions_text = _format_character_additions_block(character_additions or [])
    change_text = _character_change_block(character_sheet)
    # 照合だけ別モデルを使いたい場合は config.yaml の script.character_check_model で指定する
    # （未設定なら台本生成と同じモデル）
    check_model = script_cfg.get("character_check_model") or script_cfg["model"]

    # system＝固定部（日ごとに変わる値を絶対に混ぜない。キャッシュ効果の実態は冒頭のコメント参照）
    system_text = SYSTEM + "\n\n" + PROMPT_PATH.read_text(encoding="utf-8").format(
        minutes=minutes,
        target_chars=target_chars,
        max_news=script_cfg.get("max_news", 4),
        news_reuse_avoid_days=script_cfg.get("news_reuse_avoid_days", 14),
        character_sheet=sheet_text,
    )
    # user（1通目）＝可変部（放送日・履歴・ニュース候補など毎日変わる入力）
    daily_text = DAILY_PROMPT_PATH.read_text(encoding="utf-8").format(
        today=_today_label(),
        tomorrow_label=_tomorrow_label(),
        debut_block=_debut_block(show_cfg),
        bgm_intro_block=_bgm_intro_block(show_cfg),
        holiday_block=_holiday_block(),
        character_change_block=change_text,
        banter_block=_banter_block(),
        news_reuse_avoid_days=script_cfg.get("news_reuse_avoid_days", 14),
        recent_news_block=_format_recent_news_block(recent_news),
        recent_terms_block=_format_recent_terms_block(recent_terms),
        character_additions_block=additions_text,
        news_block=_news_block(news),
    )
    client = Anthropic()
    # キャッシュのブレークポイントは2つだけ（system末尾と、1通目userの末尾）。
    # リトライ時に追記するメッセージは文字列contentのままにし、1通目を不変に保つ
    # （同一実行内のリトライ＝5分TTL内の2回目以降の呼び出しで、system＋1通目が
    # キャッシュから読める）
    system_blocks = [{"type": "text", "text": system_text,
                      "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user",
                 "content": [{"type": "text", "text": daily_text,
                              "cache_control": {"type": "ephemeral"}}]}]
    last_err: Exception | None = None
    refusals: list[str] = []
    for attempt in range(_MAX_ATTEMPTS):
        resp = client.messages.create(
            model=script_cfg["model"],
            max_tokens=20000,  # 思考もmax_tokensを消費。16000では10/7・10/15・10/17に切断→再試行で台本が短くなった。非ストリーミングで安全な上限は約21,000
            system=system_blocks,
            messages=messages,
        )
        usage_line = _usage_summary(resp)
        print(f"[usage] {usage_line}")
        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason == "refusal":
            # 安全装置による拒否。messagesは増やさず、同じ内容でそのまま再試行する
            category = _refusal_category(resp)
            refusals.append(category)
            last_err = RuntimeError(f"安全装置により拒否されました: {category}")
            print(f"[warn] 台本生成が拒否されました (試行{attempt + 1}): "
                  f"category={category} / {usage_line}")
            continue
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            if stop_reason == "max_tokens":
                print(f"[warn] 台本の出力が上限で途中で切れました (試行{attempt + 1}): {usage_line}")
                raise ValueError("出力が長すぎて途中で切れました。"
                                 "内容は変えずJSONが閉じる長さに収めて出力し直してください")
            data = _validate(_extract_json(text))
            used_news = _resolve_used_news(data, news, int(script_cfg.get("max_news", 4)))
            problems = _check_glossary_term(data, recent_terms, used_news)
            problems += _check_glossary_topic_safety(
                data.get("glossary_term", ""), used_news, script_cfg["model"])
            problems += _check_ordinal_references(data["lines"], len(used_news))
            problems += _check_sections(data["lines"])
            # キャラ設定シートとの照合（AI呼び出し）は、上の指摘が無いとき、または最終試行のときだけ
            # 行う。上の指摘で書き直しになる台本を照合しても無駄になるため。最終試行は、受容する
            # 台本の新しい設定（記録対象）を取るために必ず行う
            character_problems: list[str] = []
            new_facts: list[dict[str, str]] = []
            if not problems or attempt == _MAX_ATTEMPTS - 1:
                character_problems, new_facts = _check_character_consistency(
                    data["lines"], sheet_text, additions_text, change_text, check_model)
            all_problems = problems + character_problems
            if all_problems and attempt < _MAX_ATTEMPTS - 1:
                hint = "; ".join(all_problems) + "。"
                if problems:
                    hint += "別の用語を選び直してください。"
                if character_problems:
                    hint += "キャラ設定シートに合わせて該当のセリフを直してください。"
                raise ValueError(hint + "直した台本全体を、指定のJSONだけで出力し直してください。")
            if all_problems:
                print(f"[warn] 台本に懸念あり(最終試行のため受容): {'; '.join(all_problems)}")
            # 受容した台本で新しく出た設定。run_daily が放送後にサイトの追加分へ記録する
            data["character_additions"] = new_facts
            total = sum(len(ln["text"]) for ln in data["lines"])
            print(f"[ok] 台本生成: {data['title']} / {len(data['lines'])}セリフ "
                  f"/ 約{total}文字 (目標{target_chars})")
            return data
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            print(f"[warn] 台本の検証に失敗 (試行{attempt + 1}): {e}")
            if isinstance(e, json.JSONDecodeError):
                # e.pos は _extract_json が切り出した文字列（e.doc）上の位置。元の応答全文
                # (text) から切ると、前置きやコードフェンスの分だけ位置がずれる
                snippet = e.doc[max(0, e.pos - 80):e.pos + 80]
                print(f"[debug] 失敗箇所付近: ...{snippet}...")
                if e.pos >= len(e.doc) - 1:
                    print("[hint] 出力が途中で切れている可能性があります（解析位置が出力の末尾）")
            debug_path = Path(__file__).resolve().parent.parent / "out" / f"last_script_error_{attempt + 1}.txt"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(text, encoding="utf-8")
            print(f"[debug] 生テキストを保存: {debug_path}")
            retry_hint = (str(e) if isinstance(e, ValueError) and not isinstance(e, json.JSONDecodeError)
                         else "出力が指定のJSON形式ではありません。指定のJSONのみを出力し直してください。")
            # 出力が空・空白のみ（思考だけで上限に達した等）のときは、空のassistantメッセージを
            # 積まない（APIは空contentを受け付けない）。userのhintだけを追記する
            # （同じroleの連続はAPIが1つのターンに結合する）
            if text.strip():
                messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": retry_hint})
    if len(refusals) == _MAX_ATTEMPTS:
        raise RuntimeError(f"台本生成が安全装置により拒否されました: {refusals[-1]}")
    raise RuntimeError(f"台本生成に失敗: {last_err}")
