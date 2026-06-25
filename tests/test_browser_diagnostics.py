from datetime import datetime
from zoneinfo import ZoneInfo

import main
from finance_crawler.diagnostics import collect_browser_diagnostics, install_browser_request_recorder
from finance_crawler.debug_files import write_capture_file
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms import tiktok_email_income


def test_diagnose_runtime_flag_forces_capture_files():
    tasks = [{"id": "tiktok_email_income", "save_capture_files": False}]

    flagged = main.apply_runtime_flags(tasks, {"save_capture_files": False}, diagnose=True)

    assert flagged[0]["diagnostic_mode"] is True
    assert flagged[0]["save_capture_files"] is True


def test_failed_capture_is_written_even_when_regular_capture_disabled(tmp_path):
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    path = write_capture_file(
        {"id": "demo", "save_capture_files": False},
        tmp_path,
        "temu",
        period,
        "demo",
        {"error": "failed"},
        failed=True,
    )

    assert path
    assert "failed" in path


def test_browser_diagnostics_collects_recorder_entries_and_page_state():
    class FakePage:
        url = "https://seller.us.tiktokshopglobalselling.com/finance/bills"

        def __init__(self):
            self.installed = False

        def run_js(self, script):
            if "__financeCrawlerRequests" in script and "fetch = async" in script:
                self.installed = True
                return True
            return {
                "url": self.url,
                "title": "Bills",
                "requests": [{"method": "GET", "url": "/api/v2/pay/settlement/file/list", "status": 200}],
                "resources": ["https://seller.us.tiktokshopglobalselling.com/api/v2/pay/settlement/file/list"],
            }

    page = FakePage()

    assert install_browser_request_recorder(page)
    assert page.installed
    diagnostics = collect_browser_diagnostics(page)

    assert diagnostics["url"] == page.url
    assert diagnostics["requests"][0]["url"] == "/api/v2/pay/settlement/file/list"
    assert "settlement/file/list" in diagnostics["resources"][0]


def test_e1e2_failed_capture_includes_browser_diagnostics(monkeypatch, tmp_path):
    tz = ZoneInfo("America/Anchorage")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )
    captured = {}

    class DummyCtx:
        page = object()

    monkeypatch.setattr(tiktok_email_income, "open_bills_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(tiktok_email_income, "install_browser_request_recorder", lambda page: True)
    monkeypatch.setattr(
        tiktok_email_income,
        "get_seller_info",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("seller failed")),
    )
    monkeypatch.setattr(
        tiktok_email_income,
        "collect_browser_diagnostics",
        lambda page: {"url": "https://seller.us.tiktokshopglobalselling.com/finance/bills", "requests": [{"url": "/api"}]},
    )

    def fake_capture(task, output_root, platform, period, file_stem, payload, failed=False):
        captured.update({"payload": payload, "failed": failed})
        return "capture.json"

    monkeypatch.setattr(tiktok_email_income, "write_capture_file", fake_capture)

    result = tiktok_email_income.export_tiktok_email_income_with_ctx(
        {"id": "tiktok_email_income", "platform": "E1E2", "diagnostic_mode": True, "save_capture_files": True},
        "TIKTOK-POP-E2-SL",
        period,
        DummyCtx(),
        tmp_path,
    )

    assert not result.success
    assert captured["failed"] is True
    assert captured["payload"]["browser_diagnostics"]["requests"][0]["url"] == "/api"
