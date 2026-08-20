"""TTSの誤読を合成前に検出・修正する検証ループ。

AivisSpeech/VOICEVOX互換エンジンの /audio_query レスポンスには
accent_phrases[].moras[].text にエンジンが解釈した読み（カナ）が入っている。
これを原文と一緒にClaude APIへ渡し、文脈上おかしい読みがないか照合する。

fail-soft: API呼び出しやJSONパースに失敗しても例外を投げず、
警告を表示して「修正なし」を返す（放送を止めない）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from anthropic import Anthropic

SYSTEM = (
    "あなたは日本語音声合成の読み検証アシスタントです。"
    "指定されたJSON形式のみを出力し、それ以外の文字を一切出力しません。"
)

_PROMPT_TEMPLATE = """日本語TTSの読み上げ検証をしてください。

以下は、ラジオ台本の各セリフの原文と、音声合成エンジンが実際に読み上げる
カナ読み（audio_queryの結果）のペアです。各ペアについて、エンジンの読みが
原文の文脈上正しいかを判定してください。

判定基準:
- 誤読の行だけ、誤読される語をひらがな（外来語はカタカナ）に置き換えた
  修正文を返してください。表記だけの変更で、語順や言い回しは変えないこと。
- 数字・アルファベットの読みは、意味が通っていれば正しいとみなしてよい
  （細かい読み方の揺れは誤読として扱わない）。
- 正しく読めている行は修正不要（corrections に含めない）。

出力は次のJSON形式のみとしてください（説明文やコードフェンスは不要）:
{{"corrections": [{{"index": 1, "fixed_text": "修正後のテキスト"}}, ...]}}

対象ペア:
{pairs_block}
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


def _build_pairs_block(pairs: list[dict[str, Any]]) -> str:
    lines = []
    for p in pairs:
        lines.append(f"{p['index']}. 原文: {p['text']}\n   読み: {p['reading']}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSONが見つからない")
    return json.loads(text[start:end + 1])


def _parse_corrections(text: str) -> dict[int, str]:
    data = _extract_json(text)
    corrections = data.get("corrections")
    if not isinstance(corrections, list):
        raise ValueError("correctionsがリストではない")
    result: dict[int, str] = {}
    for c in corrections:
        if not isinstance(c, dict):
            continue
        idx = c.get("index")
        fixed = c.get("fixed_text")
        if isinstance(idx, int) and not isinstance(idx, bool) and isinstance(fixed, str) and fixed.strip():
            result[idx] = fixed.strip()
    return result


def verify_readings(pairs: list[dict[str, Any]], model: str) -> dict[int, str]:
    """原文とエンジンの読みのペアをまとめて照合し、{index: fixed_text} を返す。

    API呼び出しやパースに失敗した場合は警告を表示して空辞書を返す（fail-soft）。
    """
    if not pairs:
        return {}
    try:
        prompt = _PROMPT_TEMPLATE.format(pairs_block=_build_pairs_block(pairs))
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        corrections = _parse_corrections(text)
        usage = resp.usage
        print(f"[ok] 読み検証: 入力{usage.input_tokens}トークン/"
              f"出力{usage.output_tokens}トークン/修正{len(corrections)}行")
        return corrections
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 読み検証に失敗（そのまま続行）: {e}")
        return {}
