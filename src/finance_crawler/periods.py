from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class PeriodRange:
    period_type: str
    start: datetime
    end: datetime

    @property
    def start_ms(self) -> int:
        return int(self.start.timestamp() * 1000)

    @property
    def end_ms(self) -> int:
        return int(self.end.timestamp() * 1000)

    def to_dict(self) -> dict[str, str | int]:
        return {
            "period_type": self.period_type,
            "start": self.start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": self.end.strftime("%Y-%m-%d %H:%M:%S"),
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


def _as_local_date(today: date | datetime | None, timezone: str) -> date:
    if today is None:
        return datetime.now(ZoneInfo(timezone)).date()
    if isinstance(today, datetime):
        return today.astimezone(ZoneInfo(timezone)).date() if today.tzinfo else today.date()
    return today


def _day_start(value: date, timezone: str) -> datetime:
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(timezone))


def _day_end(value: date, timezone: str) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=ZoneInfo(timezone))


def previous_month_range(today: date | datetime | None = None, timezone: str = DEFAULT_TIMEZONE) -> PeriodRange:
    local_today = _as_local_date(today, timezone)
    first_day_this_month = local_today.replace(day=1)
    last_day_previous_month = first_day_this_month - timedelta(days=1)
    first_day_previous_month = last_day_previous_month.replace(day=1)
    return PeriodRange(
        period_type="monthly",
        start=_day_start(first_day_previous_month, timezone),
        end=_day_end(last_day_previous_month, timezone),
    )


def previous_week_range(today: date | datetime | None = None, timezone: str = DEFAULT_TIMEZONE) -> PeriodRange:
    local_today = _as_local_date(today, timezone)
    this_week_monday = local_today - timedelta(days=local_today.weekday())
    previous_week_monday = this_week_monday - timedelta(days=7)
    previous_week_sunday = this_week_monday - timedelta(days=1)
    return PeriodRange(
        period_type="weekly",
        start=_day_start(previous_week_monday, timezone),
        end=_day_end(previous_week_sunday, timezone),
    )


def resolve_previous_period(
    period_type: str,
    today: date | datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> PeriodRange:
    normalized = (period_type or "monthly").strip().lower()
    if normalized == "monthly":
        return previous_month_range(today=today, timezone=timezone)
    if normalized == "weekly":
        return previous_week_range(today=today, timezone=timezone)
    raise ValueError(f"不支持的周期: {period_type}")
