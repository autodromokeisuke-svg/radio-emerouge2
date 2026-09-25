"""TTSの誤読を合成前に検出・修正する検証ループ。

AivisSpeech/VOICEVOX互換エンジンの /audio_query レスポンスには
accent_phrases[].moras[].text にエンジンが解釈した読み（カナ）が入っている。
これを原文と一緒にClaude APIへ渡し、文脈上おかしい読みがないか照合する。

v2（2026-09-25）: 「行全体の書き換え案をもらって2周検証する」旧方式は、
  - 指摘が2周後も残りやすい（1行に複数の誤読があると、行を丸ごと書き換える
    案では直せない語が埋もれる）
  - JSON出力が長くなりがちで、pass2で2つ目のJSONオブジェクトが混ざって
    パースが割れる事故（"Extra data"）が本番で複数回発生した
  という問題があったため、「語単位の指摘（surface/heard/correct）をもらい、
  直せたかは行ごとにエンジンへ機械的に問い合わせて確認する」方式に変更した。
  LLM呼び出しは1回のみ（find_misreadings）。2周目の検証はエンジンへの
  再問い合わせだけで完結するので、LLM側のJSON崩れによる失敗が起きない。

fail-soft: API呼び出しやJSONパースに失敗しても例外を投げず、
警告を表示して「指摘なし」を返す（放送を止めない）。
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic

SYSTEM = (
    "あなたは日本語音声合成の読み検証アシスタントです。"
    "指定されたJSON形式のみを出力し、それ以外の文字を一切出力しません。"
)

# 直近のfind_misreadings()呼び出しのトークン使用量。tools/reading_eval.py が
# モデルごとのコスト概算に使う（find_misreadings()自体の戻り値は指摘一覧だけに
# 保ちたいので、ここに逃がしている）。API呼び出し自体が失敗した場合はNone。
last_usage: dict[str, int] | None = None

_PROMPT_TEMPLATE = """日本語TTSの読み上げ検証をしてください。

以下は、ラジオ台本の各セリフの原文と、音声合成エンジンが実際にどう読み上げるか
（accent_phrasesを「／」で区切ったカナ表記）のペアです。

表記についての注意（これらはエンジン固有の書き方のクセで、誤読ではありません。
指摘しないでください）:
- 長音は母音の繰り返しで書かれます（例: 発表 → ハッピョオ、ニュース → ニュウス）。
  「ー」を使わない表記なだけで、誤読ではありません。
- 助詞の「は」「へ」「を」は発音どおり「ワ」「エ」「オ」と書かれます。
- 「／」はアクセント句の区切りです。区切りの位置や高低（プロソディ）が
  不自然に聞こえても、それだけでは誤読として扱わないでください。

指摘してほしいのは、リスナーが聞いたときに実際に「違う単語」に聞こえてしまう
誤読（同じ漢字の別の読み方に引っ張られる等）だけです。典型的な文脈依存の
誤読の例（これで全てではありません。他の語でも文脈上おかしければ指摘してください）:
  分(ぶん/わけ)・人(ひと/じん/にん)・方(かた/ほう)・次(つぎ/じ)・
  表(おもて/ひょう)・紙(かみ/し)・内製(ないせい)・目的(もくてき)・
  行った(いった/おこなった)・今日・一日・上手・他

対象セリフ:
{pairs_block}

誤読を見つけたら、原文の中でその誤読された部分を含む「ひとつづきの漢字＋
その送り仮名」をsurfaceとして過不足なく抜き出してください（原文からそのまま
抜き出すこと。言い換えたり要約したりしないこと）。1行に複数の誤読があれば、
それぞれ別の指摘としてください。正しく読めている行は指摘に含めないでください。

surfaceについての重要な制約:
  - surfaceは「漢字の連続＋任意の送り仮名（ひらがな）」だけで構成してください。
    英字・数字・カタカナ・助詞だけの指摘（例: 「AIは」のハ）は対象外です。
  - 複合語の一部だけを切り出さないでください。誤読の原因になっている漢字が
    より大きな複合語の一部（例: 「安全保障理事会」の中の「理事会」、「個人」
    の中の「人」、「方法」の中の「方」）である場合は、その複合語全体を
    surfaceにしてください。
  - surfaceは12文字以内にしてください。

heard と correct についての重要な制約:
  - heard は今エンジンがsurfaceをどう読んでいるか、correct は正しい読みを、
    それぞれ標準的なカタカナ表記で、surfaceに過不足なく対応する範囲だけ
    書いてください（surfaceより広い・狭い範囲や、前後の語・助詞・句読点を
    含めてはいけません）。
  - 助詞や仮名だけの読み違い（表記のゆれの範囲。例: 「ハ」と書くべきところが
    「ワ」になっている等）は指摘しないでください。

例:
  1) 原文「作れる分もある」の「分」が誤読 → surface: "分", heard: "ワケ",
     correct: "ブン"
  2) 原文「AIで内製開発するようになった」の「内製開発」が誤読 →
     surface: "内製開発", heard: "ウチセイカイハツ", correct: "ナイセイカイハツ"
  3) 原文「緩すぎると思う」の「緩すぎる」が誤読 → surface: "緩すぎる",
     heard: "ナルスギル", correct: "ユルスギル"

出力は次のJSON形式のみとしてください（説明文やコードフェンスは不要）:
{{"corrections": [{{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}}, ...]}}
"""


def extract_reading(query_json: dict) -> str:
    """audio_queryレスポンスから accent_phrases[].moras[].text を連結してカナ読みを返す。

    pause_mora は無視する。
    """
    reading = []
    for phrase in query_json.get("accent_phrases", []) or []:
        for mora in phrase.get("moras", []) or []:
            text = mora.get("text")
            if text:
                reading.append(text)
    return "".join(reading)


def extract_phrases(query_json: dict) -> str:
    """audio_queryレスポンスから、アクセント句ごとの読みを「／」区切りで返す。

    プロンプトへ語の区切り（アクセント句境界）を示すために使う。句末に
    ポーズ(pause_mora)がある場合は、その句の末尾に「、」を付ける。
    """
    phrases = []
    for phrase in query_json.get("accent_phrases", []) or []:
        kana = "".join(m.get("text", "") for m in (phrase.get("moras", []) or []))
        if phrase.get("pause_mora"):
            kana += "、"
        phrases.append(kana)
    return "／".join(phrases)


# --------------------------------------------------------------------------
# 読みの正規化（比較用）
#
# エンジンの長音は母音の繰り返しで書かれる（トウキョウ→トオキョオ等）一方、
# 「正しい読み」を書くときは「ー」を使う（トーキョー）のが自然なので、
# どちらの書き方で書いても同じ文字列に正規化できるようにする。
# --------------------------------------------------------------------------

_HIRAGANA_START, _HIRAGANA_END = 0x3041, 0x3096  # ぁ〜ゖ
_KATAKANA_OFFSET = 0x60  # ひらがな→カタカナのコードポイント差

# 五十音の行ごとの母音（小書き文字・拗音の子音側も、その音自体の母音に含める）
_VOWEL_ROWS = {
    "ア": "アカガサザタダナハバパマヤラワャァ",
    "イ": "イキギシジチヂニヒビピミリィ",
    "ウ": "ウクグスズツヅヌフブプムユルヴゥュ",
    "エ": "エケゲセゼテデネヘベペメレェ",
    "オ": "オコゴソゾトドノホボポモヨロヲォョ",
}
_VOWEL_OF = {c: vowel for vowel, chars in _VOWEL_ROWS.items() for c in chars}

# 発音が同じ仮名遣いのゆれ（四つ仮名など）をエンジン側の表記に揃える
_KANA_FIX = {"ヲ": "オ", "ヅ": "ズ", "ヂ": "ジ"}


def normalize_kana(s: str) -> str:
    """読みの比較用に正規化する。

    1. ひらがな→カタカナに揃える
    2. カタカナ（と長音「ー」）以外は全て捨てる（記号・「／」・空白など）
    3. 「ー」は直前の拍の母音に展開する
    4. オ行の拍の直後の「ウ」、エ行の拍の直後の「イ」は長音とみなして
       それぞれ「オ」「エ」に統一する（エンジンの長音表記に合わせる）
    5. ヲ→オ、ヅ→ズ、ヂ→ジ に揃える

    これにより「トウキョウ」「トーキョー」「トオキョオ」はすべて同じ
    文字列に正規化される。
    """
    chars: list[str] = []
    for ch in s:
        code = ord(ch)
        if _HIRAGANA_START <= code <= _HIRAGANA_END:
            ch = chr(code + _KATAKANA_OFFSET)
        if ch == "ー" or "ァ" <= ch <= "ヶ":
            chars.append(_KANA_FIX.get(ch, ch))

    result: list[str] = []
    last_vowel: str | None = None
    for ch in chars:
        if ch == "ー":
            if last_vowel:
                result.append(last_vowel)
            continue
        if ch == "ウ" and last_vowel == "オ":
            ch = "オ"
        elif ch == "イ" and last_vowel == "エ":
            ch = "エ"
        result.append(ch)
        last_vowel = _VOWEL_OF.get(ch)
    return "".join(result)


def to_hiragana(katakana: str) -> str:
    """カタカナ文字列をひらがなに変換する（「ー」等の非カタカナ文字はそのまま）。"""
    return "".join(
        chr(ord(ch) - _KATAKANA_OFFSET) if "ァ" <= ch <= "ヶ" else ch
        for ch in katakana
    )


def to_katakana(hiragana: str) -> str:
    """ひらがな文字列をカタカナに変換する（非ひらがな文字はそのまま）。

    LLMの指摘（heard/correct）は基本カタカナで返ってくる想定だが、念のため
    ひらがな混じりで返ってきても安全に使えるようにする。
    """
    return "".join(
        chr(ord(ch) + _KATAKANA_OFFSET) if _HIRAGANA_START <= ord(ch) <= _HIRAGANA_END else ch
        for ch in hiragana
    )


def kana_contains(hay: str, needle: str) -> bool:
    """hay（読み）の中にneedle（読み）が含まれるかを正規化した上で判定する。

    normalize_kana()は行全体を畳み込むため、「オ段の拍の直後のウ」のような
    境界の畳み込みは、needle単体を正規化しただけでは再現できないことがある
    （例: 「机の上」の「ウエ」は、直前の「ノ」がオ段なので行全体では
    「…ノオエ」に畳み込まれるが、"ウエ"単体を正規化しても先頭の「ウ」は
    そのまま）。この境界のケースを吸収するため、needleの先頭が「ウ」「イ」の
    場合は「オ」「エ」に読み替えた版でも追加で照合する。
    """
    hay_n = normalize_kana(hay)
    needle_n = normalize_kana(needle)
    if not needle_n:
        return False
    if needle_n in hay_n:
        return True
    if needle_n[0] == "ウ" and ("オ" + needle_n[1:]) in hay_n:
        return True
    if needle_n[0] == "イ" and ("エ" + needle_n[1:]) in hay_n:
        return True
    return False


def _build_pairs_block(pairs: list[dict[str, Any]]) -> str:
    lines = []
    for p in pairs:
        lines.append(f"{p['index']}. 原文: {p['text']}\n   読み: {p['phrases']}")
    return "\n".join(lines)


def _extract_first_json_object(text: str) -> dict[str, Any]:
    """テキストの中から最初のJSONオブジェクトだけを取り出す。

    コードフェンス（```json ... ```）や説明文が前後に付いていても、
    最初の「{」から json.JSONDecoder.raw_decode で1つ分だけ読み取ることで
    無視できる。2026-09-19〜21の本番障害（"Extra data: line 3 column 1"）は、
    出力に2つ目のJSONオブジェクトが続いてしまったケースで、素朴な
    `json.loads(text[start:end+1])`（rfind("}")で終端を決める実装）が
    2つ目の末尾までを1つの文字列とみなして壊れていた。raw_decodeなら
    1つ目のオブジェクトの終わりで自動的に止まるため、この壊れ方をしない。
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("JSONが見つからない")
    obj, _ = json.JSONDecoder().raw_decode(text, start)
    return obj


def _parse_corrections(text: str) -> list[dict[str, Any]]:
    data = _extract_first_json_object(text)
    corrections = data.get("corrections")
    if not isinstance(corrections, list):
        raise ValueError("correctionsがリストではない")
    result: list[dict[str, Any]] = []
    for c in corrections:
        if not isinstance(c, dict):
            continue
        idx = c.get("index")
        surface, heard, correct = c.get("surface"), c.get("heard"), c.get("correct")
        if (isinstance(idx, int) and not isinstance(idx, bool)
                and isinstance(surface, str) and surface.strip()
                and isinstance(heard, str) and heard.strip()
                and isinstance(correct, str) and correct.strip()):
            result.append({"index": idx, "surface": surface.strip(),
                           "heard": heard.strip(), "correct": correct.strip()})
    return result


def find_misreadings(pairs: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """原文とエンジンの読み（アクセント句表記）のペアから、誤読の指摘一覧を返す。

    各要素: {"index": int, "surface": str, "heard": str, "correct": str}。
    直せるかどうかの検証はここでは行わない（呼び出し側がエンジンへ再問い合わせ
    して機械的に確認する）。

    fail-soft: API呼び出しやパースに失敗した場合は警告を表示して空リストを
    返す（放送を止めない）。
    """
    global last_usage
    if not pairs:
        return []

    # API呼び出し自体（ネットワーク・認証等）の失敗と、その後のパース失敗を
    # 分けて扱う。トークンはAPI呼び出しが成功した時点で課金されるので、
    # そのあとのstop_reason判定やJSONパースが失敗しても last_usage は
    # 失わない（以前はパース成功後にしかセットしておらず、パース失敗時に
    # 課金済みトークンがコスト集計から抜け落ちていた）。
    try:
        prompt = _PROMPT_TEMPLATE.format(pairs_block=_build_pairs_block(pairs))
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # noqa: BLE001
        last_usage = None
        print(f"[warn] 読み検証に失敗（そのまま続行）: {e}")
        return []

    usage = resp.usage
    last_usage = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
    try:
        if resp.stop_reason in ("max_tokens", "refusal"):
            print(f"[warn] 読み検証: 応答が完了していない可能性 (stop_reason={resp.stop_reason})")
        text = "".join(b.text for b in resp.content if b.type == "text")
        corrections = _parse_corrections(text)
        print(f"[ok] 読み検証(LLM): 入力{usage.input_tokens}トークン/"
              f"出力{usage.output_tokens}トークン/指摘{len(corrections)}件")
        return corrections
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 読み検証に失敗（そのまま続行）: {e}")
        return []
