"""大型連休（土日・祝日・年末年始の連続した休日）の判定。

台本生成で「連休の入り」に一言触れるための材料を返すだけの小さなモジュール。
放送を止めないことが最優先なので、jpholiday が使えない・例外が出た場合は
[warn] を1行出して None（＝連休扱いしない）を返す。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

try:  # 未導入でも放送は止めない（long_holiday_info が None を返す）
    import jpholiday
except Exception as _e:  # noqa: BLE001
    jpholiday = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = _e
else:
    _IMPORT_ERROR = None

# ブロックの端を探す走査の上限（is_off_day が常に True を返すような異常時の無限ループ防止）
_MAX_SCAN_DAYS = 60


def is_off_day(d: date) -> bool:
    """土日、日本の祝日（振替休日・国民の休日を含む）、年末年始(12/29〜1/3)なら True。

    年末年始は官公庁の休日。jpholiday が使えないときは例外を投げる
    （呼び出し側の long_holiday_info が捕まえて None にする）。
    """
    if jpholiday is None:
        # 祝日を判定できないまま土日だけで答えると、連休の誤案内になりうるため
        raise RuntimeError(f"jpholiday を読み込めません: {_IMPORT_ERROR}")
    if d.weekday() >= 5:
        return True
    if (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3):
        return True
    return bool(jpholiday.is_holiday(d))


def long_holiday_info(today: date, min_days: int = 4) -> dict[str, Any] | None:
    """連続する休日が min_days 日以上の大型連休に入る日だけ、その情報を返す。

    - 今日が連休の初日（前日が休日でない）      → {"when": "今日から", ...}
    - 今日は平日で、明日が連休の初日            → {"when": "明日から", ...}
    - それ以外（連休中の2日目以降を含む）       → None

    戻り値の n は連休の日数、start / end は連休の最初と最後の日(date)。
    例外・jpholiday 不在のときは [warn] を1行出して None を返す。
    """
    try:
        one_day = timedelta(days=1)
        if is_off_day(today):
            if is_off_day(today - one_day):
                return None  # 連休の2日目以降には何も出さない
            start = today
        else:
            start = today + one_day
            if not is_off_day(start):
                return None
        end = start
        for _ in range(_MAX_SCAN_DAYS):
            if not is_off_day(end + one_day):
                break
            end += one_day
        else:
            raise RuntimeError("休日の連続が長すぎます（判定の異常）")
        n = (end - start).days + 1
        if n < min_days:
            return None
        return {"when": "今日から" if start == today else "明日から",
                "n": n, "start": start, "end": end}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 大型連休の判定に失敗（連休の案内なしで続行）: {e}")
        return None
