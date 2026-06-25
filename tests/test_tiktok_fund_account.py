from datetime import datetime
from zoneinfo import ZoneInfo

from finance_crawler.periods import PeriodRange
from finance_crawler.platforms import tiktok_fund_account
from finance_crawler.platforms.tiktok_fund_account import build_fund_account_payload


def test_fund_account_export_payload_is_all_transaction_history():
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    payload = build_fund_account_payload("4010000000000640969", period)

    assert payload == {
        "language": "zh",
        "task_type": "TRANSACTION_HISTORY",
        "wuid": "4010000000000640969",
        "start_time_stamp": 1777593600,
        "end_time_stamp": 1780272000,
        "bill_type": None,
        "in_out_type": None,
        "biz_reference_id": "",
    }


def test_fund_account_export_does_not_refresh_before_successful_create(monkeypatch, tmp_path):
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="weekly",
        start=datetime(2026, 5, 25, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    class DummyCtx:
        page = object()

    monkeypatch.setattr(tiktok_fund_account, "open_seller_wallet_page", lambda ctx: None)
    monkeypatch.setattr(
        tiktok_fund_account,
        "resolve_cashier_url",
        lambda ctx, detect_seconds, request_timeout: (
            "https://cashier-my4a.pipopay.com/pipo/fe/business_wallet/wallet/views/main"
            "?merchant_id=m1&wuid=w1"
        ),
    )
    monkeypatch.setattr(tiktok_fund_account, "parse_cashier_params", lambda url: {"merchant_id": "m1", "wuid": "w1"})
    monkeypatch.setattr(tiktok_fund_account, "open_cashier_page", lambda ctx, url: None)
    monkeypatch.setattr(tiktok_fund_account, "probe_fund_account_list", lambda *args, **kwargs: {"result_code": "success"})
    monkeypatch.setattr(
        tiktok_fund_account,
        "refresh_session_if_possible",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected refresh")),
    )
    monkeypatch.setattr(
        tiktok_fund_account,
        "create_fund_file_task",
        lambda *args, **kwargs: ({"task_type": "TRANSACTION_HISTORY"}, {"task_id": "task-1"}),
    )
    monkeypatch.setattr(tiktok_fund_account, "tiktok_download_poll_options", lambda task: (1, 0))
    monkeypatch.setattr(tiktok_fund_account, "wait_file_task", lambda *args, **kwargs: ("/download.csv", {"ok": True}))
    monkeypatch.setattr(tiktok_fund_account, "browser_download_file", lambda page, url, output, timeout: 123)
    monkeypatch.setattr(tiktok_fund_account, "write_capture_file", lambda *args, **kwargs: "")

    result = tiktok_fund_account.export_tiktok_fund_account_with_ctx(
        {"id": "tiktok_fund_account"},
        "C1主账号",
        period,
        DummyCtx(),
        tmp_path,
    )

    assert result.success


def test_task_result_defaults_failed_status_from_success_flag():
    from finance_crawler.models import TaskResult

    assert TaskResult("ok", "tiktok", "C1", True, "done").status == "success"
    assert TaskResult("bad", "tiktok", "C1", False, "boom").status == "failed"
