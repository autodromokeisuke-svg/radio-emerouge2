"""tools/reupload_youtube.py の単体テスト（YouTubeのAPIは呼ばない）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import reupload_youtube as ry  # noqa: E402


class TestBuildDescription(unittest.TestCase):
    def test_matches_daily_format(self) -> None:
        meta = {"description": "今日の話題: A / B"}
        d = ry.build_description(meta, {"credit": "音声: X"}, "https://o.github.io/r/")
        self.assertEqual(d, "今日の話題: A / B\n\n音声: X\n\nhttps://o.github.io/r/feed.xml")


class TestMain(unittest.TestCase):
    def _site(self, tmp: str, with_mp3: bool = True) -> str:
        ep = Path(tmp) / "episodes"
        ep.mkdir()
        (ep / "radio-20261022.json").write_text(json.dumps(
            {"title": "T", "description": "D", "file": "radio-20261022.mp3"}), encoding="utf-8")
        if with_mp3:
            (ep / "radio-20261022.mp3").write_bytes(b"x")
        return tmp

    def _run(self, argv: list[str], upload_return) -> tuple[int, list]:
        calls: list = []

        def fake_upload(mp3, title, description, cfg):
            calls.append((Path(mp3).name, title, description))
            return upload_return

        with patch.object(sys, "argv", ["x", *argv]), \
             patch.object(ry, "upload_to_youtube", side_effect=fake_upload):
            return ry.main(), calls

    def test_uploads_with_episode_title_and_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, calls = self._run(["--date", "20261022", "--base-url", "https://o.github.io/r",
                                     "--site", self._site(tmp)], "vid123")
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][:2], ("radio-20261022.mp3", "T"))
        self.assertTrue(calls[0][2].startswith("D\n\n"))
        self.assertTrue(calls[0][2].endswith("https://o.github.io/r/feed.xml"))

    def test_upload_failure_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self._run(["--date", "20261022", "--base-url", "https://o/r",
                                 "--site", self._site(tmp)], None)
        self.assertEqual(code, 1)

    def test_bad_date_and_missing_files_do_not_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = self._site(tmp, with_mp3=False)
            for argv in (["--date", "2026-10-22"], ["--date", "20261022"], ["--date", "20261001"]):
                code, calls = self._run([*argv, "--base-url", "https://o/r", "--site", site], "v")
                self.assertEqual(code, 2, argv)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
