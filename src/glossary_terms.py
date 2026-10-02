"""「今日のひとこと」用語の重複判定（表記揺れを吸収する）。

用語の履歴（site/glossary_history.json）と突き合わせて「同じ用語を二度使っていないか」
を判定するための純粋関数群。標準ライブラリ + PyYAML のみを使い、ネットワークや
Claude APIには触れない。

- normalize_term: 全角/半角・大小・ひらカナ・中黒や記号・「エーアイ」と「AI」の揺れを吸収
- load_alias_groups: assets/glossary_aliases.yaml の別名グループ（英語名・略称・カタカナ表記）
- find_duplicate: 正規化後に等しい、または同じ別名グループの過去用語（＝再利用禁止）。
  「フィジカルAI（身体性AI）」のような括弧つき表記は、全体・括弧の外側・括弧の内側を
  それぞれ比較候補にする
- find_similar: 片方が他方を含む程度の類似（ログ用のソフト判定。再生成のトリガーにはしない）
- aliases_for: 台本生成AIへ見せる「別表記」の一覧
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

ALIASES_PATH = Path(__file__).resolve().parent.parent / "assets" / "glossary_aliases.yaml"

# ソフト類似判定で、短い方の正規化語がこの文字数以上のときだけ「含む」を類似とみなす
# （「AI」「ai」程度の短い語は何にでも含まれるため）
_SIMILAR_MIN_LEN = 4

# 括弧つき表記（「フィジカルAI（身体性AI）」）の外側・内側を比較候補にするとき、
# 正規化後の長さがこの文字数以上のものだけを候補にする。「エージェント（AI）」の
# 「AI」のような短い断片が、過去の「AI」と偽陽性で一致するのを避けるため。
# 括弧を含む用語の「全体」の候補だけは、長さに関わらず常に使う
_CANDIDATE_MIN_LEN = 3

# 括弧（全角・半角の丸括弧、角括弧、隅付き括弧）。中身に括弧を含まない最も内側の1組
_INNERMOST_BRACKETS_RE = re.compile(r"[（(［\[【]([^（(［\[【）)］\]】]*)[）)］\]】]")

# 比較時に取り除く記号。空白は str.isspace() でまとめて除く。
# 長音記号「ー」は語の一部（「ソブリン」「ハーネス」等）なので残す
_STRIP_CHARS = frozenset(
    "・･·-‐‑–—―_./\\,，、。()（）[]【】「」『』\"'“”‘’:：;；!！?？"
)

# ひらがな(U+3041〜U+3096)・繰り返し記号(U+309D〜U+309E)をカタカナへ統一する変換表
_HIRA_TO_KATA = {cp: cp + 0x60 for cp in (*range(0x3041, 0x3097), 0x309D, 0x309E)}


def normalize_term(term: str) -> str:
    """用語を比較用に正規化する。

    NFKC → casefold → ひらがなをカタカナへ → 「エーアイ」を「ai」へ → 空白・記号を除去。
    空文字（や None 相当）は空文字を返す。
    """
    if not term:
        return ""
    s = unicodedata.normalize("NFKC", str(term)).casefold()
    s = s.translate(_HIRA_TO_KATA)
    s = s.replace("エーアイ", "ai")
    return "".join(ch for ch in s if not ch.isspace() and ch not in _STRIP_CHARS)


def _split_brackets(term: str) -> tuple[str, list[str]]:
    """用語を「括弧の外側」と「括弧の内側（各括弧の中身）」に分ける。

    括弧は （）()［］[]【】。入れ子は内側から順に取り出す
    （「A（B（C）D）E」→ 外側「AE」、内側「C」「BD」）。対応の取れない括弧は
    そのまま外側に残る（normalize_term が記号として除去する）。
    """
    inner: list[str] = []

    def take(m: re.Match[str]) -> str:
        inner.append(m.group(1))
        return ""

    outside = str(term)
    while True:
        replaced = _INNERMOST_BRACKETS_RE.sub(take, outside)
        if replaced == outside:
            return outside, inner
        outside = replaced


def _term_candidates(term: str) -> set[str]:
    """用語の比較候補（正規化済み）の集合。

    全体 / 括弧の外側 / 括弧の内側 を normalize_term し、全体は長さに関わらず、
    外側・内側は正規化後 _CANDIDATE_MIN_LEN 文字以上のものだけを候補にする。
    括弧が無ければ全体だけ。空の括弧「（）」や括弧だけの用語は候補が空になりうる。
    """
    candidates: set[str] = set()
    if not term:
        return candidates
    whole = normalize_term(term)
    if whole:
        candidates.add(whole)
    outside, inner = _split_brackets(term)
    for part in (outside, *inner):
        n = normalize_term(part)
        if len(n) >= _CANDIDATE_MIN_LEN:
            candidates.add(n)
    return candidates


def load_alias_groups(path: Path | None = None) -> list[set[str]]:
    """別名辞書（既定 assets/glossary_aliases.yaml）を読み、グループのリストを返す。

    各グループは同じ用語の別表記（元の表記のまま）の集合。比較は normalize_term で
    正規化してから行う。ファイルが無い/壊れている場合は [] を返し、[warn] を1行出す
    だけで例外を投げない（別名辞書が無くても放送を止めない）。
    """
    p = Path(path) if path is not None else ALIASES_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        raw_groups = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(raw_groups, list):
            print(f"[warn] 用語の別名辞書の形式が不正なため別名なしで続行します: {p.name}")
            return []
        groups: list[set[str]] = []
        for raw in raw_groups:
            if not isinstance(raw, list):
                continue
            members = {str(m).strip() for m in raw
                       if isinstance(m, (str, int, float)) and not isinstance(m, bool)
                       and str(m).strip()}
            if members:
                groups.append(members)
        return groups
    except Exception as e:  # noqa: BLE001 - 辞書が読めなくても放送は止めない（YAMLError・OSError以外も含む）
        print(f"[warn] 用語の別名辞書を読めないため別名なしで続行します: "
              f"{p.name} ({type(e).__name__})")
        return []


def _build_index(alias_groups: list[set[str]] | None) -> tuple[dict[str, int], dict[int, list[str]]]:
    """正規化語→グループ番号、グループ番号→メンバー（元の表記）を返す。

    1つの正規化語が複数グループに現れた場合は、それらのグループを和集合として1つに
    統合する。
    """
    groups = [g for g in (alias_groups or []) if g]
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_seen: dict[str, int] = {}
    for i, g in enumerate(groups):
        for m in g:
            for n in _term_candidates(m):
                if n in first_seen:
                    parent[find(i)] = find(first_seen[n])
                else:
                    first_seen[n] = i
    norm_to_gid = {n: find(i) for n, i in first_seen.items()}
    members: dict[int, list[str]] = {}
    for i, g in enumerate(groups):
        members.setdefault(find(i), []).extend(g)
    return norm_to_gid, members


def find_duplicate(term: str, recent_terms: list[dict[str, str]],
                   alias_groups: list[set[str]] | None = None) -> dict[str, str] | None:
    """termと同じ用語を過去に使っていれば、その要素（最も新しい日付のもの）を返す。

    recent_terms は {"date": "YYYYMMDD", "term": str} のリスト。正規化後に等しい、または
    同じ別名グループに属する過去用語を「同じ用語」とみなす。無ければ None。
    括弧つきの用語（「フィジカルAI（身体性AI）」）は、全体・括弧の外側・括弧の内側の
    それぞれ（_term_candidates）を比較候補にし、過去用語・別名グループのメンバーにも
    同じ候補生成を適用する。候補同士が1つでも一致するか、同じ別名グループなら重複。
    alias_groups が None のときは別名を使わず、正規化後の一致だけで判定する。
    """
    candidates = _term_candidates(term)
    if not candidates:
        return None
    norm_to_gid, _ = _build_index(alias_groups)
    gids = {norm_to_gid[c] for c in candidates if c in norm_to_gid}
    best: dict[str, str] | None = None
    for r in recent_terms:
        recent_candidates = _term_candidates(r.get("term", ""))
        if not recent_candidates:
            continue
        same_group = bool(gids) and any(norm_to_gid.get(c) in gids for c in recent_candidates)
        if candidates & recent_candidates or same_group:
            if best is None or r.get("date", "") > best.get("date", ""):
                best = r
    return best


def find_similar(term: str, recent_terms: list[dict[str, str]]) -> list[dict[str, str]]:
    """ソフト判定: 正規化後に片方が他方を含む（等しくはない）過去用語を、新しい順に返す。

    短い方の正規化語が4文字以上のときだけ対象にする。ログ用であり、再生成の
    トリガーにしてはならない（「エージェント」と「AIエージェント」のような関連語を
    誤って弾かないため）。
    """
    nt = normalize_term(term)
    if not nt:
        return []
    hits = []
    for r in recent_terms:
        rn = normalize_term(r.get("term", ""))
        if not rn or rn == nt:
            continue
        shorter, longer = sorted((nt, rn), key=len)
        if len(shorter) >= _SIMILAR_MIN_LEN and shorter in longer:
            hits.append(r)
    return sorted(hits, key=lambda r: r.get("date", ""), reverse=True)


def aliases_for(term: str, alias_groups: list[set[str]] | None) -> list[str]:
    """termと同じ別名グループの他の表記を、元の表記のまま返す（表示用）。

    term と正規化後に等しい表記は除く。正規化後に同じ表記は1つにまとめ、並びは
    決定的（文字コード順）にする。termがどのグループにも無ければ空リスト。
    """
    nt = normalize_term(term)
    if not nt:
        return []
    norm_to_gid, members = _build_index(alias_groups)
    gid = norm_to_gid.get(nt)
    if gid is None:
        return []
    seen = {nt}
    result = []
    for m in sorted(members[gid]):
        n = normalize_term(m)
        if n and n not in seen:
            seen.add(n)
            result.append(m)
    return result
