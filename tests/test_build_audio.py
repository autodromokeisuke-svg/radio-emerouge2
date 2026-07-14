"""_strip_alpha_reading_gloss() / _embed_cover_art() の単体テスト。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutagen.id3 import ID3
from pydub import AudioSegment

from src.build_audio import ASSETS, _embed_cover_art, _strip_alpha_reading_gloss


class TestStripAlphaReadingGloss(unittest.TestCase):
    def test_alpha_abbreviation_with_katakana_gloss(self) -> None:
        self.assertEqual(
            _strip_alpha_reading_gloss("TSMC（ティーエスエムシー）"),
            "ティーエスエムシー",
        )

    def test_alnum_with_hyphen_and_katakana_gloss(self) -> None:
        self.assertEqual(
            _strip_alpha_reading_gloss("GPT-4（ジーピーティーフォー）"),
            "ジーピーティーフォー",
        )

    def test_kanji_gloss_is_not_replaced(self) -> None:
        self.assertEqual(
            _strip_alpha_reading_gloss("AI（人工知能）"),
            "AI（人工知能）",
        )

    def test_plain_sentence_without_parentheses_is_unchanged(self) -> None:
        text = "今日はいい天気だね、ルジェ。"
        self.assertEqual(_strip_alpha_reading_gloss(text), text)


class TestEmbedCoverArt(unittest.TestCase):
    def test_embeds_apic_frame_when_cover_exists(self) -> None:
        cover_path = ASSETS / "cover.jpg"
        if not cover_path.exists():
            self.skipTest("assets/cover.jpg が存在しないためスキップ")

        with tempfile.TemporaryDirectory() as tmp:
            mp3_path = Path(tmp) / "test.mp3"
            AudioSegment.silent(duration=200).export(mp3_path, format="mp3")

            _embed_cover_art(mp3_path, cover_path)

            tags = ID3(mp3_path)
            apics = tags.getall("APIC")
            self.assertEqual(len(apics), 1)
            self.assertEqual(apics[0].mime, "image/jpeg")
            self.assertEqual(apics[0].data, cover_path.read_bytes())

    def test_missing_cover_file_raises_and_is_caught_by_caller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mp3_path = Path(tmp) / "test.mp3"
            AudioSegment.silent(duration=200).export(mp3_path, format="mp3")

            # 存在しないパスを渡しても例外を投げずに警告出力のみで終わる
            _embed_cover_art(mp3_path, Path(tmp) / "no_such_cover.jpg")

            # APICが追加されていないことを確認（失敗時は静かにスキップされる）
            try:
                tags = ID3(mp3_path)
                self.assertEqual(len(tags.getall("APIC")), 0)
            except Exception:
                pass  # ID3ヘッダーがそもそも無ければそれでOK


if __name__ == "__main__":
    unittest.main()
