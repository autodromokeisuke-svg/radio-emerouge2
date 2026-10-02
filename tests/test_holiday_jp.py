"""src/holiday_jp.py（大型連休の判定）の単体テスト。

期待値は実カレンダー（2026・2027年）。jpholiday を実際に使う（モックしない）テストと、
jpholiday が使えない・壊れているときに放送を止めないことを確かめるテストから成る。
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.holiday_jp import is_off_day, long_holiday_info


def _d(s: str) -> date:
    return date.fromisoformat(s)


class TestIsOffDay(unittest.TestCase):
    def test_weekends(self) -> None:
        self.assertTrue(is_off_day(_d("2026-10-03")))   # 土
        self.assertTrue(is_off_day(_d("2026-10-04")))   # 日
        self.assertFalse(is_off_day(_d("2026-10-05")))  # 月（平日）

    def test_public_holidays_including_substitute_and_national_holiday(self) -> None:
        self.assertTrue(is_off_day(_d("2026-09-21")))   # 敬老の日
        self.assertTrue(is_off_day(_d("2026-09-22")))   # 国民の休日
        self.assertTrue(is_off_day(_d("2026-09-23")))   # 秋分の日
        self.assertTrue(is_off_day(_d("2026-05-06")))   # 振替休日（水）
        self.assertTrue(is_off_day(_d("2027-04-29")))   # 昭和の日（木）

    def test_new_year_period_is_off_from_dec_29_to_jan_3(self) -> None:
        self.assertFalse(is_off_day(_d("2026-12-28")))  # 月
        for s in ["2026-12-29", "2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02", "2027-01-03"]:
            with self.subTest(day=s):
                self.assertTrue(is_off_day(_d(s)))
        self.assertFalse(is_off_day(_d("2027-01-04")))  # 月（仕事始め）

    def test_ordinary_weekday_is_not_off(self) -> None:
        self.assertFalse(is_off_day(_d("2026-10-01")))


class TestLongHolidayInfo(unittest.TestCase):
    def assertInfo(self, today: str, when: str, n: int, start: str, end: str) -> None:
        info = long_holiday_info(_d(today))
        self.assertIsNotNone(info, today)
        self.assertEqual(info["when"], when, today)
        self.assertEqual(info["n"], n, today)
        self.assertEqual(info["start"], _d(start), today)
        self.assertEqual(info["end"], _d(end), today)

    def test_silver_week_2026(self) -> None:
        """2026-09-19(土)〜23(水)の5連休（敬老の日・国民の休日・秋分の日）。"""
        self.assertInfo("2026-09-18", "明日から", 5, "2026-09-19", "2026-09-23")
        self.assertInfo("2026-09-19", "今日から", 5, "2026-09-19", "2026-09-23")

    def test_nothing_during_or_after_the_holiday(self) -> None:
        for s in ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]:
            with self.subTest(day=s):
                self.assertIsNone(long_holiday_info(_d(s)))

    def test_golden_week_2026_with_substitute_holiday(self) -> None:
        """5/2(土)〜5/6(水)。5/6は振替休日。"""
        self.assertInfo("2026-05-01", "明日から", 5, "2026-05-02", "2026-05-06")
        self.assertInfo("2026-05-02", "今日から", 5, "2026-05-02", "2026-05-06")
        for s in ["2026-05-03", "2026-05-06", "2026-05-07"]:
            with self.subTest(day=s):
                self.assertIsNone(long_holiday_info(_d(s)))

    def test_new_year_2026_2027(self) -> None:
        """12/29(火)〜1/3(日)の6連休（年末年始）。12/28(月)は平日。"""
        self.assertInfo("2026-12-28", "明日から", 6, "2026-12-29", "2027-01-03")
        self.assertInfo("2026-12-29", "今日から", 6, "2026-12-29", "2027-01-03")
        for s in ["2026-12-30", "2027-01-01", "2027-01-03", "2027-01-04"]:
            with self.subTest(day=s):
                self.assertIsNone(long_holiday_info(_d(s)))

    def test_golden_week_2027(self) -> None:
        """5/1(土)〜5/5(水)の5連休。4/29(昭和の日・木)は単独の休日で連休にならない。"""
        self.assertInfo("2027-04-30", "明日から", 5, "2027-05-01", "2027-05-05")
        self.assertIsNone(long_holiday_info(_d("2027-04-29")))

    def test_three_day_weekends_are_not_long_holidays(self) -> None:
        self.assertIsNone(long_holiday_info(_d("2026-11-20")))  # 11/21〜23の3連休の前日
        self.assertIsNone(long_holiday_info(_d("2026-11-21")))
        self.assertIsNone(long_holiday_info(_d("2026-10-09")))  # 10/10〜12の3連休の前日
        self.assertIsNone(long_holiday_info(_d("2026-10-10")))

    def test_ordinary_days_and_plain_weekends(self) -> None:
        for s in ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]:
            with self.subTest(day=s):
                self.assertIsNone(long_holiday_info(_d(s)))

    def test_min_days_is_adjustable(self) -> None:
        info = long_holiday_info(_d("2026-11-20"), min_days=3)
        self.assertEqual((info["when"], info["n"]), ("明日から", 3))
        self.assertEqual((info["start"], info["end"]), (_d("2026-11-21"), _d("2026-11-23")))
        self.assertIsNone(long_holiday_info(_d("2026-11-20"), min_days=4))

    def test_never_returns_info_for_a_day_inside_the_block(self) -> None:
        """2026年の全日付を走査: 連休が出る日は必ず「初日」か「初日の前日の平日」だけ。"""
        d = _d("2026-01-01")
        while d.year == 2026:
            info = long_holiday_info(d)
            if info is not None:
                self.assertGreaterEqual(info["n"], 4, d)
                if info["when"] == "今日から":
                    self.assertEqual(info["start"], d)
                    self.assertFalse(is_off_day(date.fromordinal(d.toordinal() - 1)), d)
                else:
                    self.assertFalse(is_off_day(d), d)
                    self.assertEqual(info["start"].toordinal(), d.toordinal() + 1)
            d = date.fromordinal(d.toordinal() + 1)


def _real_blocks_of_length(n: int, first_year: int, last_year: int) -> list[tuple[date, date]]:
    """実カレンダー（jpholiday）で、ちょうど n 日続く休日ブロックの (初日, 最終日) を探す。"""
    one = timedelta(days=1)
    found = []
    d = date(first_year, 1, 1)
    while d <= date(last_year, 12, 31):
        if is_off_day(d) and not is_off_day(d - one):
            end = d
            while is_off_day(end + one):
                end += one
            if (end - d).days + 1 == n:
                found.append((d, end))
            d = end + one
        else:
            d += one
    return found


class TestMinDaysBoundary(unittest.TestCase):
    """min_days の境界: ちょうど min_days 日なら連休（返る）、1日足りなければ連休ではない（None）。"""

    def test_three_day_weekend_is_none_by_default_and_a_long_holiday_at_min_days_3(self) -> None:
        """実カレンダー: 2026-10-10(土)〜12(月・スポーツの日)の3連休。"""
        self.assertIsNone(long_holiday_info(_d("2026-10-09")))                 # 既定 min_days=4
        self.assertIsNone(long_holiday_info(_d("2026-10-09"), min_days=4))
        info = long_holiday_info(_d("2026-10-09"), min_days=3)
        self.assertEqual((info["when"], info["n"]), ("明日から", 3))
        self.assertEqual((info["start"], info["end"]), (_d("2026-10-10"), _d("2026-10-12")))
        info = long_holiday_info(_d("2026-10-10"), min_days=3)
        self.assertEqual((info["when"], info["n"]), ("今日から", 3))
        self.assertIsNone(long_holiday_info(_d("2026-10-11"), min_days=3))     # 連休中の2日目以降は出さない
        self.assertIsNone(long_holiday_info(_d("2026-10-12"), min_days=3))

    def test_a_block_of_exactly_min_days_is_returned_and_one_more_is_not(self) -> None:
        """実カレンダー: シルバーウィーク2026（5連休）と年末年始（6連休）。"""
        for today, n in [("2026-09-18", 5), ("2026-12-28", 6)]:
            with self.subTest(today=today, n=n):
                self.assertIsNotNone(long_holiday_info(_d(today), min_days=n))
                self.assertEqual(long_holiday_info(_d(today), min_days=n)["n"], n)
                self.assertIsNone(long_holiday_info(_d(today), min_days=n + 1))
                self.assertIsNotNone(long_holiday_info(_d(today), min_days=n - 1))

    def test_exactly_four_day_block_in_the_real_calendar(self) -> None:
        """実カレンダーでちょうど4連休になる例: 2029年のGW（5/3木〜5/6日。5/3・5/4・5/5が祝日で土日に続く）。

        2026〜2028年には、実カレンダー上ちょうど4連休のブロックは無い（3連休・5連休・6連休のみ。
        jpholiday 1.0.3 で走査して確認）。最初の実例が2029年なので、それを使う。
        """
        self.assertEqual(_real_blocks_of_length(4, 2029, 2029), [(_d("2029-05-03"), _d("2029-05-06"))])
        info = long_holiday_info(_d("2029-05-02"))      # 水曜（前日の平日）
        self.assertEqual((info["when"], info["n"], info["start"], info["end"]),
                         ("明日から", 4, _d("2029-05-03"), _d("2029-05-06")))
        self.assertEqual(long_holiday_info(_d("2029-05-03"))["when"], "今日から")
        self.assertIsNone(long_holiday_info(_d("2029-05-04")))
        self.assertIsNone(long_holiday_info(_d("2029-05-02"), min_days=5))
        self.assertIsNone(long_holiday_info(_d("2029-05-03"), min_days=5))
        self.assertEqual(long_holiday_info(_d("2029-05-02"), min_days=4)["n"], 4)

    def test_exactly_four_day_block_synthesized_from_two_holidays_next_to_a_weekend(self) -> None:
        """合成: 2026-10-26(月)・10-27(火)を祝日にして 10/24(土)〜10/27(火) の4連休を作る。
        土日の判定は本物のまま、祝日だけ差し替える。"""
        fake = MagicMock()
        fake.is_holiday.side_effect = lambda d: d in (date(2026, 10, 26), date(2026, 10, 27))
        with patch("src.holiday_jp.jpholiday", fake):
            # 既定（min_days=4）: ちょうど4連休なので返る
            info = long_holiday_info(_d("2026-10-23"))
            self.assertEqual((info["when"], info["n"], info["start"], info["end"]),
                             ("明日から", 4, _d("2026-10-24"), _d("2026-10-27")))
            info = long_holiday_info(_d("2026-10-24"))
            self.assertEqual((info["when"], info["n"]), ("今日から", 4))
            # 連休中・連休明けは出さない
            for s in ["2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"]:
                with self.subTest(day=s):
                    self.assertIsNone(long_holiday_info(_d(s)))
            # min_days=5 なら4連休は連休扱いにならない
            self.assertIsNone(long_holiday_info(_d("2026-10-23"), min_days=5))
            self.assertIsNone(long_holiday_info(_d("2026-10-24"), min_days=5))
            # min_days=3 以下でも4連休として返る（n は実際の日数）
            self.assertEqual(long_holiday_info(_d("2026-10-23"), min_days=3)["n"], 4)

    def test_three_day_block_synthesized_is_not_a_long_holiday_by_default(self) -> None:
        """合成: 祝日が1日だけ土日に隣接（10/24土〜26月）なら3連休で、既定では None（4日未満）。"""
        fake = MagicMock()
        fake.is_holiday.side_effect = lambda d: d == date(2026, 10, 26)
        with patch("src.holiday_jp.jpholiday", fake):
            self.assertIsNone(long_holiday_info(_d("2026-10-23")))
            self.assertIsNone(long_holiday_info(_d("2026-10-24")))
            self.assertEqual(long_holiday_info(_d("2026-10-23"), min_days=3)["n"], 3)


class TestFailSoft(unittest.TestCase):
    """カレンダーが使えなくても放送を止めない: None を返し、[warn] を1行だけ出す。"""

    def _warn_lines(self, buf: io.StringIO) -> list[str]:
        return [ln for ln in buf.getvalue().splitlines() if ln.startswith("[warn]")]

    def test_missing_library_returns_none_with_one_warning(self) -> None:
        buf = io.StringIO()
        with patch("src.holiday_jp.jpholiday", None), redirect_stdout(buf):
            self.assertIsNone(long_holiday_info(_d("2026-09-18")))
        self.assertEqual(len(self._warn_lines(buf)), 1)

    def test_library_error_returns_none_with_one_warning(self) -> None:
        broken = MagicMock()
        broken.is_holiday.side_effect = RuntimeError("boom")
        buf = io.StringIO()
        with patch("src.holiday_jp.jpholiday", broken), redirect_stdout(buf):
            self.assertIsNone(long_holiday_info(_d("2026-09-18")))
        self.assertEqual(len(self._warn_lines(buf)), 1)
        self.assertIn("boom", buf.getvalue())

    def test_runaway_off_days_do_not_loop_forever(self) -> None:
        endless = MagicMock()
        endless.is_holiday.side_effect = lambda d: d >= date(2026, 9, 19)  # 休日が永遠に続く
        buf = io.StringIO()
        with patch("src.holiday_jp.jpholiday", endless), redirect_stdout(buf):
            self.assertIsNone(long_holiday_info(_d("2026-09-18")))
        self.assertEqual(len(self._warn_lines(buf)), 1)


if __name__ == "__main__":
    unittest.main()
