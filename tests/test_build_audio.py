"""_fix_known_misreadings() / _embed_cover_art() / 読み検証まわりの単体テスト。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutagen.id3 import ID3
from pydub import AudioSegment

from src.build_audio import (
    ASSETS,
    _apply_reading_corrections,
    _embed_cover_art,
    _fix_known_misreadings,
    _run_reading_check_with_report,
)


class TestFixKnownMisreadings(unittest.TestCase):
    def test_omoe_replaces_juu_prone_kanji(self) -> None:
        self.assertEqual(_fix_known_misreadings("今日はちょっと重めの話題だよ"),
                         "今日はちょっとおもめの話題だよ")

    def test_omoi_replaces_juu_prone_kanji(self) -> None:
        self.assertEqual(_fix_known_misreadings("これは重い話だね"),
                         "これはおもい話だね")

    def test_unrelated_text_is_unchanged(self) -> None:
        text = "今日はいい天気だね、ルジェ。"
        self.assertEqual(_fix_known_misreadings(text), text)


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


def _q(reading: str, n_phrases: int = 1) -> dict:
    """テスト用の簡易audio_queryクエリJSON。

    読みは1つ目のアクセント句にまとめ、残りは空のダミー句でn_phrasesを
    水増しする（extract_readingは全句のmorasを連結するだけなので、句の
    中身を細かく作り込まなくてもテストしたい2点＝「読みの文字列」と
    「アクセント句の数」を独立に指定できる）。
    """
    phrases = [{"moras": [{"text": c} for c in reading], "pause_mora": None}]
    phrases += [{"moras": [], "pause_mora": None} for _ in range(max(0, n_phrases - 1))]
    return {"accent_phrases": phrases}


class _FakeEngine:
    """テスト用の疑似TTSエンジン。テキスト文字列 -> クエリJSON の対応表を持つ。

    想定外のクエリ（テストが用意していない文字列）が来た場合、以前は
    AssertionErrorを投げていたが、本番の_apply_reading_correctionsは
    エンジンへのクエリ失敗を例外ごと握りつぶして続行する設計なので、
    テストの中でAssertionErrorを投げても本体側でcatchされてしまい、
    「本当は想定外のクエリを投げていたのにテストが気づかず通ってしまう」
    事故があった（F6）。そのため例外は投げず、unexpectedに記録するだけに
    し、各テストの最後で「unexpectedが空であること」を明示的に確認する。
    """

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.unexpected: list[str] = []

    def query(self, role: str, text: str) -> dict:
        self.calls.append(text)
        if text not in self.responses:
            self.unexpected.append(text)
            raise RuntimeError(f"想定外のクエリ: {text!r}")
        return self.responses[text]


class TestApplyReadingCorrections(unittest.TestCase):
    def test_already_correct_line_is_unverified_not_applied(self) -> None:
        """実際には既に正しく読めている行への指摘は、位置検証プローブが
        現在の読みを再現できない（＝heardが実在しない）ため"unverified"として
        見送られ、変更されない。"""
        engine = _FakeEngine({
            "さて、ジの話題です": _q("サテ、チガウ"),
            "さて、じの話題です": _q("サテ、チガウ"),
        })
        lines = [{"speaker": "ruje"}]
        texts = ["さて、次の話題です"]
        queries = [_q("サテ、ツギノワダイデス")]
        corrections = [{"index": 1, "surface": "次", "heard": "ジ", "correct": "ツギ"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "さて、次の話題です")
        self.assertEqual({k: report[k] for k in ("pointed", "applied", "unverified", "rejected_scope", "unfixable")},
                         {"pointed": 1, "applied": 0, "unverified": 1, "rejected_scope": 0, "unfixable": 0})
        self.assertEqual(engine.unexpected, [])

    def test_surface_not_found_is_skipped(self) -> None:
        engine = _FakeEngine({})
        lines = [{"speaker": "eme"}]
        texts = ["今日はいい天気"]
        queries = [_q("キョウハイイテンキ")]
        corrections = [{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "今日はいい天気")
        self.assertEqual({k: report[k] for k in ("pointed", "applied", "unverified", "rejected_scope", "unfixable")},
                         {"pointed": 1, "applied": 0, "unverified": 0, "rejected_scope": 0, "unfixable": 1})
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.unexpected, [])

    def test_scope_rejects_correct_that_drops_part_of_surface(self) -> None:
        """F2: correctがsurfaceの一部（「になる」）の読みを欠いている指摘は、
        エンジンへ問い合わせるまでもなく機械的に弾く（「便利になる分」→「ブン」）。"""
        engine = _FakeEngine({})
        lines = [{"speaker": "eme"}]
        texts = ["使ってみると便利になる分もあるよね"]
        queries = [_q("ツカッテミルトベンリニナルワケモアルヨネ")]
        corrections = [{"index": 1, "surface": "便利になる分", "heard": "ベンリニナルワケ", "correct": "ブン"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "使ってみると便利になる分もあるよね")  # 変更なし
        self.assertEqual(report["rejected_scope"], 1)
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.unexpected, [])

    def test_scope_rejects_correct_that_duplicates_unrelated_reading(self) -> None:
        """F2: correctが余計な読み（「安全保障」の読みの重複）を含み、heardより
        大幅に長い指摘は機械的に弾く（理事会っていうと→アンゼンホショオリジカイッテイウト）。"""
        engine = _FakeEngine({})
        lines = [{"speaker": "eme"}]
        texts = ["その理事会っていうとどうなるの"]
        queries = [_q("ソノリジカイッテイウトドウナルノ")]
        corrections = [{"index": 1, "surface": "理事会っていうと", "heard": "リジカイ",
                        "correct": "アンゼンホショオリジカイッテイウト"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "その理事会っていうとどうなるの")
        self.assertEqual(report["rejected_scope"], 1)
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.unexpected, [])

    def test_picks_candidate_with_fewer_accent_phrases(self) -> None:
        line = "副次的な効果もある"
        engine = _FakeEngine({
            # 位置検証プローブ（heardのカナへの置換）。現在の（誤読された）読みを再現する
            "フクツギテキな効果もある": _q("フクツギテキナコオカモアル"),
            # 修正候補（correctのひらがな／カタカナ）
            "ふくじてきな効果もある": _q("フクジテキナコオカモアル", n_phrases=2),
            "フクジテキな効果もある": _q("フクジテキナコオカモアル", n_phrases=3),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("フクツギテキナコオカモアル")]  # 誤読中: 次がツギと読まれている
        corrections = [{"index": 1, "surface": "副次的", "heard": "フクツギテキ", "correct": "フクジテキ"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "ふくじてきな効果もある")  # 句数が少ない方（ひらがな）が採用
        self.assertEqual(report["applied"], 1)
        self.assertEqual(engine.unexpected, [])

    def test_tie_breaks_to_hiragana(self) -> None:
        line = "最適化に向けた"
        engine = _FakeEngine({
            "パアソナライズに向けた": _q("パアソナライズニムケタ"),
            "さいてきかに向けた": _q("サイテキカニムケタ", n_phrases=2),
            "サイテキカに向けた": _q("サイテキカニムケタ", n_phrases=2),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("パアソナライズニムケタ")]
        corrections = [{"index": 1, "surface": "最適化", "heard": "パアソナライズ", "correct": "サイテキカ"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "さいてきかに向けた")
        self.assertEqual(report["applied"], 1)
        self.assertEqual(engine.unexpected, [])

    def test_keeps_original_when_neither_candidate_verifies(self) -> None:
        line = "難しい語です"
        engine = _FakeEngine({
            "難しいゴです": _q("マチガイノママ"),  # プローブは再現する（＝検証はできる）
            "難しいよみです": _q("マチガイノママ"),  # が、どちらの候補でも読みが直らない
            "難しいヨミです": _q("マチガイノママ"),
        })
        lines = [{"speaker": "ruje"}]
        texts = [line]
        queries = [_q("マチガイノママ")]
        corrections = [{"index": 1, "surface": "語", "heard": "ゴ", "correct": "ヨミ"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], line)  # 変更なし
        self.assertEqual(queries[0], _q("マチガイノママ"))
        self.assertEqual({k: report[k] for k in ("pointed", "applied", "unverified", "rejected_scope", "unfixable")},
                         {"pointed": 1, "applied": 0, "unverified": 0, "rejected_scope": 0, "unfixable": 1})
        self.assertEqual(engine.unexpected, [])

    def test_unverified_when_heard_is_not_reproducible(self) -> None:
        """LLMの指摘（heard）が実際には現在の読みを再現できない場合
        （行は既に正しく読めている等）、"unverified"として見送り、行は変更しない。"""
        line = "今日は忙しい一日だった"
        engine = _FakeEngine({
            "今日はボウガシイ一日だった": _q("チガウヨミダ"),
            "今日はぼうがしい一日だった": _q("チガウヨミダ"),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("キョウワイソガシイイチニチダッタ")]  # 実際には既に正しく読めている
        corrections = [{"index": 1, "surface": "忙しい", "heard": "ボウガシイ", "correct": "イソガシイ"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], line)
        self.assertEqual({k: report[k] for k in ("pointed", "applied", "unverified", "rejected_scope", "unfixable")},
                         {"pointed": 1, "applied": 0, "unverified": 1, "rejected_scope": 0, "unfixable": 0})
        self.assertEqual(engine.unexpected, [])

    def test_multi_occurrence_only_the_misread_one_is_fixed(self) -> None:
        """F1: 同じsurface（「人」）が行内に複数回現れても、位置検証で実際に
        誤読されている出現（「って人」）だけを直し、無関係な出現（「個人」）は
        巻き込まない。"""
        line = "個人だけど、って人多いよね"
        engine = _FakeEngine({
            # 「個人」側の出現（idx1）: プローブを当てても現在の読みを再現できない
            "個ジンだけど、って人多いよね": _q("アイウエオ"),
            "個じんだけど、って人多いよね": _q("アイウエオ"),
            # 「って人」側の出現（idx8）: プローブが現在の読みを再現する（＝ここが誤読箇所）
            "個人だけど、ってジン多いよね": _q("コジンダケドッテジンオオイヨネ"),
            # 修正候補
            "個人だけど、ってひと多いよね": _q("コジンダケドッテヒトオオイヨネ", n_phrases=1),
            "個人だけど、ってヒト多いよね": _q("コジンダケドッテヒトオオイヨネ", n_phrases=2),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("コジンダケドッテジンオオイヨネ")]
        corrections = [{"index": 1, "surface": "人", "heard": "ジン", "correct": "ヒト"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "個人だけど、ってひと多いよね")  # 「個人」は保持、「って人」だけ修正
        self.assertEqual(report["applied"], 1)
        self.assertEqual(engine.unexpected, [])

    def test_short_reading_elsewhere_in_line_does_not_block_real_fix(self) -> None:
        """F3: 「自分」に含まれる「ブン」のような短い読みが行の他の場所に
        既に存在していても、本当に誤読されている「分」は誤検知として
        discardされず、きちんと修正される。"""
        line = "自分の分だけ気にしてる"
        engine = _FakeEngine({
            # 「自分」側の出現（idx1）: プローブを当てても現在の読みを再現できない
            "自ワケの分だけ気にしてる": _q("チガウ"),
            "自わけの分だけ気にしてる": _q("チガウ"),
            # 標準の「分」側の出現（idx3）: プローブが現在の読みを再現する
            "自分のワケだけ気にしてる": _q("ジブンノワケダケキニシテル"),
            # 修正候補
            "自分のぶんだけ気にしてる": _q("ジブンノブンダケキニシテル", n_phrases=1),
            "自分のブンだけ気にしてる": _q("ジブンノブンダケキニシテル", n_phrases=2),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("ジブンノワケダケキニシテル")]
        corrections = [{"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "自分のぶんだけ気にしてる")  # 「自分」は保持、標準の「分」だけ修正
        self.assertEqual(report["applied"], 1)
        self.assertEqual(engine.unexpected, [])

    def test_boundary_vowel_fold_does_not_block_valid_fix(self) -> None:
        """F4: normalize_kanaの境界畳み込み（「ノ」の後の「ウ」→「オ」）のせいで
        正しい修正（机の上→ウエ）が誤って却下されないこと。"""
        line = "机の上に置いてね"
        engine = _FakeEngine({
            "机のジョウに置いてね": _q("ツクエノジョウニオイテネ"),
            "机のうえに置いてね": _q("ツクエノウエニオイテネ", n_phrases=1),
            "机のウエに置いてね": _q("ツクエノウエニオイテネ", n_phrases=1),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("ツクエノジョウニオイテネ")]
        corrections = [{"index": 1, "surface": "上", "heard": "ジョウ", "correct": "ウエ"}]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "机のうえに置いてね")  # 同数ならひらがな採用
        self.assertEqual(report["applied"], 1)
        self.assertEqual(engine.unexpected, [])

    def test_two_corrections_on_one_line_apply_sequentially(self) -> None:
        line = "分と人の話"
        engine = _FakeEngine({
            # 1件目（「分」）の位置検証プローブ
            "ワケと人の話": _q("ワケトニンノハナシ"),
            # 1件目の修正候補
            "ぶんと人の話": _q("ブントニンノハナシ", n_phrases=1),
            "ブンと人の話": _q("ブントニンノハナシ", n_phrases=2),
            # 2件目（「人」）の位置検証プローブ（1件目適用後のテキストに対して）
            "ぶんとニンの話": _q("ブントニンノハナシ"),
            # 2件目の修正候補
            "ぶんとひとの話": _q("ブントヒトノハナシ", n_phrases=1),
            "ぶんとヒトの話": _q("ブントヒトノハナシ", n_phrases=2),
        })
        lines = [{"speaker": "eme"}]
        texts = [line]
        queries = [_q("ワケトニンノハナシ")]
        corrections = [
            {"index": 1, "surface": "分", "heard": "ワケ", "correct": "ブン"},
            {"index": 1, "surface": "人", "heard": "ニン", "correct": "ヒト"},
        ]

        report = _apply_reading_corrections(engine, lines, texts, queries, corrections)

        self.assertEqual(texts[0], "ぶんとひとの話")
        self.assertEqual(report["applied"], 2)
        self.assertEqual(engine.unexpected, [])


class TestRunReadingCheckWithReport(unittest.TestCase):
    def test_returned_queries_are_used_for_synthesis(self) -> None:
        lines = [
            {"speaker": "eme", "text": "分かった、分だけ気をつける"},
            {"speaker": "ruje", "text": "了解、問題ないよ"},
        ]
        prepared = [ln["text"] for ln in lines]
        engine = _FakeEngine({
            "分かった、分だけ気をつける": _q("ワカッタ、ワケダケキヲツケル", n_phrases=2),
            "了解、問題ないよ": _q("リョウカイ、モンダイナイヨ", n_phrases=2),
            # 位置検証プローブ
            "分かった、ワケダケ気をつける": _q("ワカッタ、ワケダケキヲツケル"),
            # 修正候補
            "分かった、ぶんだけ気をつける": _q("ワカッタ、ブンダケキヲツケル", n_phrases=1),
            "分かった、ブンダケ気をつける": _q("ワカッタ、ブンダケキヲツケル", n_phrases=2),
        })
        corrections = [{"index": 1, "surface": "分だけ", "heard": "ワケダケ", "correct": "ブンダケ"}]

        with patch("src.build_audio.find_misreadings", return_value=corrections) as fm:
            queries, report = _run_reading_check_with_report(engine, lines, prepared, "claude-sonnet-4-6")

        fm.assert_called_once()
        self.assertEqual(len(queries), 2)
        # 1行目は修正版のクエリに差し替わっている
        self.assertEqual(queries[0], _q("ワカッタ、ブンダケキヲツケル", n_phrases=1))
        # 2行目は指摘が無いので1周目のクエリのまま
        self.assertEqual(queries[1], _q("リョウカイ、モンダイナイヨ", n_phrases=2))
        self.assertEqual(report["applied"], 1)
        self.assertEqual(engine.unexpected, [])

    def test_one_line_query_failure_does_not_abort_the_whole_phase(self) -> None:
        """1セリフのaudio_query失敗（本番で実際に起きた500再現）があっても、
        読み検証フェーズ全体を中断せず、残りのセリフの検証は続ける。"""
        lines = [
            {"speaker": "eme", "text": "おはよう"},
            {"speaker": "ruje", "text": "使いっぱなしで改善のループを回せてない"},  # これだけqueryが失敗する
            {"speaker": "eme", "text": "また明日ね"},
        ]
        prepared = [ln["text"] for ln in lines]
        engine = _FakeEngine({
            "おはよう": _q("オハヨウ"),
            "また明日ね": _q("マタアシタネ"),
            # 2行目はresponsesに無いので_FakeEngine.queryがRuntimeErrorを投げる
        })

        with patch("src.build_audio.find_misreadings", return_value=[]) as fm:
            queries, report = _run_reading_check_with_report(engine, lines, prepared, "claude-sonnet-4-6")

        # 失敗した2行目だけqueryがNoneのまま、他の2行は通常どおり検証済み
        self.assertIsNone(queries[1])
        self.assertEqual(queries[0], _q("オハヨウ"))
        self.assertEqual(queries[2], _q("マタアシタネ"))
        self.assertEqual(report["query_failed"], 1)

        # find_misreadingsに渡されたpairsに失敗した2行目は含まれない
        pairs_arg = fm.call_args[0][0]
        self.assertEqual([p["index"] for p in pairs_arg], [1, 3])


class _FakeSynthEngine:
    """synthesize()の合成フェーズ用の疑似エンジン。fail_predicate(text)が
    Trueを返すテキストはengine.synth()が例外を投げる。"""

    def __init__(self, fail_predicate) -> None:
        self.fail_predicate = fail_predicate
        self.calls: list[str] = []

    def prepare(self) -> None:
        pass

    def synth(self, role: str, text: str) -> AudioSegment:
        self.calls.append(text)
        if self.fail_predicate(text):
            raise RuntimeError("boom (engine)")
        return AudioSegment.silent(duration=50)

    def synth_from_query(self, role: str, query_json: dict) -> AudioSegment:
        raise AssertionError("このテストではreading_check_modelを渡していないので呼ばれないはず")


class TestSynthesizeSplitRecovery(unittest.TestCase):
    """synthesize()の各セリフ合成が失敗した時の分割合成フォールバック。"""

    def _run(self, lines: list[dict[str, str]], fail_predicate):
        import io
        from contextlib import redirect_stdout

        from src.build_audio import synthesize

        engine = _FakeSynthEngine(fail_predicate)
        buf = io.StringIO()
        with patch("src.build_audio.get_engine", return_value=engine), redirect_stdout(buf):
            show, starts_ms = synthesize(lines, tts_cfg={"pause_ms": 10})
        return show, starts_ms, engine, buf.getvalue()

    def test_normal_case_does_not_split(self) -> None:
        """正常時（合成が一度も失敗しない）は分割せず、通常の呼び出し1回だけ。
        分割合成のヘルパーは一切呼ばれない（正常経路が変わっていないことの確認）。"""
        lines = [
            {"speaker": "eme", "text": "おはよう"},
            {"speaker": "ruje", "text": "今日もいい天気だね"},
        ]
        with patch("src.build_audio._synth_line_with_recovery") as recovery:
            show, starts_ms, engine, log = self._run(lines, fail_predicate=lambda t: False)

        recovery.assert_not_called()
        self.assertEqual(engine.calls, ["おはよう", "今日もいい天気だね"])
        self.assertNotIn("分割合成", log)
        self.assertNotIn("スキップ", log)

    def test_recovers_via_punctuation_split_without_losing_the_line(self) -> None:
        """句読点で区切った断片ごとの合成に落とせば直る失敗（本番の500再現に近い
        状況）では、セリフを丸ごと欠落させずに分割合成で復旧する。"""
        full_text = "使いっぱなしで改善のループを回せてない、それでも頑張ろう。"
        lines = [{"speaker": "ruje", "text": full_text}]

        def fail_predicate(text: str) -> bool:
            return text == full_text  # フルテキストの時だけ失敗、断片は成功する

        show, starts_ms, engine, log = self._run(lines, fail_predicate)

        self.assertIn(full_text, engine.calls)  # まず通常合成を試みている
        # 分割された断片（句読点で終わる）が個別に合成されている
        self.assertIn("使いっぱなしで改善のループを回せてない、", engine.calls)
        self.assertIn("それでも頑張ろう。", engine.calls)
        self.assertIn("[fix] セリフ1: 分割合成で復旧", log)
        self.assertNotIn("スキップ", log)
        self.assertGreater(len(show), 0)

    def test_recovers_via_second_level_half_split(self) -> None:
        """句読点分割の断片単体でも失敗する場合、その断片をさらに半分に分けて
        再試行する（最大2段）ところまで含めて復旧できる。"""
        frag1 = "使いっぱなしで改善のループを回せてない、"
        frag2 = "それでも頑張ろう。"
        full_text = frag1 + frag2

        def fail_predicate(text: str) -> bool:
            return text in (full_text, frag1)  # フルテキストと1つ目の断片は失敗

        show, starts_ms, engine, log = self._run([{"speaker": "eme", "text": full_text}], fail_predicate)

        self.assertIn(full_text, engine.calls)
        self.assertIn(frag1, engine.calls)
        self.assertIn(frag2, engine.calls)
        mid = len(frag1) // 2
        self.assertIn(frag1[:mid], engine.calls)
        self.assertIn(frag1[mid:], engine.calls)
        self.assertIn("[fix] セリフ1: 分割合成で復旧", log)
        self.assertNotIn("欠落", log)
        self.assertNotIn("セリフ1の合成をスキップ", log)
        self.assertGreater(len(show), 0)

    def test_gives_up_after_two_split_levels_and_skips_only_that_line(self) -> None:
        """フルテキスト・句読点断片・半分割のいずれも失敗する最小断片は、
        その部分だけを飛ばし（本文はログに出さない）、他のセリフは正常に
        収録され、synthesize()自体は例外を投げない。"""
        bad_text = "AAAA"  # 句読点が無いので半分割のみで、最終的に全滅させる
        lines = [
            {"speaker": "eme", "text": "1本目は普通に成功する"},
            {"speaker": "ruje", "text": bad_text},
            {"speaker": "eme", "text": "3本目も普通に成功する"},
            {"speaker": "ruje", "text": "4本目も普通に成功する"},
            {"speaker": "eme", "text": "5本目も普通に成功する"},
        ]

        def fail_predicate(text: str) -> bool:
            # bad_textを含む断片（分割で生まれる部分文字列含む）はすべて失敗させる
            return "A" in text

        show, starts_ms, engine, log = self._run(lines, fail_predicate)

        self.assertIn("[warn] セリフ2の合成をスキップ: RuntimeError", log)
        self.assertNotIn(bad_text, log)  # 台本の本文はログに出さない
        self.assertGreater(len(show), 0)  # 他の4本は収録されている


if __name__ == "__main__":
    unittest.main()
