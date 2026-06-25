from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms import shenhe_report_bill


def install_fake_drission(monkeypatch, page, existing_only_calls=None):
    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            if existing_only_calls is not None:
                existing_only_calls.append(on_off)
            return self

    class FakeBrowser:
        def __init__(self, options):
            self.latest_tab = page

        def new_tab(self, url):
            return page

        def get_tabs(self, url=None):
            return []

    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )


def test_start_logged_in_page_holds_auth_slot_until_page_is_ready(monkeypatch, tmp_path) -> None:
    events: list[str] = []

    @contextmanager
    def fake_slot():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    class ReadyPage:
        @property
        def url(self):
            return shenhe_report_bill.TARGET_URL if events == ["enter"] else "about:blank"

    page = ReadyPage()
    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
    )
    install_fake_drission(monkeypatch, page)
    monkeypatch.setattr(shenhe_report_bill, "ziniu_auth_slot", fake_slot, raising=False)
    monkeypatch.setattr(shenhe_report_bill, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(shenhe_report_bill.time, "sleep", lambda seconds: None)

    _, _, returned_page, _ = shenhe_report_bill.start_logged_in_page("A1Y", tmp_path / "auth.py", 1)

    assert returned_page is page
    assert events == ["enter", "exit"]


def test_start_logged_in_page_cleans_up_when_page_disconnects_before_return(monkeypatch, tmp_path) -> None:
    sent_payloads: list[dict] = []
    quit_calls: list[bool] = []

    class DisconnectedPage:
        @property
        def url(self):
            raise RuntimeError("与页面的连接已断开")

        def quit(self):
            quit_calls.append(True)

    page = DisconnectedPage()
    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: sent_payloads.append(payload)
        or {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
    )
    install_fake_drission(monkeypatch, page)
    monkeypatch.setattr(shenhe_report_bill, "load_ziniu_helper", lambda auth_path: helper)

    with pytest.raises(RuntimeError, match="连接已断开"):
        shenhe_report_bill.start_logged_in_page("A1Y", tmp_path / "auth.py", 1)

    assert any(payload.get("action") == "stopBrowser" for payload in sent_payloads)
    assert quit_calls == [True]


def test_start_logged_in_page_uses_existing_browser_and_reconnects_tab(monkeypatch, tmp_path) -> None:
    existing_only_calls: list[bool] = []
    reconnect_calls: list[int] = []

    class RecoverablePage:
        connected = False

        @property
        def url(self):
            if not self.connected:
                raise RuntimeError("与页面的连接已断开")
            return shenhe_report_bill.TARGET_URL

        def reconnect(self, wait=0):
            reconnect_calls.append(wait)
            self.connected = True

    page = RecoverablePage()
    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
    )
    install_fake_drission(monkeypatch, page, existing_only_calls)
    monkeypatch.setattr(shenhe_report_bill, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(shenhe_report_bill.time, "sleep", lambda seconds: None)

    _, _, returned_page, _ = shenhe_report_bill.start_logged_in_page(
        "A1Y",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert returned_page is page
    assert existing_only_calls == [True]
    assert reconnect_calls == [1]


def test_start_logged_in_page_reuses_latest_tab_without_creating_target(monkeypatch, tmp_path) -> None:
    navigations: list[str] = []
    new_tab_calls: list[str] = []

    class ExistingPage:
        current_url = "about:blank"

        @property
        def url(self):
            return self.current_url

        def get(self, url):
            navigations.append(url)
            self.current_url = url

    page = ExistingPage()

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    class FakeBrowser:
        def __init__(self, options):
            self.latest_tab = page

        def new_tab(self, url):
            new_tab_calls.append(url)
            raise AssertionError("existing ZiNiao target must be reused")

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(shenhe_report_bill, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(shenhe_report_bill.time, "sleep", lambda seconds: None)

    _, _, returned_page, _ = shenhe_report_bill.start_logged_in_page(
        "A1Y",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert returned_page is page
    assert new_tab_calls == []
    assert navigations == [shenhe_report_bill.TARGET_URL]


def test_start_logged_in_page_retries_initial_target_switch(monkeypatch, tmp_path) -> None:
    latest_tab_calls = 0

    class ExistingPage:
        current_url = "about:blank"

        @property
        def url(self):
            return self.current_url

        def get(self, url):
            self.current_url = url

    page = ExistingPage()

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    class FakeBrowser:
        def __init__(self, options):
            pass

        @property
        def latest_tab(self):
            nonlocal latest_tab_calls
            latest_tab_calls += 1
            if latest_tab_calls == 1:
                raise RuntimeError("No such target id: ABC123")
            return page

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(shenhe_report_bill, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(shenhe_report_bill.time, "sleep", lambda seconds: None)

    _, _, returned_page, _ = shenhe_report_bill.start_logged_in_page(
        "A1Y",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert returned_page is page
    assert latest_tab_calls == 2


def test_browser_fetch_replaces_destroyed_shenhe_tab_and_reuses_it(monkeypatch) -> None:
    requested_hosts: list[str] = []

    class GonePage:
        def run_js(self, script):
            raise RuntimeError("与页面的连接已断开")

        def reconnect(self, wait=0):
            raise RuntimeError("target not found")

    class ReplacementPage:
        def run_js(self, script):
            return {"ok": True, "status": 200, "url": shenhe_report_bill.LIST_URL, "data": {"code": "0"}}

    replacement = ReplacementPage()

    class FakeBrowser:
        latest_tab = GonePage()

        def get_tabs(self, url=None):
            requested_hosts.append(url)
            return [replacement] if url == "shenhe888.com" else []

    page_ref = shenhe_report_bill.ShenhePageRef(FakeBrowser(), FakeBrowser.latest_tab)
    monkeypatch.setattr(shenhe_report_bill.time, "sleep", lambda seconds: None)

    result = shenhe_report_bill.browser_fetch(page_ref, shenhe_report_bill.LIST_URL, "GET", None, 30)

    assert result == {"code": "0"}
    assert page_ref.page is replacement
    assert requested_hosts == ["shenhe888.com"]


def test_export_holds_auth_slot_until_browser_is_closed(monkeypatch, tmp_path) -> None:
    events: list[str] = []
    helper = page = object()

    @contextmanager
    def fake_slot():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fake_start(*args, **kwargs):
        assert events == ["enter"]
        return helper, object(), page, "oauth"

    def fake_list(*args, **kwargs):
        assert events == ["enter"]
        return [], {"code": "0", "info": {"list": {"data": []}}}, []

    def fake_close(*args, **kwargs):
        assert events == ["enter"]
        events.append("closed")

    monkeypatch.setattr(shenhe_report_bill, "ziniu_auth_slot", fake_slot)
    monkeypatch.setattr(shenhe_report_bill, "start_logged_in_page", fake_start)
    monkeypatch.setattr(shenhe_report_bill, "list_report_bills", fake_list)
    monkeypatch.setattr(shenhe_report_bill, "close_browser", fake_close)
    monkeypatch.setattr(shenhe_report_bill, "write_capture_file", lambda *args, **kwargs: "")
    tz = ZoneInfo("Asia/Shanghai")

    result = shenhe_report_bill.export_shenhe_report_bill(
        {"id": "shein_a1y_a4y_report_bill", "platform": "shein", "export_folder": "销售数据平台费用"},
        "A1Y-主账号-CX",
        PeriodRange(
            period_type="monthly",
            start=datetime(2026, 5, 1, tzinfo=tz),
            end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
        ),
        tmp_path / "auth.py",
        tmp_path,
    )

    assert isinstance(result, TaskResult)
    assert result.success
    assert events == ["enter", "closed", "exit"]
