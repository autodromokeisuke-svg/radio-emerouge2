"""台本を1行ずつ音声合成し、1本の放送音源(MP3)に組み立てる。

- セリフ間に短い「間」を入れる
- assets/jingle.mp3 があれば冒頭と末尾に流す（任意）
- 音量をざっくり揃えてから書き出す
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from pydub import AudioSegment

from . import bgm as bgm_mod
from .reading_check import (
    extract_phrases,
    extract_reading,
    find_misreadings,
    normalize_kana,
    to_hiragana,
    to_katakana,
)
from .reading_normalize import normalize_for_tts
from .tts import get_engine

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TARGET_DBFS = -16.0

# 音声合成エンジンが繰り返し誤読する語の固定置換（プロンプト指示だけでは
# 再発したため保険として追加）。「重め/重い」は「じゅうめ/ちょう」等に
# 誤読されるが、この番組の文脈では常に「おも」と読ませたいので安全に置換できる
_KNOWN_MISREADINGS = {
    "重め": "おもめ",
    "重い": "おもい",
}


def _fix_known_misreadings(text: str) -> str:
    for wrong, right in _KNOWN_MISREADINGS.items():
        text = text.replace(wrong, right)
    return text


def _normalize(seg: AudioSegment) -> AudioSegment:
    if seg.dBFS == float("-inf"):
        return seg
    return seg.apply_gain(TARGET_DBFS - seg.dBFS)


def _is_kana_char(ch: str) -> bool:
    """1文字がひらがな・カタカナ・長音記号「ー」かどうか。"""
    if not ch:
        return False
    code = ord(ch)
    return (0x3041 <= code <= 0x3096) or ("ァ" <= ch <= "ヶ") or ch == "ー"


def _is_kanji_char(ch: str) -> bool:
    """1文字が漢字（CJK統合漢字、または「々」）かどうか。"""
    if not ch:
        return False
    return (0x4E00 <= ord(ch) <= 0x9FFF) or ch == "々"


def _is_hiragana_char(ch: str) -> bool:
    """1文字がひらがな（ぁ〜ゖ）かどうか。"""
    return bool(ch) and 0x3041 <= ord(ch) <= 0x3096


# 読み（heard/correct）を整形する際に取り除く句読点類
_PUNCT_CHARS = "、。,.!?！？・"


def _sanitize_kana_field(s: str) -> str | None:
    """指摘のheard/correctを整形する（A対策）。

    前後の空白・句読点（、。,.!?！？・）を取り除き、残りが1文字でも仮名
    （ひらがな・カタカナ・長音「ー」）以外なら整形不能としてNoneを返す
    （呼び出し側でrejected_scope扱いにする）。LLMがcorrectの末尾に「、」
    まで含めてしまい、そのまま挿入すると「ブン、」→「、、」のように句読点が
    重複する事故を防ぐ。
    """
    cleaned = "".join(ch for ch in s if not ch.isspace() and ch not in _PUNCT_CHARS)
    if not cleaned or any(not _is_kana_char(ch) for ch in cleaned):
        return None
    return cleaned


def _surface_kanji_okuri(surface: str) -> tuple[str, str] | None:
    """surfaceが「漢字の連続＋任意の送り仮名（ひらがな）」という形（B対策）
    に収まっているかを検査し、収まっていれば (漢字部分, 送り仮名部分) を
    返す。英字・数字・カタカナが混じっていたり、漢字の後に漢字以外を挟んで
    さらに漢字が続いたりする場合はNone（呼び出し側でrejected_scope扱いに
    する）。「AIは」のような助詞だけの指摘や、範囲がそもそも複合語の部分列に
    なっていない指摘をここで機械的に弾く。
    """
    n = len(surface)
    i = 0
    while i < n and _is_kanji_char(surface[i]):
        i += 1
    if i == 0:
        return None
    kanji_part, okuri = surface[:i], surface[i:]
    if any(not _is_hiragana_char(ch) for ch in okuri):
        return None
    return kanji_part, okuri


def _find_eligible_occurrences(text: str, surface: str) -> list[int]:
    """textの中のsurfaceの出現のうち、複合語の一部を巻き込まない位置（C対策）
    だけを返す。

    直前の文字が漢字なら、その出現は複合語（「個人」の「人」・「方法」の
    「方」・「安全保障理事会」の「理事会」等）の一部を切り出しただけとみなし
    除外する。直後が漢字かどうかは、それだけでは複合語の途中か単に次の語が
    漢字で始まっているだけかを区別できない（「って人多い」の「人」等）ため
    ここでは判定材料にせず、実際に誤読されている箇所かどうかは後段の
    `_occurrence_verified`（実測プローブ）と `_scope_ok`（読みの妥当性）に
    委ねる。
    """
    positions = []
    start = 0
    while True:
        idx = text.find(surface, start)
        if idx == -1:
            break
        before_ok = idx == 0 or not _is_kanji_char(text[idx - 1])
        if before_ok:
            positions.append(idx)
        start = idx + len(surface)
    return positions


def _occurrence_verified(engine: Any, speaker: str, text: str, idx: int, surface: str,
                         heard: str, current_reading_norm: str, warn_label: str) -> bool:
    """textのidx位置にあるsurfaceの出現が、LLMが指摘した誤読の実体かどうかを
    機械的に確かめる（D対策）。

    その出現だけをheardのカナ表記（カタカナを先に試し、再現できなければ
    ひらがなにフォールバック）に置き換えた「プローブ文」をエンジンへ
    問い合わせ、その読みが現在の（実際に誤読されている）行の読みと完全一致
    すれば、その出現こそが誤読箇所だとみなせる。
    """
    for kana in dict.fromkeys([to_katakana(heard), to_hiragana(heard)]):
        probe_text = text[:idx] + kana + text[idx + len(surface):]
        try:
            probe_q = engine.query(speaker, probe_text)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {warn_label} の位置検証クエリに失敗: {type(e).__name__}")
            continue
        if normalize_kana(extract_reading(probe_q)) == current_reading_norm:
            return True
    return False


# 送り仮名・前後の仮名の境界で許容する読みのゆれ（母音の畳み込み境界対策）
_BOUNDARY_VOWEL_VARIANTS = {"ウ": "オ", "イ": "エ"}
# 直後の仮名が助詞として読まれる場合の表記（ヲ→オはnormalize_kanaが既に処理する）
_PARTICLE_READING = {"ハ": "ワ", "ヘ": "エ"}


def _tolerant_suffix_match(hay: str, needle: str) -> bool:
    """hayがneedleで終わるか。needleの先頭1文字だけウ→オ／イ→エの境界ゆれを
    追加で許容する（E1用）。"""
    if not needle:
        return True
    if hay.endswith(needle):
        return True
    alt = _BOUNDARY_VOWEL_VARIANTS.get(needle[0])
    return bool(alt) and hay.endswith(alt + needle[1:])


def _f_variants(fold_f: str) -> list[str]:
    """出現の直後にある仮名の並びfold_fについて、比較に使う表記の候補を返す
    （文字どおりの表記と、先頭が「は」「へ」なら助詞として読まれた場合の
    表記の両方。E3用）。"""
    variants = [fold_f]
    alt = _PARTICLE_READING.get(fold_f[0]) if fold_f else None
    if alt:
        variants.append(alt + fold_f[1:])
    return variants


def _overlaps_nonempty_prefix(s: str, variants: list[str]) -> bool:
    """sが、variantsのいずれかの非空prefixで終わっているか（E3用）。"""
    for v in variants:
        for k in range(1, len(v) + 1):
            if s.endswith(v[:k]):
                return True
    return False


def _overlaps_nonempty_suffix(s: str, other: str) -> bool:
    """sが、otherの非空suffixで始まっているか（E3用）。"""
    return any(s.startswith(other[-k:]) for k in range(1, len(other) + 1))


def _hiragana_run_before(text: str, idx: int) -> str:
    """textのidxの直前から連続するひらがなの並びを返す（E3用）。"""
    j = idx
    while j > 0 and _is_hiragana_char(text[j - 1]):
        j -= 1
    return text[j:idx]


def _hiragana_run_after(text: str, idx: int, surf_len: int) -> str:
    """textのidx+surf_lenの直後から連続するひらがなの並びを返す（E3用）。"""
    start = idx + surf_len
    j = start
    while j < len(text) and _is_hiragana_char(text[j]):
        j += 1
    return text[start:j]


def _scope_ok_basic(kanji_part: str, okuri: str, fold_heard: str, fold_correct: str) -> bool:
    """出現位置に依存しない範囲チェック（E1・E2・E4）。surfaceの出現位置を
    まだ特定していない段階（エンジン問い合わせによる位置検証の前）でも
    機械的に弾けるよう、位置依存のE3とは独立させてある。1つでも怪しけ
    れば弾く。

    E1 送り仮名の境界: surfaceが送り仮名okuriで終わるなら、correctの末尾も
       fold(okuri)で終わっていること（境界のウ→オ／イ→エゆれは許容）。
    E2 漢字の拍数の妥当範囲: 漢字n文字に対し、correctの拍数（送り仮名分を
       除く）がn拍〜3n+1拍に収まっていること。
    E4 heardとの長さの妥当性: correctとheardの拍数差が、heardの半分
       （最低2）を超えないこと。
    """
    fold_okuri = normalize_kana(okuri)
    if fold_okuri and not _tolerant_suffix_match(fold_correct, fold_okuri):
        return False

    n_kanji = sum(1 for ch in kanji_part if _is_kanji_char(ch))
    bound_len = len(fold_correct) - len(fold_okuri)
    if not (n_kanji <= bound_len <= 3 * n_kanji + 1):
        return False

    if abs(len(fold_correct) - len(fold_heard)) > max(2, len(fold_heard) // 2):
        return False

    return True


def _scope_ok_position(text: str, idx: int, surface: str, fold_correct: str) -> bool:
    """検証済みの1出現について、位置に依存する範囲チェック（E3）を行う。

    E3 前後の仮名との重複: correctの末尾が、直後の仮名（助詞として読まれた
       場合の表記も含む）の非空prefixと重なっていたり、correctの先頭が、
       直前の仮名の非空suffixと重なっていたりしないこと（別の語の読みを
       巻き込んでいる兆候）。
    """
    fold_after = normalize_kana(_hiragana_run_after(text, idx, len(surface)))
    fold_before = normalize_kana(_hiragana_run_before(text, idx))
    if fold_after and _overlaps_nonempty_prefix(fold_correct, _f_variants(fold_after)):
        return False
    if fold_before and _overlaps_nonempty_suffix(fold_correct, fold_before):
        return False

    return True


def _scope_ok(text: str, idx: int, surface: str, kanji_part: str, okuri: str,
             fold_heard: str, fold_correct: str) -> bool:
    """検証済みの1出現について、correctがsurfaceの読みとして妥当な範囲に
    収まっているかを検査する（E対策）。位置非依存のE1・E2・E4
    （`_scope_ok_basic`）と、位置依存のE3（`_scope_ok_position`）を
    まとめて行う。1つでも怪しければ弾く。
    """
    return (_scope_ok_basic(kanji_part, okuri, fold_heard, fold_correct)
            and _scope_ok_position(text, idx, surface, fold_correct))


_TOLERANT_PAIRS = ({"ウ", "オ"}, {"イ", "エ"})


def _chars_tolerant_eq(a: str, b: str) -> bool:
    """1文字同士が一致するか、ウ↔オ／イ↔エの境界ゆれの範囲で一致するか。"""
    return a == b or any({a, b} == p for p in _TOLERANT_PAIRS)


def _find_heard_positions(Ro: str, fold_heard: str) -> list[int]:
    """現在の行の読みRoの中で、fold_heardが現れる位置をすべて返す（F用）。
    先頭1文字だけウ↔オ／イ↔エの境界ゆれを許容し、残りは完全一致を要求する。"""
    m = len(fold_heard)
    if m == 0 or m > len(Ro):
        return []
    positions = []
    for k in range(len(Ro) - m + 1):
        if _chars_tolerant_eq(Ro[k], fold_heard[0]) and Ro[k + 1:k + m] == fold_heard[1:]:
            positions.append(k)
    return positions


def _match_with_tolerant_positions(a: str, b: str, positions: set[int]) -> bool:
    """aとbが同じ長さで、positionsに含まれる位置（境界の再折り畳み）だけ
    ウ↔オ／イ↔エのゆれを許容して、それ以外は完全一致するか。"""
    if len(a) != len(b):
        return False
    for i, (ca, cb) in enumerate(zip(a, b)):
        if ca == cb:
            continue
        if i in positions and _chars_tolerant_eq(ca, cb):
            continue
        return False
    return True


def _fix_occurrence(engine: Any, speaker: str, text: str, idx: int, surface: str,
                    fold_heard: str, correct: str, fold_correct: str,
                    current_reading_norm: str, warn_label: str
                    ) -> tuple[str, dict, str, int, int] | None:
    """検証済みの出現をcorrectの読みに置き換えられるか、厳密な「完全再構成」
    チェックで確かめる（F対策）。

    ひらがな表記／カタカナ表記の両方を候補としてエンジンへ問い合わせ、
    「置換後の行全体の読みRcが、置換前の読みRo（current_reading_norm）の
    うちheardの現れた区間[k, k+len(heard))だけをcorrectで置き換えた文字列
    と、区間の両端（各1文字だけウ↔オ／イ↔エの境界ゆれを許容）を除いて
    完全一致する」候補だけを残す。この1点のチェックで「区間の外側が変わって
    いないこと」と「区間の中身が過不足なくcorrectそのものであること」の
    両方を同時に保証できる（旧方式の長さ／部分一致チェックの組み合わせでは
    区間外の変化や、correctが広すぎ・狭すぎて語が重複・欠落するケースを
    見逃していた）。採用はアクセント句が少ない方（同数ならひらがな）。
    戻り値は (新テキスト, 新クエリ, 表記ラベル, 置換した文字列の長さ,
    一致したheardの開始位置k) か、どちらの候補も一致しなければNone。
    """
    Ro = current_reading_norm
    h_positions = _find_heard_positions(Ro, fold_heard)
    if not h_positions:
        return None

    best: tuple[int, str, dict, str, int, int] | None = None
    for kana, label in ((to_hiragana(correct), "ひらがな"), (to_katakana(correct), "カタカナ")):
        candidate_text = text[:idx] + kana + text[idx + len(surface):]
        try:
            cand_q = engine.query(speaker, candidate_text)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {warn_label} の検証クエリに失敗: {type(e).__name__}")
            continue
        Rc = normalize_kana(extract_reading(cand_q))

        matched_k = None
        for k in h_positions:
            expected = Ro[:k] + fold_correct + Ro[k + len(fold_heard):]
            tol_positions = {k}
            end = k + len(fold_correct)
            if end < len(expected):
                tol_positions.add(end)
            if _match_with_tolerant_positions(Rc, expected, tol_positions):
                matched_k = k
                break
        if matched_k is None:
            continue

        n_phrases = len(cand_q.get("accent_phrases", []) or [])
        if best is None or n_phrases < best[0]:
            best = (n_phrases, candidate_text, cand_q, label, len(kana), matched_k)

    if best is None:
        return None
    _, candidate_text, cand_q, label, repl_len, matched_k = best
    return candidate_text, cand_q, label, repl_len, matched_k


def _apply_one_correction(engine: Any, speaker: str, line_text: str, current_q: dict | None,
                          surface: str, heard: str, correct: str, warn_label: str
                          ) -> tuple[str, dict | None, str, dict[str, Any]]:
    """1件の指摘を検証・適用する（A〜Fを順に適用。疑わしければ即座に
    「変更しない」を返す）。

    戻り値: (更新後のテキスト, 更新後のクエリ, outcome, detail用の追加
    情報)。outcomeは "applied" | "unverified" | "rejected_scope" |
    "unfixable"。
    """
    if len(surface) > 12:
        return line_text, current_q, "rejected_scope", {}

    heard_clean = _sanitize_kana_field(heard)
    correct_clean = _sanitize_kana_field(correct)
    if heard_clean is None or correct_clean is None:
        return line_text, current_q, "rejected_scope", {}
    fold_heard = normalize_kana(heard_clean)
    fold_correct = normalize_kana(correct_clean)
    if fold_heard == fold_correct:  # no-op
        return line_text, current_q, "rejected_scope", {}

    kanji_okuri = _surface_kanji_okuri(surface)
    if kanji_okuri is None:
        return line_text, current_q, "rejected_scope", {}
    kanji_part, okuri = kanji_okuri

    # 位置非依存の範囲チェック（E1・E2・E4）はエンジン問い合わせが要る
    # 出現位置の検証より前に済ませる。これによりcorrectが明らかに
    # heardとかけ離れている／漢字の拍数に見合わない指摘は、エンジンが
    # 出現位置を1つも検証できない場合でもrejected_scopeとして機械的に
    # 弾ける（unverified止まりにならない）。
    if not _scope_ok_basic(kanji_part, okuri, fold_heard, fold_correct):
        return line_text, current_q, "rejected_scope", {}

    eligible = _find_eligible_occurrences(line_text, surface)
    if not eligible:
        return line_text, current_q, "unfixable", {}

    reading_norm = normalize_kana(extract_reading(current_q)) if current_q is not None else ""

    verified = [idx for idx in eligible
               if _occurrence_verified(engine, speaker, line_text, idx, surface,
                                       heard_clean, reading_norm, warn_label)]
    if len(verified) != 1:
        return line_text, current_q, "unverified", {}
    idx = verified[0]

    if not _scope_ok_position(line_text, idx, surface, fold_correct):
        return line_text, current_q, "rejected_scope", {}

    fixed = _fix_occurrence(engine, speaker, line_text, idx, surface, fold_heard,
                            correct_clean, fold_correct, reading_norm, warn_label)
    if fixed is None:
        return line_text, current_q, "unfixable", {}
    new_text, new_q, label, _repl_len, k = fixed
    after_reading_norm = normalize_kana(extract_reading(new_q))
    extra = {
        "chosen": label,
        "before_reading": reading_norm[k:k + len(fold_heard)],
        "after_reading": after_reading_norm[k:k + len(fold_correct)],
    }
    return new_text, new_q, "applied", extra


def _truncate_log(s: str, limit: int = 12) -> str:
    """ログ出力用に文字列を切り詰める（G対策）。指摘語だけを対象にした
    用途でも、念のため長さを制限しておく（セリフの行全体は別途出さない）。"""
    return s if len(s) <= limit else s[:limit] + "…"


def _apply_reading_corrections(engine: Any, lines: list[dict[str, str]],
                               texts: list[str], queries: list[dict | None],
                               corrections: list[dict[str, Any]]) -> dict[str, Any]:
    """LLMが指摘した誤読を1件ずつ機械的に検証しながら適用する（LLM呼び出しなし）。

    各指摘の判定は _apply_one_correction() に委ねる（A〜Fの詳細は各
    ヘルパーのdocstring参照）。「疑わしければ何もしない」を徹底しており、
    複合語の一部を切り出しただけの指摘（C）や、範囲が広すぎ・狭すぎる
    correct（E）、区間の外側まで変えてしまう候補（F）は、直せそうでも
    unfixable/rejected_scope/unverifiedとして行を変更しない。

    texts・queries はその場で更新する。戻り値は {"pointed", "applied",
    "unverified", "rejected_scope", "unfixable", "details"}。detailsは
    指摘1件ごとの内訳（tools/reading_eval.py がラベル外の修正を人間の目で
    チェックするのに使う。appliedの場合は該当区間の読みのbefore/afterも
    含む）。ログには指摘語（surface/heard/correct）を12文字に切り詰めて
    出すが、セリフの行全体は出さない。例外はtype名だけを出す（エンジンの
    エラーメッセージにはリクエストURL＝行全体が含まれうるため）。
    """
    report: dict[str, Any] = {"pointed": len(corrections), "applied": 0, "unverified": 0,
                              "rejected_scope": 0, "unfixable": 0, "details": []}
    for c in corrections:
        pos = c["index"] - 1
        if not (0 <= pos < len(lines)):
            continue
        surface, heard, correct = c["surface"], c["heard"], c["correct"]
        warn_label = f"{c['index']}行目 「{_truncate_log(surface)}」"
        speaker = lines[pos]["speaker"]
        line_text = texts[pos]

        new_text, new_q, outcome, extra = _apply_one_correction(
            engine, speaker, line_text, queries[pos], surface, heard, correct, warn_label)

        detail = {"index": c["index"], "surface": surface, "heard": heard, "correct": correct,
                  "outcome": outcome, **extra}
        report[outcome] += 1
        report["details"].append(detail)

        heard_t, correct_t = _truncate_log(heard), _truncate_log(correct)
        if outcome == "applied":
            texts[pos] = new_text
            queries[pos] = new_q
            print(f"[fix] {warn_label} {heard_t}→{correct_t}（{extra.get('chosen')}で置換）")
        elif outcome == "unverified":
            print(f"[warn] {warn_label} ({heard_t}→{correct_t}) は現在の読みで再現できず、"
                  f"検証不能として見送ります")
        elif outcome == "rejected_scope":
            print(f"[warn] {warn_label} ({heard_t}→{correct_t}) は範囲が不審なため除外")
        else:  # unfixable
            print(f"[warn] {warn_label} ({heard_t}→{correct_t}) は修正できず、そのまま採用します")
    return report


def _run_reading_check_with_report(engine: Any, lines: list[dict[str, str]],
                                   prepared_texts: list[str], model: str
                                   ) -> tuple[list[dict | None], dict[str, Any]]:
    """全セリフの読みをエンジンに問い合わせ、LLMの指摘を機械的な再検証で直す。

    LLM呼び出しは1回のみ（find_misreadings）。直せたかどうかの2周目は
    エンジンへの再問い合わせだけで完結する（詳細は reading_check.py 冒頭参照）。
    戻り値は (合成にそのまま使えるクエリJSONのリスト, 件数レポート)。
    """
    texts = list(prepared_texts)
    queries: list[dict | None] = [None] * len(lines)
    pairs = []
    for i, ln in enumerate(lines):
        q = engine.query(ln["speaker"], texts[i])
        queries[i] = q
        pairs.append({"index": i + 1, "text": texts[i], "phrases": extract_phrases(q)})

    corrections = find_misreadings(pairs, model)
    report = _apply_reading_corrections(engine, lines, texts, queries, corrections)
    print(f"[ok] 読み検証: 指摘{report['pointed']}件 / 修正{report['applied']}件 / "
          f"検証不能{report['unverified']}件 / 範囲不審で除外{report['rejected_scope']}件 / "
          f"修正できず{report['unfixable']}件")
    return queries, report


def _run_reading_check(engine: Any, lines: list[dict[str, str]],
                       prepared_texts: list[str], model: str) -> list[dict | None]:
    """synthesize()から呼ぶ薄いラッパー。件数レポートが要る側は
    _run_reading_check_with_report()を直接使う（tools/reading_eval.py等）。
    """
    queries, _ = _run_reading_check_with_report(engine, lines, prepared_texts, model)
    return queries


def _prepare_bgm(lines: list[dict[str, str]], bgm_cfg: dict[str, Any] | None):
    """BGMを付けられるか判定し、(設定, 区間計画, メイン曲, ニュース曲) を返す。

    付けない（無効・ラベル不正・素材が読めない）場合は None。BGMは付加価値なので、
    どの理由でも例外にせず、声だけの放送を成立させる。
    """
    if bgm_cfg is None:
        return None
    settings = bgm_mod.resolve_config(bgm_cfg)
    if not settings["enabled"]:
        return None
    plan = bgm_mod.plan_spans([ln.get("section", "") for ln in lines])
    if plan is None:
        print("[warn] セリフのsectionラベルが不正なため、BGM無しで続行します")
        return None
    main = bgm_mod.load_track(settings["main_file"])
    news = bgm_mod.load_track(settings["news_file"])
    if main is None or news is None:
        return None
    return settings, plan, main, news


def synthesize(lines: list[dict[str, str]], tts_cfg: dict[str, Any],
               reading_check_model: str | None = None,
               lead_in_ms: int = 0) -> tuple[AudioSegment, list[int]]:
    """全セリフを合成して声だけの放送を作り、(放送, 各セリフの開始位置ms) を返す。

    lead_in_ms は先頭に足す無音（BGMの曲だけ流すリードイン用）の長さ。
    BGMを重ねる処理(add_bgm)とは分けてあり、BGMの音量調整時は声の合成結果を使い回せる。
    """
    engine = get_engine(tts_cfg)
    engine.prepare()

    pause = AudioSegment.silent(duration=int(tts_cfg.get("pause_ms", 350)))
    show = AudioSegment.silent(duration=300 + lead_in_ms)

    jingle_path = ASSETS / "jingle.mp3"
    jingle = None
    if jingle_path.exists():
        jingle = _normalize(AudioSegment.from_file(jingle_path))
        show += jingle + pause

    total = len(lines)
    # normalize_for_tts は音声合成・読み検証にだけ使うテキストを整える
    # （「RAG（ラグ）」のような英字略語＋カナ読みの二重読み対策）。ここが
    # TTSエンジンおよび読み検証(reading_check)へテキストが渡る直前の唯一の
    # 適用箇所であり、番組説明文・RSS・字幕・X投稿素材等には適用しない。
    # カッコの二重読み対策を先に行ってから、既知の誤読を個別置換する。
    prepared_texts = [_fix_known_misreadings(normalize_for_tts(ln["text"]))
                      for ln in lines]
    queries: list[dict | None] = [None] * total

    if reading_check_model and getattr(engine, "supports_reading_check", False):
        try:
            queries = _run_reading_check(engine, lines, prepared_texts, reading_check_model)
        except Exception as e:  # noqa: BLE001
            # type名だけを出す（エンジンのエラーメッセージにはリクエストURL＝
            # 台本の行そのものが含まれうるため、行全体をログに出さないため）
            print(f"[warn] 読み検証フェーズに失敗。通常合成にフォールバックします: {type(e).__name__}")
            queries = [None] * total

    failed = 0
    starts_ms = [0] * total  # 各セリフが show の中で始まる位置（BGMの区間切り替えに使う）
    for i, ln in enumerate(lines, 1):
        starts_ms[i - 1] = len(show)
        try:
            q = queries[i - 1]
            if q is not None:
                seg = engine.synth_from_query(ln["speaker"], q)
            else:
                seg = engine.synth(ln["speaker"], prepared_texts[i - 1])
            show += _normalize(seg) + pause
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[warn] セリフ{i}の合成をスキップ: {e}")
        if i % 10 == 0 or i == total:
            print(f"[..] 収録中 {i}/{total}")

    if failed > total // 4:
        raise RuntimeError(f"合成失敗が多すぎる ({failed}/{total})。エンジン状態を確認して。")

    if jingle is not None:
        show += jingle
    return show, starts_ms


def add_bgm(show: AudioSegment, starts_ms: list[int], bgm_ctx) -> AudioSegment:
    """声だけの放送に区間ごとのBGMを重ねる。失敗しても声だけの放送をそのまま返す。"""
    settings, plan, main_track, news_track = bgm_ctx
    tail_ms = int(settings["tail_ms"])
    show = show + AudioSegment.silent(duration=tail_ms)  # 最後の声のあとの余韻ぶん
    try:
        mixed = bgm_mod.mix_bgm(show, plan, starts_ms, main_track, news_track,
                                settings, tail_ms)
        print("[ok] BGMを重ねました（メイン: オープニング/エンディング、ニュース: 本編〜今日のひとこと）")
        return mixed
    except Exception as e:  # noqa: BLE001
        print(f"[warn] BGMのミキシングに失敗（声だけで続行）: {e}")
        return show


def build(lines: list[dict[str, str]], tts_cfg: dict[str, Any],
         reading_check_model: str | None = None,
         bgm_cfg: dict[str, Any] | None = None) -> AudioSegment:
    bgm_ctx = _prepare_bgm(lines, bgm_cfg)
    lead_in_ms = int(bgm_ctx[0]["lead_in_ms"]) if bgm_ctx else 0

    show, starts_ms = synthesize(lines, tts_cfg, reading_check_model, lead_in_ms)
    if bgm_ctx:
        show = add_bgm(show, starts_ms, bgm_ctx)

    minutes = len(show) / 1000 / 60
    print(f"[ok] 収録完了: 約{minutes:.1f}分")
    return show


def _embed_cover_art(mp3_path: Path, cover_path: Path) -> None:
    try:
        try:
            tags = ID3(mp3_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,  # front cover
            desc="Cover",
            data=cover_path.read_bytes(),
        ))
        tags.save(mp3_path)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] カバー画像の埋め込みに失敗: {e}")


def export_mp3(show: AudioSegment, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    show.export(out_path, format="mp3", bitrate="96k",
                tags={"artist": "えめるーじぇ"})

    cover_path = ASSETS / "cover.jpg"
    if cover_path.exists():
        _embed_cover_art(out_path, cover_path)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"[ok] 書き出し: {out_path} ({size_mb:.1f} MB)")
