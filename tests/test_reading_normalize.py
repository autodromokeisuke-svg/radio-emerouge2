import unittest

from src.build_audio import _fix_known_misreadings
from src.reading_normalize import normalize_for_tts


class TestNormalizeForTts(unittest.TestCase):

    def test_latin_then_kana_gloss_at_sentence_boundary(self) -> None:
        self.assertEqual(normalize_for_tts("RAG（ラグ）が便利"), "ラグが便利")

    def test_latin_then_kana_gloss_after_hiragana(self) -> None:
        self.assertEqual(normalize_for_tts("新しいLLM（エルエルエム）の話"),
                          "新しいエルエルエムの話")

    def test_multi_char_latin_token_with_symbols(self) -> None:
        self.assertEqual(normalize_for_tts("GPT-5.5（ジーピーティーごーてんご）"),
                          "ジーピーティーごーてんご")

    def test_multi_word_latin_token(self) -> None:
        self.assertEqual(normalize_for_tts("Claude Code（クロードコード）で"),
                          "クロードコードで")

    def test_half_width_parens_with_spaces_around(self) -> None:
        self.assertEqual(normalize_for_tts("LLM (エルエルエム) が"), "エルエルエムが")

    def test_latin_after_kanji_keeps_token_drops_paren_only(self) -> None:
        # 複合語の一部の可能性（直前が漢字）→ 表記は残し、カッコだけ消す
        self.assertEqual(normalize_for_tts("生成AI（エーアイ）"), "生成AI")

    def test_katakana_then_latin_spelling_gloss(self) -> None:
        self.assertEqual(normalize_for_tts("オープンエーアイ（OpenAI）が"),
                          "オープンエーアイが")

    def test_unchanged_when_gloss_contains_kanji(self) -> None:
        self.assertEqual(normalize_for_tts("LLM（大規模言語モデル）"),
                          "LLM（大規模言語モデル）")

    def test_unchanged_laughing_note(self) -> None:
        self.assertEqual(normalize_for_tts("すごいね（笑）"), "すごいね（笑）")

    def test_unchanged_bare_note(self) -> None:
        self.assertEqual(normalize_for_tts("（注）"), "（注）")

    def test_unchanged_when_no_gloss_follows(self) -> None:
        self.assertEqual(normalize_for_tts("RAGは便利"), "RAGは便利")

    def test_unchanged_empty_string(self) -> None:
        self.assertEqual(normalize_for_tts(""), "")

    def test_two_occurrences_in_one_line_both_normalized(self) -> None:
        self.assertEqual(
            normalize_for_tts("RAG（ラグ）とLLM（エルエルエム）の話"),
            "ラグとエルエルエムの話",
        )

    # --- build_audio._strip_alpha_reading_gloss から移設（役割をnormalize_for_ttsへ一本化） ---

    def test_alpha_abbreviation_with_katakana_gloss(self) -> None:
        self.assertEqual(
            normalize_for_tts("TSMC（ティーエスエムシー）"),
            "ティーエスエムシー",
        )

    def test_alnum_with_hyphen_and_katakana_gloss(self) -> None:
        self.assertEqual(
            normalize_for_tts("GPT-4（ジーピーティーフォー）"),
            "ジーピーティーフォー",
        )

    def test_kanji_gloss_is_not_replaced(self) -> None:
        self.assertEqual(
            normalize_for_tts("AI（人工知能）"),
            "AI（人工知能）",
        )

    def test_plain_sentence_without_parentheses_is_unchanged(self) -> None:
        text = "今日はいい天気だね、ルジェ。"
        self.assertEqual(normalize_for_tts(text), text)

    # --- 数字だけの語を含む複数語トークン（Gemini 3 / Sora 2 / Llama 4 Scout 等） ---

    def test_multi_word_token_with_trailing_number_word(self) -> None:
        self.assertEqual(
            normalize_for_tts("Gemini 3（ジェミニスリー）が"),
            "ジェミニスリーが",
        )

    def test_multi_word_token_with_trailing_single_digit(self) -> None:
        self.assertEqual(
            normalize_for_tts("Sora 2（ソラツー）"),
            "ソラツー",
        )

    def test_multi_word_token_with_number_word_in_middle(self) -> None:
        self.assertEqual(
            normalize_for_tts("Llama 4 Scout（ラマフォースカウト）"),
            "ラマフォースカウト",
        )

    def test_single_word_letter_then_digit_unaffected_by_extension(self) -> None:
        # "o3" のような1語内の英字+数字は元々1つのLATIN_WORDとしてマッチ済み
        self.assertEqual(normalize_for_tts("o3（オースリー）"), "オースリー")

    def test_number_only_word_not_matched_as_first_word(self) -> None:
        # 先頭語が数字始まり（漢字が続く）の場合は対象外のまま
        self.assertEqual(
            normalize_for_tts("2025年（令和7年）"),
            "2025年（令和7年）",
        )

    def test_iphone_model_number_with_kana_gloss(self) -> None:
        # 数字語を含む複数語トークンの拡張により、カッコ内カナへ置換される
        self.assertEqual(
            normalize_for_tts("iPhone 17（アイフォーン）の"),
            "アイフォーンの",
        )

    # --- build_audioの前処理チェーン全体（normalize_for_tts → _fix_known_misreadings）を通した回帰 ---

    def test_full_chain_claude_code_gloss(self) -> None:
        text = "Claude Code（クロードコード）で"
        self.assertEqual(
            _fix_known_misreadings(normalize_for_tts(text)),
            "クロードコードで",
        )


if __name__ == "__main__":
    unittest.main()
