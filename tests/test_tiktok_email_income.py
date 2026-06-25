import json
import threading
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from finance_crawler.config import load_app_config
from finance_crawler.periods import PeriodRange


def test_tiktok_email_accounts_are_loaded_as_separate_platform(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    account_dir = tmp_path / "tools"
    account_dir.mkdir()
    (account_dir / "tk账号池.txt").write_text("C1主账号\nC2主账号\n", encoding="utf-8")
    (account_dir / "E1-E2.txt").write_text("TIKTOK-POP-E1\nTIKTOK-POP-E2-SL\n", encoding="utf-8")
    (config_dir / "local.json").write_text(
        json.dumps(
            {
                "paths": {
                    "tiktok_account_file": str(account_dir / "tk账号池.txt"),
                    "tiktok_email_account_file": str(account_dir / "E1-E2.txt"),
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "tasks.json").write_text('{"tasks":[]}', encoding="utf-8")

    config = load_app_config("local", config_dir)

    assert config.accounts["tiktok"] == ["C1主账号", "C2主账号"]
    assert config.accounts["tiktok_email"] == ["TIKTOK-POP-E1", "TIKTOK-POP-E2-SL"]


def test_tiktok_email_income_task_contract():
    tasks = json.loads(Path("config/tasks.json").read_text(encoding="utf-8"))["tasks"]
    task = next(item for item in tasks if item["id"] == "tiktok_email_income")

    assert task["platform"] == "E1E2"
    assert task["account_source"] == "tiktok_email"
    assert task["runner"] == "tiktok_email.income"
    assert task["frequency"] == ["monthly"]
    assert task["default_period"] == "monthly"
    assert task["timezone"] == "America/Anchorage"
    assert "seller.us.tiktokshopglobalselling.com/finance/bills" in task["target_page"]


def test_tiktok_email_income_payload_matches_e2_har_month_boundary():
    from finance_crawler.platforms.tiktok_email_income import build_income_export_payload, build_statement_list_params

    tz = ZoneInfo("America/Anchorage")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    assert build_income_export_payload(period) == {
        "period": {"begin_date": "1777622400", "end_date": "1780300799"},
        "file_type": 1,
        "statement_version": 0,
    }
    assert build_statement_list_params(period)["bill_period_time_lower"] == "1777622400000"
    assert build_statement_list_params(period)["bill_period_time_upper"] == "1780300799999"


def test_tiktok_email_income_waits_for_downloadable_file_status():
    from finance_crawler.platforms.tiktok_email_income import choose_ready_income_file

    files = [
        {"file_id": "not-ready", "file_name": "income_20260604182051(UTC-8).xlsx", "status": 1},
        {"file_id": "ready", "file_name": "income_20260604182052(UTC-8).xlsx", "status": 2},
    ]

    assert choose_ready_income_file(files)["file_id"] == "ready"
    assert choose_ready_income_file([{**files[0], "status": 3}])["file_id"] == "not-ready"


def test_open_bills_page_uses_seller_packet_as_readiness_signal():
    from finance_crawler.platforms import tiktok_email_income

    target_url = tiktok_email_income.BILLS_PAGE_URL
    events = []

    class FakeResponse:
        status = 200
        body = {
            "code": 0,
            "data": {
                "seller": {
                    "seller_id": "seller-1",
                    "shop_name": "E1",
                }
            },
        }

    class FakePacket:
        is_failed = False
        response = FakeResponse()

    class FakeListener:
        def start(self, target, method=None):
            events.append(("listen.start", target, method))

        def wait(self, timeout=None, raise_err=None):
            events.append(("listen.wait", timeout, raise_err))
            return FakePacket()

        def stop(self):
            events.append(("listen.stop",))

    class FakeWait:
        def url_change(self, text, timeout=None, raise_err=None):
            events.append(("wait.url_change", text, timeout, raise_err))
            return True

        def doc_loaded(self, timeout=None, raise_err=None):
            events.append(("wait.doc_loaded", timeout, raise_err))
            return True

    class FakePage:
        listen = FakeListener()
        wait = FakeWait()

        def get(self, url):
            events.append(("page.get", url))

    ctx = type("Ctx", (), {"page": FakePage()})()

    seller_info = tiktok_email_income.open_bills_page(ctx, target_url, timeout=30)

    assert seller_info["seller_id"] == "seller-1"
    assert events == [
        ("listen.start", tiktok_email_income.SELLER_COMMON_PATH, "GET"),
        ("page.get", target_url),
        ("wait.url_change", "/finance/bills", 30, False),
        ("wait.doc_loaded", 30, False),
        ("listen.wait", 30, False),
        ("listen.stop",),
    ]


def test_open_bills_page_listener_timeout_returns_fallback_signal():
    from finance_crawler.platforms import tiktok_email_income

    stopped = []

    class FakeListener:
        def start(self, target, method=None):
            pass

        def wait(self, timeout=None, raise_err=None):
            return False

        def stop(self):
            stopped.append(True)

    class FakeWait:
        def url_change(self, text, timeout=None, raise_err=None):
            return True

        def doc_loaded(self, timeout=None, raise_err=None):
            return True

    class FakePage:
        listen = FakeListener()
        wait = FakeWait()

        def get(self, url):
            pass

    ctx = type("Ctx", (), {"page": FakePage()})()

    seller_info = tiktok_email_income.open_bills_page(ctx, tiktok_email_income.BILLS_PAGE_URL, timeout=5)

    assert seller_info == {}
    assert stopped == [True]


def test_browser_json_request_retries_page_refresh_error(monkeypatch):
    from finance_crawler.platforms import tiktok_email_income

    class FakePage:
        def __init__(self):
            self.calls = 0

        def run_js(self, script):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("页面被刷新，请操作前尝试等待页面刷新或加载完成。")
            return {"ok": True, "status": 200, "url": "https://example.test", "data": {"code": 0}}

    page = FakePage()
    monkeypatch.setattr(tiktok_email_income.time, "sleep", lambda seconds: None)

    result = tiktok_email_income.browser_json_request(
        page,
        "GET",
        "https://example.test",
        None,
        60,
    )

    assert result == {"code": 0}
    assert page.calls == 2


def test_tiktok_email_income_output_uses_e1e2_sales_data_name(tmp_path):
    from finance_crawler.platforms import tiktok_email_income

    tz = ZoneInfo("America/Anchorage")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    class DummyCtx:
        page = object()

    monkeypatches = [
        ("open_bills_page", lambda *args, **kwargs: None),
        ("get_seller_info", lambda *args, **kwargs: {"seller_id": "seller-1", "seller": {}, "raw": {}}),
        ("list_statement_detail", lambda *args, **kwargs: {"code": 0}),
        ("create_income_export", lambda *args, **kwargs: ({"ok": True}, {"code": 0})),
        ("tiktok_download_poll_options", lambda task: (1, 0)),
        ("wait_income_file", lambda *args, **kwargs: ({"file_id": "file-1"}, {"code": 0})),
        ("get_income_download_url", lambda *args, **kwargs: ("https://example.test/file.xlsx", {"code": 0})),
        ("browser_download_file", lambda page, url, output, timeout: output.write_bytes(b"PK\x03\x04") or 4),
        ("write_capture_file", lambda *args, **kwargs: ""),
    ]
    for name, value in monkeypatches:
        setattr(tiktok_email_income, name, value)

    result = tiktok_email_income.export_tiktok_email_income_with_ctx(
        {"id": "tiktok_email_income", "platform": "E1E2", "export_folder": "销售数据", "runner": "tiktok_email.income"},
        "TIKTOK-POP-E2-SL",
        period,
        DummyCtx(),
        tmp_path,
    )

    assert result.success
    assert result.output_path.endswith(r"E1E2\monthly\20260501_20260531\销售数据\E2_20260501-20260531_销售数据.xlsx")


def test_tiktok_email_income_reuses_seller_info_from_listener(tmp_path):
    from finance_crawler.platforms import tiktok_email_income

    tz = ZoneInfo("America/Anchorage")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    class DummyCtx:
        page = object()

    def forbidden_get_seller_info(*args, **kwargs):
        raise AssertionError("listener seller info should avoid a second seller request")

    monkeypatches = [
        ("open_bills_page", lambda *args, **kwargs: {"seller_id": "seller-1", "seller": {}, "raw": {"code": 0}}),
        ("get_seller_info", forbidden_get_seller_info),
        ("list_statement_detail", lambda *args, **kwargs: {"code": 0}),
        ("create_income_export", lambda *args, **kwargs: ({"ok": True}, {"code": 0})),
        ("tiktok_download_poll_options", lambda task: (1, 0)),
        ("wait_income_file", lambda *args, **kwargs: ({"file_id": "file-1"}, {"code": 0})),
        ("get_income_download_url", lambda *args, **kwargs: ("https://example.test/file.xlsx", {"code": 0})),
        ("browser_download_file", lambda page, url, output, timeout: output.write_bytes(b"PK\x03\x04") or 4),
        ("write_capture_file", lambda *args, **kwargs: ""),
    ]
    for name, value in monkeypatches:
        setattr(tiktok_email_income, name, value)

    result = tiktok_email_income.export_tiktok_email_income_with_ctx(
        {"id": "tiktok_email_income", "platform": "E1E2", "export_folder": "销售数据", "runner": "tiktok_email.income"},
        "TIKTOK-POP-E2-SL",
        period,
        DummyCtx(),
        tmp_path,
    )

    assert result.success


def test_tiktok_browser_start_respects_ziniu_auth_concurrency(monkeypatch):
    from finance_crawler import auth
    from finance_crawler.platforms import tiktok_withdrawals

    auth.configure_ziniu_auth_concurrency(1)
    active = 0
    max_active = 0
    entered = 0
    lock = threading.Lock()
    second_entered = threading.Event()

    class FakeHelper:
        def ensure_client_online(self):
            return True, ""

        def get_shop_info(self, account_name):
            return {"browserOauth": f"oauth-{account_name}"}, ""

        def build_start_browser_payload(self, info):
            return {"action": "startBrowser"}

        def send_http(self, payload):
            nonlocal active, max_active, entered
            with lock:
                active += 1
                entered += 1
                max_active = max(max_active, active)
                if entered >= 2:
                    second_entered.set()
            second_entered.wait(0.2)
            with lock:
                active -= 1
            return {"statusCode": "0", "debuggingPort": 9222, "browserOauth": "oauth"}

    class FakePage:
        def __init__(self, url):
            self.url = url

        def quit(self):
            pass

    class FakeChromiumOptions:
        def set_local_port(self, port):
            return self

    class FakeChromium:
        def __init__(self, options):
            self.latest_tab = FakePage("about:blank")

        def new_tab(self, url):
            return FakePage(url)

    fake_drission = types.SimpleNamespace(Chromium=FakeChromium, ChromiumOptions=FakeChromiumOptions)
    monkeypatch.setitem(__import__("sys").modules, "DrissionPage", fake_drission)
    monkeypatch.setattr(tiktok_withdrawals, "load_ziniu_helper", lambda auth_path: FakeHelper())
    monkeypatch.setattr(tiktok_withdrawals.time, "sleep", lambda seconds: None)

    errors = []

    def run(account):
        try:
            tiktok_withdrawals.start_tiktok_browser(account, Path("tools/ziniu_auth_login_extracted.py"), 30)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(account,)) for account in ("TIKTOK-POP-E1", "TIKTOK-POP-E2-SL")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert max_active == 1
    auth.configure_ziniu_auth_concurrency(1)


def test_tiktok_email_income_no_export_data_is_no_data_status(tmp_path):
    from finance_crawler.platforms import tiktok_email_income

    tz = ZoneInfo("America/Anchorage")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )

    class DummyCtx:
        page = object()

    class NoDataError(RuntimeError):
        pass

    def raise_no_data(*args, **kwargs):
        raise NoDataError("TK 邮箱分支业务失败 export: code=22008000 message=暂无数据可导出")

    monkeypatches = [
        ("open_bills_page", lambda *args, **kwargs: None),
        ("get_seller_info", lambda *args, **kwargs: {"seller_id": "seller-1", "seller": {}, "raw": {}}),
        ("list_statement_detail", lambda *args, **kwargs: {"code": 0}),
        ("create_income_export", raise_no_data),
        ("write_capture_file", lambda *args, **kwargs: ""),
        ("collect_browser_diagnostics", lambda page: {}),
    ]
    for name, value in monkeypatches:
        setattr(tiktok_email_income, name, value)

    result = tiktok_email_income.export_tiktok_email_income_with_ctx(
        {"id": "tiktok_email_income", "platform": "E1E2", "export_folder": "销售数据", "runner": "tiktok_email.income"},
        "TIKTOK-POP-E1",
        period,
        DummyCtx(),
        tmp_path,
    )

    assert result.success
    assert result.status == "no_data"
    assert "暂无数据可导出" in result.message
