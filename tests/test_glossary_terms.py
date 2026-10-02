"""src/glossary_terms.py（今日のひとこと用語の表記揺れ対応の重複判定）の単体テスト。"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.glossary_terms import (aliases_for, find_duplicate, find_similar,
                                load_alias_groups, normalize_term)


def _entry(date: str, term: str) -> dict[str, str]:
    return {"date": date, "term": term}


class TestNormalizeTerm(unittest.TestCase):
    def test_fullwidth_and_halfwidth_are_equal(self) -> None:
        self.assertEqual(normalize_term("ＡＩ"), normalize_term("AI"))
        self.assertEqual(normalize_term("ＬＬＭ１"), normalize_term("llm1"))
        self.assertEqual(normalize_term("ｱｲ"), normalize_term("アイ"))

    def test_case_is_ignored(self) -> None:
        self.assertEqual(normalize_term("Physical AI"), normalize_term("physical ai"))
        self.assertEqual(normalize_term("HuggingFace"), normalize_term("huggingface"))

    def test_middle_dot_spaces_and_symbols_are_removed(self) -> None:
        self.assertEqual(normalize_term("ヒューマン・イン・ザ・ループ"),
                         normalize_term("ヒューマンインザループ"))
        self.assertEqual(normalize_term("ヒューマン･イン･ザ･ループ"),
                         normalize_term("ヒューマンインザループ"))
        self.assertEqual(normalize_term("physical　AI"), normalize_term("physicalAI"))
        self.assertEqual(normalize_term("fine-tuning"), normalize_term("fine tuning"))
        self.assertEqual(normalize_term("「AIエージェント」"), normalize_term("AIエージェント"))
        self.assertEqual(normalize_term("(RSI)"), "rsi")
        self.assertEqual(normalize_term("a/b.c_d,e、f。g"), "abcdefg")

    def test_hiragana_is_unified_to_katakana(self) -> None:
        self.assertEqual(normalize_term("ぜい弱性"), normalize_term("ゼイ弱性"))
        self.assertEqual(normalize_term("ふぃじかるAI"), normalize_term("フィジカルAI"))

    def test_eeai_katakana_becomes_ai(self) -> None:
        self.assertEqual(normalize_term("フィジカルエーアイ"), normalize_term("フィジカルAI"))
        self.assertEqual(normalize_term("えーあいガバナンス"), normalize_term("AIガバナンス"))
        self.assertEqual(normalize_term("エーアイ"), "ai")

    def test_long_vowel_mark_is_kept(self) -> None:
        self.assertNotEqual(normalize_term("シャドーAI"), normalize_term("シャドAI"))
        self.assertEqual(normalize_term("ハーネス"), "ハーネス")
        self.assertEqual(normalize_term("ソブリンAI"), "ソブリンai")

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(normalize_term(""), "")
        self.assertEqual(normalize_term("   "), "")
        self.assertEqual(normalize_term("・・"), "")


class TestFindDuplicate(unittest.TestCase):
    GROUPS = [{"フィジカルAI", "physical AI", "フィジカルエーアイ", "身体性AI"},
              {"AIエージェント", "AI agent", "AI agents"},
              {"マルチエージェント", "multi-agent"}]
    RECENT = [_entry("20260901", "フィジカルAI"), _entry("20260905", "OCR")]

    def test_exact_match_without_alias_groups(self) -> None:
        self.assertEqual(find_duplicate("フィジカルAI", self.RECENT), self.RECENT[0])
        self.assertEqual(find_duplicate("フィジカルAI", self.RECENT, []), self.RECENT[0])

    def test_notation_variants_match_the_alias_group(self) -> None:
        for variant in ["physical AI", "Physical AI", "ＰＨＹＳＩＣＡＬ　ＡＩ",
                        "フィジカルエーアイ", "身体性AI", "ふぃじかるえーあい"]:
            with self.subTest(variant=variant):
                self.assertEqual(find_duplicate(variant, self.RECENT, self.GROUPS),
                                 self.RECENT[0])

    def test_alias_is_found_in_either_direction(self) -> None:
        recent = [_entry("20260901", "physical AI")]
        self.assertEqual(find_duplicate("フィジカルAI", recent, self.GROUPS), recent[0])

    def test_notation_variants_without_alias_groups_do_not_match(self) -> None:
        """英語名・別の言い方は別名辞書があって初めて同一視される（辞書が無いと素通り）。"""
        self.assertIsNone(find_duplicate("physical AI", self.RECENT, []))
        self.assertIsNone(find_duplicate("身体性AI", self.RECENT, None))

    def test_different_terms_in_different_groups_do_not_match(self) -> None:
        recent = [_entry("20260901", "マルチエージェント")]
        self.assertIsNone(find_duplicate("AIエージェント", recent, self.GROUPS))
        self.assertIsNone(find_duplicate("フィジカルAI", recent, self.GROUPS))

    def test_unrelated_term_returns_none(self) -> None:
        self.assertIsNone(find_duplicate("ハルシネーション", self.RECENT, self.GROUPS))

    def test_returns_the_newest_when_several_match(self) -> None:
        recent = [_entry("20260901", "フィジカルAI"), _entry("20260915", "physical AI"),
                  _entry("20260910", "身体性AI")]
        self.assertEqual(find_duplicate("フィジカルAI", recent, self.GROUPS),
                         _entry("20260915", "physical AI"))

    def test_empty_term_and_empty_recent(self) -> None:
        self.assertIsNone(find_duplicate("", self.RECENT, self.GROUPS))
        self.assertIsNone(find_duplicate("・", self.RECENT, self.GROUPS))
        self.assertIsNone(find_duplicate("フィジカルAI", [], self.GROUPS))
        self.assertIsNone(find_duplicate("フィジカルAI", [_entry("20260901", "")], self.GROUPS))

    def test_overlapping_groups_are_treated_as_a_union(self) -> None:
        groups = [{"A語", "B語"}, {"B語", "C語"}]
        recent = [_entry("20260901", "C語")]
        self.assertEqual(find_duplicate("A語", recent, groups), recent[0])


class TestFindDuplicateWithBrackets(unittest.TestCase):
    """括弧つき表記（全体・括弧の外側・括弧の内側を比較候補にする）の素通りを防ぐ。"""

    def setUp(self) -> None:
        self.groups = load_alias_groups()   # 初期データの別名辞書

    def assertDup(self, term: str, past: str, groups=None) -> None:
        recent = [_entry("20260901", past)]
        self.assertEqual(find_duplicate(term, recent, self.groups if groups is None else groups),
                         recent[0], f"{term!r} は {past!r} と重複のはず")

    def assertNotDup(self, term: str, past: str, groups=None) -> None:
        recent = [_entry("20260901", past)]
        self.assertIsNone(find_duplicate(term, recent, self.groups if groups is None else groups),
                          f"{term!r} は {past!r} と別のはず")

    def test_term_with_alias_in_brackets_matches_the_plain_past_term(self) -> None:
        self.assertDup("フィジカルAI（身体性AI）", "フィジカルAI")
        self.assertDup("AIエージェント（AI Agent）", "AIエージェント")
        # 別名辞書なしでも、括弧の外側が一致するので重複
        self.assertDup("フィジカルAI（身体性AI）", "フィジカルAI", groups=[])
        self.assertDup("AIエージェント（AI Agent）", "AIエージェント", groups=[])

    def test_inner_part_matches_too(self) -> None:
        self.assertDup("身体性AI（フィジカルAI）", "フィジカルAI", groups=[])
        self.assertDup("AI Agent（AIエージェント）", "AIエージェント", groups=[])

    def test_past_term_with_brackets_matches_the_plain_new_term(self) -> None:
        self.assertDup("フィジカルAI", "フィジカルAI（身体性AI）")
        self.assertDup("AIエージェント", "AIエージェント（AI Agent）", groups=[])
        self.assertDup("身体性AI", "フィジカルAI（身体性AI）", groups=[])

    def test_all_bracket_kinds_are_recognized(self) -> None:
        for term in ["フィジカルAI（身体性AI）", "フィジカルAI(身体性AI)", "フィジカルAI［身体性AI］",
                     "フィジカルAI[身体性AI]", "フィジカルAI【身体性AI】", "フィジカルAI （身体性AI）"]:
            with self.subTest(term=term):
                self.assertDup(term, "フィジカルAI", groups=[])

    def test_alias_group_is_applied_to_the_bracket_candidates(self) -> None:
        """括弧の外側・内側が別名辞書のメンバーなら、同じグループの過去用語と重複。"""
        self.assertDup("身体性AI（Embodied AI）", "physical AI")      # 外側 → グループ
        self.assertDup("Embodied AI（フィジカルエーアイ）", "身体性AI")  # 内側 → グループ
        self.assertDup("ハーネス（Harness）", "エージェントハーネス")

    def test_alias_group_members_with_brackets_get_the_same_candidates(self) -> None:
        groups = [{"ソブリンAI（主権AI）", "sovereign AI"}]
        self.assertDup("主権AI", "sovereign AI", groups=groups)       # メンバーの括弧の内側
        self.assertDup("ソブリンAI", "sovereign AI", groups=groups)   # メンバーの括弧の外側
        self.assertDup("sovereign AI", "主権AI", groups=groups)
        self.assertNotDup("主権", "sovereign AI", groups=groups)

    def test_different_term_with_similar_english_is_not_a_duplicate(self) -> None:
        self.assertNotDup("マルチエージェント（Multi-Agent）", "AIエージェント")
        self.assertNotDup("マルチエージェント（Multi-Agent）", "AIエージェント（AI Agent）")
        self.assertDup("マルチエージェント（Multi-Agent）", "マルチエージェント")

    def test_short_bracket_fragments_do_not_cause_false_positives(self) -> None:
        # 括弧の中の「AI」（正規化後2文字）は候補にしない
        self.assertNotDup("エージェント（AI）", "AI")
        self.assertNotDup("AI", "エージェント（AI）")
        self.assertNotDup("AIエージェント（AI）", "AI")
        self.assertNotDup("エージェント（AI）", "AIガバナンス（AI）")
        # 全体の候補は長さに関わらず使う（従来どおり）
        self.assertDup("AI", "AI")
        self.assertDup("ＡＩ", "ai")
        self.assertDup("蒸留", "蒸留")

    def test_candidate_length_boundary_is_three_characters(self) -> None:
        self.assertDup("基盤モデル（LLM）", "LLM", groups=[])    # 3文字は候補
        self.assertDup("LLM（基盤モデル）", "LLM", groups=[])
        self.assertNotDup("基盤モデル（AI）", "ＡＩ", groups=[])  # 2文字は候補にしない

    def test_multiple_and_nested_brackets(self) -> None:
        self.assertDup("エージェント基盤（フィジカルAI）（シャドーAI）", "シャドーAI", groups=[])
        self.assertDup("エージェント基盤（フィジカルAI）（シャドーAI）", "フィジカルAI", groups=[])
        self.assertDup("基盤モデル（大規模（Large）言語モデル）", "Large", groups=[])
        # 入れ子の外側の括弧の中身・括弧を全部外した外側（2周目以降）も候補になる
        self.assertDup("基盤モデル（大規模（Large）言語モデル）", "大規模言語モデル", groups=[])
        self.assertDup("基盤モデル（大規模（Large）言語モデル）", "基盤モデル", groups=[])

    def test_empty_brackets_and_bracket_only_terms_do_not_crash(self) -> None:
        for term in ["（）", "()", "【】", "［］", "（ ）", "（（））", "（", "）", "()）（", "（・）"]:
            with self.subTest(term=term):
                self.assertIsNone(find_duplicate(term, [_entry("20260901", "フィジカルAI")], self.groups))
                self.assertIsNone(find_duplicate(term, [_entry("20260901", term)], self.groups))
                self.assertIsNone(find_duplicate("フィジカルAI（身体性AI）",
                                                 [_entry("20260901", term)], self.groups))
        self.assertDup("フィジカルAI（）", "フィジカルAI", groups=[])   # 空の括弧は無視されて外側が残る
        self.assertDup("（）フィジカルAI", "フィジカルAI", groups=[])

    def test_unbalanced_brackets_do_not_crash(self) -> None:
        for term in ["フィジカルAI（身体性", "身体性AI）フィジカルAI", "（フィジカルAI", "AI（（"]:
            with self.subTest(term=term):
                find_duplicate(term, [_entry("20260901", "フィジカルAI")], self.groups)

    def test_non_string_or_missing_terms_do_not_crash(self) -> None:
        recent = [{"date": "20260901", "term": None}, {"date": "20260902"}, _entry("20260903", "")]
        self.assertIsNone(find_duplicate("フィジカルAI（身体性AI）", recent, self.groups))
        self.assertIsNone(find_duplicate("None", recent, self.groups))

    def test_the_newest_date_is_returned_among_bracket_matches(self) -> None:
        recent = [_entry("20260901", "フィジカルAI"), _entry("20260920", "フィジカルAI（身体性AI）")]
        self.assertEqual(find_duplicate("フィジカルAI", recent, self.groups), recent[1])


class TestFindSimilar(unittest.TestCase):
    def test_containment_with_four_or_more_chars_is_similar(self) -> None:
        recent = [_entry("20260901", "エージェント")]
        self.assertEqual(find_similar("AIエージェント", recent), recent)

    def test_equal_terms_are_not_similar(self) -> None:
        self.assertEqual(find_similar("フィジカルAI", [_entry("20260901", "physical ai")]), [])
        self.assertEqual(find_similar("OCR", [_entry("20260901", "ocr")]), [])

    def test_short_contained_term_is_not_similar(self) -> None:
        # 短い方が3文字（ai / llm など）では何にでも含まれるので類似にしない
        self.assertEqual(find_similar("LLMの活用", [_entry("20260901", "LLM")]), [])
        self.assertEqual(find_similar("AIガバナンス", [_entry("20260901", "AI")]), [])

    def test_unrelated_is_not_similar(self) -> None:
        self.assertEqual(find_similar("ハルシネーション", [_entry("20260901", "OCR")]), [])

    def test_empty_inputs(self) -> None:
        self.assertEqual(find_similar("", [_entry("20260901", "OCR")]), [])
        self.assertEqual(find_similar("OCR", []), [])

    def test_results_are_newest_first(self) -> None:
        recent = [_entry("20260901", "エージェント"), _entry("20260920", "AIエージェント基盤")]
        got = find_similar("AIエージェント", recent)
        self.assertEqual([r["date"] for r in got], ["20260920", "20260901"])


class TestAliasesFor(unittest.TestCase):
    GROUPS = [{"フィジカルAI", "physical AI", "フィジカルエーアイ", "身体性AI"},
              {"ファインチューン", "fine-tuning", "fine tuning", "finetuning"}]

    def test_returns_other_notations_in_original_spelling(self) -> None:
        got = aliases_for("フィジカルAI", self.GROUPS)
        self.assertEqual(sorted(got), ["physical AI", "身体性AI"])

    def test_notation_equal_to_the_term_is_excluded(self) -> None:
        # 「フィジカルエーアイ」は正規化するとフィジカルAIと同じなので載せない
        self.assertNotIn("フィジカルエーアイ", aliases_for("フィジカルAI", self.GROUPS))
        self.assertNotIn("physical AI", aliases_for("Ｐｈｙｓｉｃａｌ　ＡＩ", self.GROUPS))

    def test_variants_that_normalize_the_same_are_listed_once(self) -> None:
        got = aliases_for("ファインチューン", self.GROUPS)
        self.assertEqual(len(got), 1)
        self.assertIn(got[0], {"fine-tuning", "fine tuning", "finetuning"})

    def test_term_not_in_any_group_returns_empty(self) -> None:
        self.assertEqual(aliases_for("OCR", self.GROUPS), [])
        self.assertEqual(aliases_for("OCR", []), [])
        self.assertEqual(aliases_for("OCR", None), [])
        self.assertEqual(aliases_for("", self.GROUPS), [])

    def test_order_is_deterministic(self) -> None:
        self.assertEqual(aliases_for("フィジカルAI", self.GROUPS),
                         aliases_for("フィジカルAI", [set(g) for g in self.GROUPS]))


class TestLoadAliasGroups(unittest.TestCase):
    def _load(self, text: str | None) -> list[set[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.yaml"
            if text is not None:
                path.write_text(text, encoding="utf-8")
            return load_alias_groups(path)

    def test_loads_groups_as_sets(self) -> None:
        got = self._load("groups:\n  - [フィジカルAI, physical AI]\n  - [トークン, token, tokens]\n")
        self.assertEqual(got, [{"フィジカルAI", "physical AI"}, {"トークン", "token", "tokens"}])

    def test_missing_file_returns_empty_list_without_raising(self) -> None:
        self.assertEqual(self._load(None), [])

    def test_broken_yaml_returns_empty_list_without_raising(self) -> None:
        self.assertEqual(self._load("groups: [unclosed\n  - : :\n"), [])

    def test_wrong_shapes_return_empty_list(self) -> None:
        self.assertEqual(self._load(""), [])
        self.assertEqual(self._load("- a\n- b\n"), [])
        self.assertEqual(self._load("groups: 123\n"), [])
        self.assertEqual(self._load("groups:\n"), [])

    def test_empty_and_malformed_groups_are_skipped(self) -> None:
        got = self._load("groups:\n  - []\n  - not-a-list\n  - [a, '', null, b]\n  - [c]\n")
        self.assertEqual(got, [{"a", "b"}, {"c"}])

    def test_non_string_members_do_not_crash(self) -> None:
        got = self._load("groups:\n  - [2025, true, x]\n")
        self.assertEqual(got, [{"2025", "x"}])

    def _load_with_output(self, text: str | None = None, **patches):
        """読み込み結果と、標準出力に出た [warn] 行を返す。"""
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.yaml"
            path.write_text(text if text is not None else "groups:\n  - [a, b]\n", encoding="utf-8")
            with redirect_stdout(buf):
                if "safe_load" in patches:
                    with patch("src.glossary_terms.yaml.safe_load", side_effect=patches["safe_load"]):
                        got = load_alias_groups(path)
                else:
                    got = load_alias_groups(path)
        warns = [ln for ln in buf.getvalue().splitlines() if ln.startswith("[warn]")]
        return got, warns

    def test_any_exception_from_the_parser_returns_empty_list_with_one_warning(self) -> None:
        for exc in [ValueError("bad"), RecursionError("deep"), TypeError("t"), KeyError("k"),
                    AttributeError("a"), RuntimeError("r"), MemoryError, UnicodeDecodeError(
                        "utf-8", b"\xff", 0, 1, "invalid")]:
            with self.subTest(exc=type(exc).__name__ if not isinstance(exc, type) else exc.__name__):
                got, warns = self._load_with_output(safe_load=exc)
                self.assertEqual(got, [])
                self.assertEqual(len(warns), 1)

    def test_deeply_nested_yaml_raises_recursion_internally_but_is_swallowed(self) -> None:
        got, warns = self._load_with_output("groups: " + "[" * 3000 + "]" * 3000 + "\n")
        self.assertEqual(got, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("RecursionError", warns[0])

    def test_unreadable_files_return_empty_list_with_one_warning(self) -> None:
        # ディレクトリを渡す（読み込みでOSError）・不正なバイト列（UnicodeDecodeError）
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(buf):
            self.assertEqual(load_alias_groups(Path(tmp)), [])
            bad = Path(tmp) / "bad.yaml"
            bad.write_bytes(b"groups:\n  - [\xff\xfe, b]\n")
            self.assertEqual(load_alias_groups(bad), [])
        self.assertEqual(len([ln for ln in buf.getvalue().splitlines() if ln.startswith("[warn]")]), 2)

    def test_structurally_invalid_yaml_never_raises(self) -> None:
        cases = {
            "groups: foo\n": [],                                  # groupsがリストでない
            "groups: {a: [x, y]}\n": [],
            "groups: [foo, bar]\n": [],                           # 要素が文字列（リストでない）
            "groups: [1, 2, 3]\n": [],
            "groups: [[{a: 1}, [x], {}, null, true]]\n": [],      # 要素が文字列でない
            "groups: [[a, b], 5, null, {x: y}, [{k: v}, c]]\n": [{"a", "b"}, {"c"}],
            "groups: [[a, [b, c]]]\n": [{"a"}],
            "{}\n": [],
            "just a string\n": [],
            "null\n": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                got, _ = self._load_with_output(text)
                self.assertEqual(got, expected)

    def test_default_dictionary_loads_and_has_the_physical_ai_group(self) -> None:
        groups = load_alias_groups()
        self.assertGreater(len(groups), 0)
        self.assertTrue(all(isinstance(g, set) and g for g in groups))
        recent = [_entry("20260901", "フィジカルAI")]
        for variant in ["physical AI", "Physical AI", "ＰＨＹＳＩＣＡＬ　ＡＩ", "フィジカルエーアイ"]:
            with self.subTest(variant=variant):
                self.assertEqual(find_duplicate(variant, recent, groups), recent[0])

    def test_default_dictionary_keeps_related_but_different_terms_apart(self) -> None:
        groups = load_alias_groups()
        recent = [_entry("20260901", "マルチエージェント")]
        self.assertIsNone(find_duplicate("AIエージェント", recent, groups))
        self.assertIsNone(find_duplicate("ローカルAIエージェント",
                                         [_entry("20260901", "AIエージェント")], groups))

    def test_default_dictionary_members_do_not_collide_across_groups(self) -> None:
        """同じ正規化語が複数グループに現れると和集合で統合されてしまう。初期データは
        それが起きない（起きるなら別の用語を同一視している疑いがある）。"""
        owner: dict[str, int] = {}
        for i, g in enumerate(load_alias_groups()):
            for m in g:
                n = normalize_term(m)
                self.assertTrue(n, f"空に正規化される表記: {m!r}")
                self.assertEqual(owner.setdefault(n, i), i, f"グループ間で重複: {m!r}")


if __name__ == "__main__":
    unittest.main()
