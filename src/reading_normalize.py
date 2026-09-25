"""TTSに渡す直前のテキストだけを整える、副作用のない正規化関数。

「RAG（ラグ）」のように、英字略語のすぐ後にカナ読みの注釈が続く表記を
そのままTTSへ渡すと、「アールエージー ラグ」のように英字部分とカナ部分の
両方が読まれてしまう（二重読み）。ここでは音声合成・読み検証（reading_check）
にだけ使うテキストを整形する。番組説明文・RSS・字幕・X投稿素材・用語履歴など
音声以外に出るテキストには絶対に適用しないこと（呼び出し側の責務）。

ルールは後から1行で追記できるよう、(ルール名, 正規表現, 置換関数) の
タプルのリストとして _RULES にまとめてある。
"""
from __future__ import annotations

import re
from typing import Callable

# --- 文字種判定ヘルパー（「トークン直前の文字」を分類するためだけに使う） ---


def _is_hiragana_char(ch: str) -> bool:
    return bool(ch) and "ぁ" <= ch <= "ゖ"


def _is_katakana_char(ch: str) -> bool:
    return bool(ch) and "゠" <= ch <= "ヿ"


def _is_kanji_char(ch: str) -> bool:
    return bool(ch) and (("一" <= ch <= "鿿") or ch == "々")


# --- R1: ラテン表記＋カナ読み（例: "RAG（ラグ）", "Claude Code（クロードコード）"） ---

# 英数字トークン。"GPT-5.5" のような1語、"Claude Code" のように半角スペース
# で区切られた複数語も対象にする。2語目以降は "Gemini 3" "Llama 4 Scout" の
# ように数字だけの語（例: 3, 4, 2.5, 17）も許容する（1語目は必ず英字始まり）。
_LATIN_WORD = r"[A-Za-z][A-Za-z0-9.\-+&']*"
_NUMERIC_WORD = r"[0-9]+(?:\.[0-9]+)?"
_SUBSEQUENT_WORD = r"(?:" + _LATIN_WORD + r"|" + _NUMERIC_WORD + r")"
_LATIN_TOKEN = _LATIN_WORD + r"(?:[ ](?=[A-Za-z0-9])" + _SUBSEQUENT_WORD + r")*"

# カッコの中身が「カナのみ」（カタカナ・ひらがな・長音ー・中点・スペース）。
_KANA_ONLY = r"[ぁ-ゖァ-ヺー・\s]+"

# カッコの前後に許容する半角/全角スペース0〜1個
_SPACE_OPT = r"[ 　]?"

_R1_PATTERN = re.compile(
    r"(?P<token>" + _LATIN_TOKEN + r")" + _SPACE_OPT
    + r"[（(](?P<kana>" + _KANA_ONLY + r")[）)]" + _SPACE_OPT
)


def _r1_repl(m: re.Match[str]) -> str:
    text = m.string
    start = m.start()
    before = text[start - 1] if start > 0 else ""
    if before and (_is_kanji_char(before) or _is_katakana_char(before)):
        # 複合語の一部の可能性（例: 「生成AI（エーアイ）」）→ 表記は残し、カッコだけ消す
        return m.group("token")
    # 文頭／空白／句読点・記号／ひらがなの直後 → 読みに置き換え、カッコを消す
    return m.group("kana")


# --- R2: カナ表記＋ラテン原綴り（例: "オープンエーアイ（OpenAI）"） ---

_KATAKANA_TOKEN = r"[ァ-ヺー・]+"
_LATIN_ONLY = r"[A-Za-z0-9.\-+&'\s]+"

_R2_PATTERN = re.compile(
    r"(?P<katakana>" + _KATAKANA_TOKEN + r")" + _SPACE_OPT
    + r"[（(](?P<latin>" + _LATIN_ONLY + r")[）)]" + _SPACE_OPT
)


def _r2_repl(m: re.Match[str]) -> str:
    return m.group("katakana")


# ルール一覧。追加するときはここに (名前, 正規表現, 置換関数) を1行足す。
_RULES: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    ("latin_token_with_kana_gloss", _R1_PATTERN, _r1_repl),
    ("kana_word_with_latin_spelling", _R2_PATTERN, _r2_repl),
]


def normalize_for_tts(text: str) -> str:
    """TTSに渡す直前のテキストだけを整形する（読み上げ用途専用）。

    「英字（カナ読み）」「カナ表記（ラテン原綴り）」の二重読みパターンを
    畳み込む。音声以外の出力（番組説明文・RSS・字幕・X投稿素材・用語履歴等）
    には適用しないこと。
    """
    if not text:
        return text
    for _name, pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
