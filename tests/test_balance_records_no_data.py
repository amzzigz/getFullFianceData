from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from finance_crawler.periods import PeriodRange
from finance_crawler.platforms import balance_records


@pytest.mark.parametrize("account_name", ["A21POP", "A23POP"])
def test_inactive_pop_balance_accounts_treat_empty_currency_as_no_data(
    monkeypatch,
    tmp_path,
    account_name,
) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )
    auth_result = SimpleNamespace(success=True, cookie="cookie", user_agent="ua")

    monkeypatch.setattr(balance_records, "build_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(balance_records, "resolve_supplier_id", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        balance_records,
        "resolve_currency",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("未能获取币种: {'code': '0', 'msg': 'OK', 'info': []}")
        ),
    )

    result = balance_records.export_balance_records(
        {
            "id": "pop_balance_records",
            "platform": "pop",
            "target_page": "https://sso.geiwohuo.com/#/mws/seller/balance-changes",
            "_auth_result": auth_result,
        },
        account_name,
        period,
        tmp_path / "auth.py",
        tmp_path,
    )

    assert result.success
    assert result.status == "no_data"
    assert result.data["no_data"] is True


def test_other_pop_account_keeps_empty_currency_as_failure(monkeypatch, tmp_path) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )
    auth_result = SimpleNamespace(success=True, cookie="cookie", user_agent="ua")

    monkeypatch.setattr(balance_records, "build_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(balance_records, "resolve_supplier_id", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        balance_records,
        "resolve_currency",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("未能获取币种: {'code': '0', 'msg': 'OK', 'info': []}")
        ),
    )

    result = balance_records.export_balance_records(
        {
            "id": "pop_balance_records",
            "platform": "pop",
            "target_page": "https://sso.geiwohuo.com/#/mws/seller/balance-changes",
            "_auth_result": auth_result,
        },
        "A20POP",
        period,
        tmp_path / "auth.py",
        tmp_path,
    )

    assert not result.success
    assert result.status == "failed"
