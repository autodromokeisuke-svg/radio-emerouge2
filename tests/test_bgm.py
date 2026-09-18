"""src/bgm.py（番組BGMの区間計画・ループ・音量整形・ミキシング）の単体テスト。

実素材（数MBのmp3）は使わず、pydubで合成した短い音で検証する。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydub import AudioSegment
from pydub.generators import Sine

from src import bgm
from src.bgm import build_loop, load_track, mix_bgm, plan_spans, resolve_config, shape_span


def _tone(ms: int, db: float = -14.0, freq: int = 440) -> AudioSegment:
    """指定の長さ・おおよその音量の正弦波（ステレオ、44.1kHz）。"""
    return Sine(freq).to_audio_segment(duration=ms).set_channels(2).apply_gain(db)


class TestPlanSpans(unittest.TestCase):
    def test_valid_labels_return_boundaries(self) -> None:
        sections = ["opening", "opening", "news", "news", "glossary", "ending", "ending"]
        self.assertEqual(plan_spans(sections), {"news_first": 2, "ending_first": 5})

    def test_glossary_is_optional(self) -> None:
        sections = ["opening", "news", "news", "ending"]
        self.assertEqual(plan_spans(sections), {"news_first": 1, "ending_first": 3})

    def test_empty_or_missing_label_is_rejected(self) -> None:
        self.assertIsNone(plan_spans([]))
        self.assertIsNone(plan_spans(["opening", "", "news", "ending"]))

    def test_unknown_label_is_rejected(self) -> None:
        self.assertIsNone(plan_spans(["opening", "intro", "news", "ending"]))

    def test_going_backwards_is_rejected(self) -> None:
        """glossaryのあとにnewsへ戻るなど、順序が逆戻りする台本はBGM区間を決められない。"""
        self.assertIsNone(plan_spans(["opening", "news", "glossary", "news", "ending"]))
        self.assertIsNone(plan_spans(["news", "opening", "ending"]))

    def test_required_sections_must_be_present(self) -> None:
        self.assertIsNone(plan_spans(["news", "news", "ending"]))      # openingなし
        self.assertIsNone(plan_spans(["opening", "opening", "ending"]))  # newsなし
        self.assertIsNone(plan_spans(["opening", "news", "glossary"]))   # endingなし


class TestBuildLoop(unittest.TestCase):
    def test_result_has_exactly_the_requested_length(self) -> None:
        track = _tone(20_000)
        for want in (5_000, 20_000, 47_000, 120_000):
            out = build_loop(track, want, head_ms=3000, tail_ms=3000, crossfade_ms=1000)
            self.assertLessEqual(abs(len(out) - want), 2, f"want={want} got={len(out)}")

    def test_loops_when_track_is_shorter_than_span(self) -> None:
        """曲より長い区間でも、曲を繋いで最後まで音が途切れない。"""
        track = _tone(10_000)
        out = build_loop(track, 60_000, head_ms=2000, tail_ms=2000, crossfade_ms=500)
        # 末尾近くまで無音になっていない
        self.assertGreater(out[-3000:].dBFS, -30)

    def test_short_track_falls_back_without_error(self) -> None:
        """切り出すと短すぎる曲でも例外にならず、指定の長さになる。"""
        track = _tone(3_000)
        out = build_loop(track, 20_000, head_ms=8000, tail_ms=8000, crossfade_ms=3000)
        self.assertLessEqual(abs(len(out) - 20_000), 2)

    def test_zero_length_returns_empty(self) -> None:
        self.assertEqual(len(build_loop(_tone(5000), 0, 1000, 1000, 500)), 0)


class TestShapeSpan(unittest.TestCase):
    KW = dict(solo_db=-8.0, bed_db=-17.0, duck_ms=1000, fade_in_ms=2000, fade_out_ms=2000)

    def test_fades_in_and_out(self) -> None:
        bed = _tone(30_000)
        out = shape_span(bed, 30_000, lead_in_ms=0, tail_ms=0, **self.KW)
        self.assertLess(out[:300].dBFS, out[10_000:11_000].dBFS - 6)   # 頭は小さい
        self.assertLess(out[-300:].dBFS, out[10_000:11_000].dBFS - 6)  # 終わりも小さい

    def test_lead_in_is_louder_than_bed_under_voice(self) -> None:
        """冒頭の曲だけの区間は、声が始まったあとの敷き音量より大きい。"""
        bed = _tone(30_000)
        out = shape_span(bed, 30_000, lead_in_ms=6000, tail_ms=0, **self.KW)
        solo = out[3000:5000].dBFS
        under_voice = out[15_000:17_000].dBFS
        self.assertGreater(solo - under_voice, 6)   # 設定差は9dB

    def test_tail_returns_to_solo_level(self) -> None:
        bed = _tone(30_000)
        out = shape_span(bed, 30_000, lead_in_ms=0, tail_ms=8000, **self.KW)
        under_voice = out[10_000:12_000].dBFS
        tail_solo = out[24_000:25_000].dBFS   # 声の終わり(22s)+ダッキング後、フェードアウト前
        self.assertGreater(tail_solo - under_voice, 6)

    def test_length_is_preserved(self) -> None:
        out = shape_span(_tone(30_000), 25_000, lead_in_ms=3000, tail_ms=3000, **self.KW)
        self.assertLessEqual(abs(len(out) - 25_000), 2)

    def test_very_short_span_does_not_crash(self) -> None:
        out = shape_span(_tone(3000), 1500, lead_in_ms=1000, tail_ms=1000, **self.KW)
        self.assertLessEqual(abs(len(out) - 1500), 2)


class TestIntroBoostAndSwell(unittest.TestCase):
    """冒頭は曲の小さいイントロを持ち上げて聞こえるようにし、盛り上がり（本体）に入る
    手前で元の音量へ戻す（本体まで持ち上げるとクリップするため）。
    """

    KW = dict(solo_db=0.0, bed_db=-8.0, duck_ms=1000, fade_in_ms=500, fade_out_ms=500)

    def _track_with_soft_intro(self) -> AudioSegment:
        """小さいイントロ(8秒,-30dB)＋大きい本体(22秒,-10dB)の合成トラック。"""
        return _tone(8000, db=-30.0).append(_tone(22000, db=-10.0), crossfade=0)

    def test_intro_is_boosted_but_body_is_not(self) -> None:
        bed = self._track_with_soft_intro()
        plain = shape_span(bed, 30_000, lead_in_ms=9000, tail_ms=0, intro_boost_db=0.0, **self.KW)
        boosted = shape_span(bed, 30_000, lead_in_ms=9000, tail_ms=0, intro_boost_db=10.0,
                             swell_at_ms=8000, swell_ms=1000, **self.KW)
        # イントロ(3〜6秒)は約10dB大きくなる
        self.assertGreater(boosted[3000:6000].dBFS - plain[3000:6000].dBFS, 8)
        # 本体に入った直後(8.2〜8.8秒)は持ち上げが戻っていて、ほぼ同じ
        self.assertLess(abs(boosted[8200:8800].dBFS - plain[8200:8800].dBFS), 2)

    def test_swell_does_not_clip(self) -> None:
        """本体は0dBFS近くまで大きい曲でも、イントロの持ち上げがクリップを起こさない。"""
        bed = _tone(8000, db=-25.0).append(_tone(22000, db=-1.0), crossfade=0)
        out = shape_span(bed, 30_000, lead_in_ms=9000, tail_ms=0, intro_boost_db=9.0,
                         swell_at_ms=8000, swell_ms=1000, **self.KW)
        self.assertLess(out.max_dBFS, 0.5)

    def test_voice_start_ducks_the_music(self) -> None:
        bed = self._track_with_soft_intro()
        out = shape_span(bed, 30_000, lead_in_ms=9000, tail_ms=0, intro_boost_db=0.0, **self.KW)
        swell = out[8300:8900].dBFS      # 盛り上がり(声の前): 曲だけの音量
        under = out[15_000:16_000].dBFS  # 声が始まって十分あと: 敷き音量
        self.assertGreater(swell - under, 5)   # 設定差は8dB


class TestBuildLoopSkipsIntro(unittest.TestCase):
    def _track(self) -> AudioSegment:
        return _tone(8000, db=-30.0).append(_tone(22000, db=-10.0), crossfade=0)

    def test_use_intro_true_starts_soft(self) -> None:
        out = build_loop(self._track(), 20_000, head_ms=8000, tail_ms=2000, crossfade_ms=500,
                         use_intro=True)
        self.assertLess(out[2000:4000].dBFS, -25)

    def test_use_intro_false_starts_at_body(self) -> None:
        out = build_loop(self._track(), 20_000, head_ms=8000, tail_ms=2000, crossfade_ms=500,
                         use_intro=False)
        self.assertGreater(out[2000:4000].dBFS, -15)


class TestMixBgm(unittest.TestCase):
    """区間ごとに正しい曲が正しい位置で鳴ること。

    メイン曲=440Hz、ニュース曲=880Hzの正弦波にして、区間内の周波数成分の違いで
    どちらが鳴っているかを判定する。声の代わりに小さな低い音（100Hz）を全体に敷く。
    """

    def _cfg(self) -> dict:
        cfg = resolve_config({})
        cfg.update(lead_in_ms=2000, tail_ms=3000, fade_in_ms=1000, fade_out_ms=1000,
                   duck_ms=500, loop_head_ms=1000, loop_tail_ms=1000, loop_crossfade_ms=300,
                   swell_ms=400, intro_boost_db=0.0)
        return cfg

    def _dominant(self, seg: AudioSegment) -> int:
        """区間内で強い周波数（440 or 880）を返す。"""
        mono = seg.set_channels(1)
        # 440Hzと880Hzの帯域の強さを、簡易に比較する（ローパス/ハイパスでの音量差）
        low = mono.low_pass_filter(600).dBFS
        high = mono.high_pass_filter(700).dBFS
        return 440 if low > high else 880

    def test_each_section_gets_its_own_track(self) -> None:
        cfg = self._cfg()
        lead, tail = cfg["lead_in_ms"], cfg["tail_ms"]
        # 声のタイムライン: 前 2s 無音 / 10s opening / 20s news(+glossary) / 10s ending / 後 3s 無音
        total = lead + 10_000 + 20_000 + 10_000 + tail
        show = AudioSegment.silent(duration=total)
        starts = [lead, lead + 10_000, lead + 20_000, lead + 30_000]  # 4セリフ想定
        plan = {"news_first": 1, "ending_first": 3}
        out = mix_bgm(show, plan, starts, _tone(30_000, freq=440), _tone(30_000, freq=880),
                      cfg, tail)
        self.assertLessEqual(abs(len(out) - total), 3)
        self.assertEqual(self._dominant(out[4_000:9_000]), 440)      # opening
        self.assertEqual(self._dominant(out[lead + 14_000:lead + 26_000]), 880)  # news〜glossary
        self.assertEqual(self._dominant(out[lead + 31_000:lead + 38_000]), 440)  # ending

    def test_music_is_silent_at_section_boundaries(self) -> None:
        """区間の切れ目（1本目のニュース開始・エンディング開始）では、直前の曲がフェードアウト済み。"""
        cfg = self._cfg()
        lead, tail = cfg["lead_in_ms"], cfg["tail_ms"]
        total = lead + 40_000 + tail
        show = AudioSegment.silent(duration=total)
        starts = [lead, lead + 10_000, lead + 30_000]
        out = mix_bgm(show, {"news_first": 1, "ending_first": 2}, starts,
                      _tone(30_000), _tone(30_000, freq=880), cfg, tail)
        b = lead + 10_000
        mid = out[lead + 5_000:lead + 6_000].dBFS
        edge = out[b - 300:b].dBFS   # opening曲の終わり際 = フェードアウトの最後
        self.assertLess(edge, mid - 6)

    def test_output_does_not_clip(self) -> None:
        cfg = self._cfg()
        lead, tail = cfg["lead_in_ms"], cfg["tail_ms"]
        total = lead + 30_000 + tail
        voice = Sine(200).to_audio_segment(duration=total).set_channels(1).apply_gain(-16)
        starts = [lead, lead + 10_000, lead + 20_000]
        # 実際の曲（平均約-14dBFS、ピーク約0dBFS）に近い、ピークが-3dBFSの曲で検証する。
        # 曲だけの区間（solo_db=0）は原音のまま鳴るため、ピーク0dBFSの曲だとそれ自体が
        # 0dBFSに達する。それは曲の側の性質で、ミキシングが足したものではない
        out = mix_bgm(voice, {"news_first": 1, "ending_first": 2}, starts,
                      _tone(30_000, db=-3.0), _tone(30_000, db=-3.0, freq=880), cfg, tail)
        self.assertLess(out.max_dBFS, -0.5)


class TestPerTrackBedLevel(unittest.TestCase):
    """メイン曲とニュース曲で、声の下に敷く音量を別々に設定できること
    （聴き比べで、メインはM案・ニュースはS案が丁度良いと確定したため）。"""

    def _mix(self, bed_db: float, news_bed_db: float) -> AudioSegment:
        cfg = resolve_config({})
        cfg.update(lead_in_ms=1000, tail_ms=1000, fade_in_ms=500, fade_out_ms=500,
                   duck_ms=300, loop_head_ms=500, loop_tail_ms=500, loop_crossfade_ms=200,
                   swell_ms=200, intro_boost_db=0.0, bed_db=bed_db, news_bed_db=news_bed_db)
        total = 1000 + 30_000 + 1000
        show = AudioSegment.silent(duration=total)
        starts = [1000, 11_000, 21_000]   # opening 10s / news 10s / ending 10s
        return mix_bgm(show, {"news_first": 1, "ending_first": 2}, starts,
                       _tone(30_000, freq=440), _tone(30_000, freq=880), cfg, 1000)

    def test_news_track_uses_its_own_level(self) -> None:
        out = self._mix(bed_db=-8.0, news_bed_db=-14.0)
        main_level = out[4_000:8_000].dBFS      # opening（メイン曲）
        news_level = out[14_000:18_000].dBFS    # news（ニュース曲）
        self.assertGreater(main_level - news_level, 4)   # 設定差は6dB

    def test_news_bed_defaults_to_bed_when_missing(self) -> None:
        """news_bed_dbが無い古い設定でも、bed_dbと同じ音量で動く。"""
        cfg = resolve_config({})
        cfg.update(lead_in_ms=1000, tail_ms=1000, fade_in_ms=500, fade_out_ms=500,
                   duck_ms=300, loop_head_ms=500, loop_tail_ms=500, loop_crossfade_ms=200,
                   swell_ms=200, intro_boost_db=0.0, bed_db=-8.0)
        del cfg["news_bed_db"]
        show = AudioSegment.silent(duration=32_000)
        out = mix_bgm(show, {"news_first": 1, "ending_first": 2}, [1000, 11_000, 21_000],
                      _tone(30_000, freq=440), _tone(30_000, freq=440), cfg, 1000)
        self.assertLess(abs(out[4_000:8_000].dBFS - out[14_000:18_000].dBFS), 2)


class TestLoadTrack(unittest.TestCase):
    def test_missing_file_returns_none_without_raising(self) -> None:
        self.assertIsNone(load_track("assets/does_not_exist.mp3"))


class TestResolveConfig(unittest.TestCase):
    def test_defaults_fill_missing_keys(self) -> None:
        cfg = resolve_config({"bed_db": -20})
        self.assertEqual(cfg["bed_db"], -20)
        self.assertEqual(cfg["fade_in_ms"], bgm.DEFAULTS["fade_in_ms"])
        self.assertTrue(cfg["enabled"])

    def test_none_uses_defaults(self) -> None:
        self.assertEqual(resolve_config(None), bgm.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
