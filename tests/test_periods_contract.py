from datetime import datetime

from finance_crawler.periods import resolve_previous_period


def test_previous_month_boundary():
    period = resolve_previous_period("monthly", today=datetime(2026, 5, 12), timezone="Asia/Shanghai")
    assert period.period_type == "monthly"
    assert period.start.strftime("%Y-%m-%d") == "2026-04-01"
    assert period.end.strftime("%Y-%m-%d") == "2026-04-30"


def test_previous_week_exists():
    period = resolve_previous_period("weekly", today=datetime(2026, 5, 12), timezone="Asia/Shanghai")
    assert period.period_type == "weekly"
    assert period.start < period.end
