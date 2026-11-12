"""決まった誤読を機械的に直すための「規則」の定義（エンジン非依存の純粋な関数）。

AivisSpeechは、単独の「人」「他」、ひらがなの後の「分」、「緩」＋ひらがななどを
繰り返し誤読する。ここでは該当箇所を見つけて、ひらがなへの置き換え候補を返すだけ。
実際に置き換えるかどうかは、エンジンに両方読ませて読みが変わる時だけ
build_audio._apply_reading_rules() が決める（エンジンが既に正しく読んでいる
箇所は変えない）。

規則は後から1行で足せるよう、(規則名, コンパイル済み正規表現, ひらがな) の
リスト _RULES にまとめてある（reading_normalize.py の _RULES と同じ流儀）。
正規表現のマッチ全体が置き換え対象の表記になる（先読み・後読みで文脈を表す）。
"""
from __future__ import annotations

import re

_KANJI = "一-鿿々"
_DIGIT = "0-9０-９"
_KATA = "ァ-ヺー"
_LATIN = "A-Za-zＡ-Ｚａ-ｚ"
_HIRA = "ぁ-ゖ"

_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # 単独の「人」。「3人」「日本人」「人間」「助っ人」「人々」等は除く
    ("hito", re.compile(rf"(?<![{_KANJI}{_DIGIT}{_KATA}{_LATIN}っ])人(?![{_KANJI}])"), "ひと"),
    # 単独の「他」。「その他」「他社」「自他」等は除く
    ("hoka", re.compile(rf"(?<![{_KANJI}])(?<!その)他(?![{_KANJI}])"), "ほか"),
    # ひらがなの直後の「分」。「分かる」「分ける」「分野」「分類」等は除く
    ("bun", re.compile(rf"(?<=[{_HIRA}])分(?![{_KANJI}かけ])"), "ぶん"),
    # ひらがなが続く「緩」。「緩和」「緩急」等は除く
    ("yuru", re.compile(rf"緩(?=[{_HIRA}])"), "ゆる"),
]


def find_rule_hits(text: str) -> list[dict]:
    """text中の規則該当箇所を start 昇順で返す。重なりは先に見つかった方を残す。

    各要素: {"start", "end", "surface", "kana"(ひらがな), "rule"}
    """
    hits: list[dict] = []
    for name, pattern, kana in _RULES:
        for m in pattern.finditer(text):
            hits.append({"start": m.start(), "end": m.end(), "surface": m.group(0),
                         "kana": kana, "rule": name})
    hits.sort(key=lambda h: (h["start"], h["end"]))
    result: list[dict] = []
    last_end = -1
    for h in hits:
        if h["start"] >= last_end:
            result.append(h)
            last_end = h["end"]
    return result
