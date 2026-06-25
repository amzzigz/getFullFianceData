from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from finance_crawler.platforms import temu_fund_details
from finance_crawler.periods import PeriodRange


def load_auth_module():
    return temu_fund_details.load_ziniu_helper(
        temu_fund_details.Path(__file__).resolve().parents[1] / "tools" / "ziniu_auth_login_extracted.py"
    ).__class__


def install_fake_drission(monkeypatch, page):
    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    class FakeBrowser:
        def __init__(self, options):
            self.latest_tab = page

        def get_tabs(self, url=None):
            return []

        def new_tab(self, url):
            return page

    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )


def test_start_temu_browser_uses_existing_latest_tab(monkeypatch, tmp_path) -> None:
    new_tab_calls: list[str] = []
    existing_only_calls: list[bool] = []

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            existing_only_calls.append(on_off)
            return self

    page = SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL)

    class FakeBrowser:
        def __init__(self, options):
            self.latest_tab = page

        def new_tab(self, url):
            new_tab_calls.append(url)
            raise AssertionError("TEMU startup should reuse latest_tab")

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))
    monkeypatch.setattr(temu_fund_details, "temu_seller_session_ready", lambda current_page: True)
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    ctx = temu_fund_details.start_temu_browser(
        "B2",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert ctx.page is page
    assert new_tab_calls == []
    assert existing_only_calls == [True]


@pytest.mark.parametrize(
    "message",
    [
        "Set changed size during iteration",
        "No such target id: ABC123",
    ],
)
def test_temu_target_switch_errors_are_recoverable(message) -> None:
    assert temu_fund_details.is_temu_tab_connection_error(RuntimeError(message))


def test_start_temu_browser_retries_initial_latest_tab_target_race(monkeypatch, tmp_path) -> None:
    latest_tab_calls = 0
    sleeps: list[int] = []

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    page = SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL)

    class FakeBrowser:
        def __init__(self, options):
            pass

        @property
        def latest_tab(self):
            nonlocal latest_tab_calls
            latest_tab_calls += 1
            if latest_tab_calls == 1:
                raise RuntimeError("Set changed size during iteration")
            return page

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))
    monkeypatch.setattr(temu_fund_details, "temu_seller_session_ready", lambda current_page: current_page is page)
    monkeypatch.setattr(temu_fund_details.time, "sleep", sleeps.append)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    ctx = temu_fund_details.start_temu_browser(
        "B2",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert ctx.page is page
    assert latest_tab_calls == 2
    assert sleeps == [1, 3]


def test_start_temu_browser_retries_chromium_attach_target_race(monkeypatch, tmp_path) -> None:
    attach_calls = 0
    sleeps: list[int] = []

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    page = SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL)

    class FakeBrowser:
        latest_tab = page

    def fake_chromium(options):
        nonlocal attach_calls
        attach_calls += 1
        if attach_calls == 1:
            raise RuntimeError("No such target id: initial")
        return FakeBrowser()

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=fake_chromium, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))
    monkeypatch.setattr(temu_fund_details, "temu_seller_session_ready", lambda current_page: current_page is page)
    monkeypatch.setattr(temu_fund_details.time, "sleep", sleeps.append)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    ctx = temu_fund_details.start_temu_browser(
        "B2",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert ctx.page is page
    assert attach_calls == 2
    assert sleeps == [1, 3]


def test_browser_post_json_recovers_context_page_during_export() -> None:
    class DisconnectedPage:
        def run_js(self, script):
            raise RuntimeError("与页面的连接已断开")

        def reconnect(self, wait=0):
            raise RuntimeError("No such target id: gone")

    class ReplacementPage:
        url = temu_fund_details.SELLER_BILL_URL

        def run_js(self, script):
            return {"ok": True, "status": 200, "data": {"success": True, "result": {"total": 1}}}

    replacement = ReplacementPage()
    browser = SimpleNamespace(
        get_tabs=lambda url=None: [replacement] if url == "kuajingmaihuo.com" else [],
        latest_tab=replacement,
    )
    ctx = temu_fund_details.TemuBrowserContext(
        helper=SimpleNamespace(),
        browser=browser,
        page=DisconnectedPage(),
        browser_oauth="oauth",
        debug_port=12345,
    )

    data = temu_fund_details.browser_post_json(ctx, "https://example.test/api", {})

    assert data["result"]["total"] == 1
    assert ctx.page is replacement
    assert ctx.reconnect_attempts == 1


def test_page_operation_retries_when_first_recovery_also_hits_target_race() -> None:
    class DisconnectedPage:
        def reconnect(self, wait=0):
            raise RuntimeError("No such target id: gone")

    replacement = SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL)
    get_tabs_calls = 0

    class FakeBrowser:
        latest_tab = property(lambda self: (_ for _ in ()).throw(RuntimeError("No such target id: latest")))

        def get_tabs(self, url=None):
            nonlocal get_tabs_calls
            get_tabs_calls += 1
            if get_tabs_calls <= 3:
                raise RuntimeError("Set changed size during iteration")
            return [replacement]

    ctx = temu_fund_details.TemuBrowserContext(
        helper=SimpleNamespace(),
        browser=FakeBrowser(),
        page=DisconnectedPage(),
        browser_oauth="oauth",
        debug_port=12345,
    )
    operation_calls = 0

    def operation(page):
        nonlocal operation_calls
        operation_calls += 1
        if page is not replacement:
            raise RuntimeError("与页面的连接已断开")
        return "ok"

    result = temu_fund_details.run_temu_page_operation(ctx, operation)

    assert result == "ok"
    assert ctx.page is replacement
    assert ctx.reconnect_attempts == 2
    assert operation_calls == 2


def test_browser_download_file_recovers_context_page(tmp_path) -> None:
    class DisconnectedPage:
        def run_js(self, script):
            raise RuntimeError("与页面的连接已断开")

        def reconnect(self, wait=0):
            raise RuntimeError("No such target id: gone")

    class ReplacementPage:
        url = temu_fund_details.SELLER_BILL_URL

        def run_js(self, script):
            return {
                "ok": True,
                "status": 200,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "length": 4,
                "bodyBase64": "UEsDBA==",
            }

    replacement = ReplacementPage()
    browser = SimpleNamespace(
        get_tabs=lambda url=None: [replacement],
        latest_tab=replacement,
    )
    ctx = temu_fund_details.TemuBrowserContext(
        helper=SimpleNamespace(),
        browser=browser,
        page=DisconnectedPage(),
        browser_oauth="oauth",
        debug_port=12345,
    )
    output_path = tmp_path / "result.xlsx"

    size = temu_fund_details.browser_download_file(
        ctx,
        "https://seller.kuajingmaihuo.com/file.xlsx",
        output_path,
    )

    assert size == 4
    assert output_path.read_bytes() == b"PK\x03\x04"
    assert ctx.page is replacement


def test_temu_seller_session_ready_propagates_disconnect(monkeypatch) -> None:
    monkeypatch.setattr(
        temu_fund_details,
        "browser_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("与页面的连接已断开")),
    )

    with pytest.raises(RuntimeError, match="连接已断开"):
        temu_fund_details.temu_seller_session_ready(object())


def test_open_agent_target_recovers_navigation_disconnect(monkeypatch) -> None:
    base = "https://agentseller-eu.temu.com"

    class DisconnectedPage:
        url = temu_fund_details.SELLER_BILL_URL

        def run_js(self, script):
            return "mallid=123"

        def get(self, url):
            raise RuntimeError("与页面的连接已断开")

        def reconnect(self, wait=0):
            raise RuntimeError("No such target id: gone")

    class ReplacementPage:
        url = base + "/labor/bill-download-with-detail"

        def get(self, url):
            self.url = base + "/labor/bill-download-with-detail"

    replacement = ReplacementPage()
    browser = SimpleNamespace(
        get_tabs=lambda url=None: [replacement] if url == "temu.com" else [],
        latest_tab=replacement,
    )
    helper = SimpleNamespace(
        _handle_click_for_platform=lambda page, *args, **kwargs: page,
        _log=lambda message: None,
    )
    ctx = temu_fund_details.TemuBrowserContext(
        helper=helper,
        browser=browser,
        page=DisconnectedPage(),
        browser_oauth="oauth",
        debug_port=12345,
    )
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)

    temu_fund_details.open_agent_target(
        ctx,
        {"name": "欧区", "region": 2, "base": base},
        {"id": 1, "agentSellerExportParams": "params", "agentSellerExportSign": "sign"},
        123,
    )

    assert ctx.page is replacement
    assert ctx.reconnect_attempts == 1


def test_start_temu_browser_reconnects_same_tab_after_initial_disconnect(monkeypatch, tmp_path) -> None:
    attach_count = 0
    reconnect_calls: list[int] = []

    class FakeOptions:
        def set_local_port(self, port):
            assert port == 12345
            return self

        def existing_only(self, on_off=True):
            return self

    class RecoverablePage:
        connected = False

        @property
        def url(self):
            if not self.connected:
                raise RuntimeError("与页面的连接已断开")
            return temu_fund_details.SELLER_BILL_URL

        def reconnect(self, wait=0):
            reconnect_calls.append(wait)
            self.connected = True

    page = RecoverablePage()

    class FakeBrowser:
        def __init__(self, options):
            nonlocal attach_count
            attach_count += 1
            self.latest_tab = page

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))
    monkeypatch.setattr(temu_fund_details, "temu_seller_session_ready", lambda current_page: current_page is page)
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    ctx = temu_fund_details.start_temu_browser(
        "B2",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert ctx.page is page
    assert reconnect_calls == [1]
    assert attach_count == 1


def test_start_temu_browser_selects_new_temu_tab_when_original_target_is_gone(monkeypatch, tmp_path) -> None:
    from DrissionPage.errors import TargetNotFoundError

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    class GonePage:
        @property
        def url(self):
            raise RuntimeError("与页面的连接已断开")

        def reconnect(self, wait=0):
            raise TargetNotFoundError("target gone")

    replacement = SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL)
    requested_urls: list[str] = []

    class FakeBrowser:
        def __init__(self, options):
            self.latest_tab = GonePage()

        def get_tabs(self, url=None):
            requested_urls.append(url)
            return [replacement] if url == "kuajingmaihuo.com" else []

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))
    monkeypatch.setattr(
        temu_fund_details,
        "temu_seller_session_ready",
        lambda current_page: current_page is replacement,
    )
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    ctx = temu_fund_details.start_temu_browser(
        "B2",
        tmp_path / "auth.py",
        1,
        auth_slot_held=True,
    )

    assert ctx.page is replacement
    assert requested_urls == ["kuajingmaihuo.com"]


def test_start_temu_browser_holds_auth_slot_until_session_is_ready(monkeypatch, tmp_path) -> None:
    events: list[str] = []

    @contextmanager
    def fake_slot():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    page = SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL)
    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda page, *args, **kwargs: page,
        _log=lambda message: None,
    )
    install_fake_drission(monkeypatch, page)
    monkeypatch.setattr(temu_fund_details, "ziniu_auth_slot", fake_slot, raising=False)
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))
    monkeypatch.setattr(
        temu_fund_details,
        "temu_seller_session_ready",
        lambda current_page: events == ["enter"],
    )
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)

    ctx = temu_fund_details.start_temu_browser("B2", tmp_path / "auth.py", 1)

    assert ctx.page is page
    assert events == ["enter", "exit"]


def test_start_temu_browser_cleans_up_when_page_disconnects_before_return(monkeypatch, tmp_path) -> None:
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
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: sent_payloads.append(payload)
        or {"statusCode": "0", "browserOauth": "oauth", "debuggingPort": 12345},
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    install_fake_drission(monkeypatch, page)
    monkeypatch.setattr(temu_fund_details, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(temu_fund_details, "resolve_temu_shop_info", lambda helper, account: ({"browserId": "1"}, ""))

    with pytest.raises(RuntimeError, match="连接已断开"):
        temu_fund_details.start_temu_browser("B2", tmp_path / "auth.py", 1)

    assert any(payload.get("action") == "stopBrowser" for payload in sent_payloads)
    assert quit_calls == [True]


def test_stop_temu_browser_session_retries_failed_response_after_delay(monkeypatch) -> None:
    responses = iter(
        [
            {"statusCode": -1, "statusMessage": "browser stopping"},
            {"statusCode": 0},
        ]
    )
    sent_payloads: list[dict] = []
    sleeps: list[int] = []
    helper = SimpleNamespace(
        send_http=lambda payload: sent_payloads.append(payload) or next(responses),
    )
    monkeypatch.setattr(temu_fund_details.time, "sleep", sleeps.append)

    stopped = temu_fund_details.stop_temu_browser_session(helper, "oauth")

    assert stopped is True
    assert [payload["action"] for payload in sent_payloads] == ["stopBrowser", "stopBrowser"]
    assert sleeps == [1, 3]


def test_stop_temu_browser_session_cools_down_after_first_success(monkeypatch) -> None:
    helper = SimpleNamespace(send_http=lambda payload: {"statusCode": 0})
    waits: list[int] = []
    monkeypatch.setattr(
        temu_fund_details,
        "wait_for_debug_port_closed",
        lambda port, **kwargs: waits.append(port) or True,
    )

    stopped = temu_fund_details.stop_temu_browser_session(helper, "oauth", debug_port=9222)

    assert stopped is True
    assert waits == [9222]


def test_stop_temu_browser_session_blocks_new_starts_when_port_stays_open(monkeypatch) -> None:
    helper = SimpleNamespace(send_http=lambda payload: {"statusCode": 0})
    monkeypatch.setattr(temu_fund_details, "wait_for_debug_port_closed", lambda port, **kwargs: False)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    stopped = temu_fund_details.stop_temu_browser_session(helper, "oauth", debug_port=9222)

    assert stopped is False
    assert "9222" in temu_fund_details._TEMU_START_BLOCK_REASON


def test_stop_temu_browser_session_blocks_when_oauth_missing_and_port_open(monkeypatch) -> None:
    monkeypatch.setattr(temu_fund_details, "wait_for_debug_port_closed", lambda port, **kwargs: False)
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "")

    stopped = temu_fund_details.stop_temu_browser_session(
        SimpleNamespace(),
        "",
        debug_port=9222,
    )

    assert stopped is False
    assert "browserOauth" in temu_fund_details._TEMU_START_BLOCK_REASON


def test_start_temu_browser_refuses_to_start_after_cleanup_block(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(temu_fund_details, "_TEMU_START_BLOCK_REASON", "debug port 9222 still open")

    with pytest.raises(RuntimeError, match="已阻断后续启动"):
        temu_fund_details.start_temu_browser(
            "B2",
            tmp_path / "auth.py",
            1,
            auth_slot_held=True,
        )


def test_close_temu_browser_raises_when_browser_did_not_stop(monkeypatch) -> None:
    quit_calls: list[bool] = []
    ctx = SimpleNamespace(
        helper=object(),
        browser_oauth="oauth",
        debug_port=9222,
        page=SimpleNamespace(quit=lambda: quit_calls.append(True)),
    )
    monkeypatch.setattr(temu_fund_details, "stop_temu_browser_session", lambda *args, **kwargs: False)

    with pytest.raises(RuntimeError, match="未确认停止"):
        temu_fund_details.close_temu_browser(ctx)

    assert quit_calls == [True]


def test_ziniu_client_online_rejects_get_browser_list_access_denied() -> None:
    auth_class = load_auth_module()
    client = auth_class()
    client.send_http = lambda payload: {"statusCode": -10000, "statusMessage": "拒绝访问"}

    ok, error = client.ensure_client_online()

    assert ok is False
    assert "-10000" in error
    assert "权限" in error


def test_temu_authorization_ticks_all_unchecked_checkboxes() -> None:
    auth_class = load_auth_module()
    clicked: list[str] = []

    class FakeCheckbox:
        def __init__(self, name: str, checked: bool) -> None:
            self.name = name
            self.states = SimpleNamespace(is_checked=checked)

        def click(self, by_js: bool = False) -> None:
            clicked.append(self.name)
            self.states.is_checked = True

    checkboxes = [FakeCheckbox("share-shop-info", False), FakeCheckbox("dont-remind", False)]

    class FakePage:
        def eles(self, selector: str):
            return checkboxes if 'input[type="checkbox"]' in selector else []

    count = auth_class._tick_temu_agreement_checkboxes(FakePage(), lambda message: None)

    assert count == 2
    assert clicked == ["share-shop-info", "dont-remind"]


def test_open_agent_target_runs_temu_login_handler_on_region_auth_page(monkeypatch) -> None:
    class FakePage:
        url = ""

        def run_js(self, script: str):
            return "mallid=123"

        def get(self, url: str) -> None:
            self.url = (
                "https://agentseller-eu.temu.com/auth/authentication"
                "?redirectUrl=https%3A%2F%2Fagentseller-eu.temu.com%2Flabor%2Fbill-download-with-detail"
            )

    class FakeHelper:
        def __init__(self) -> None:
            self.calls = 0

        def _handle_click_for_platform(self, page, platform, current_url, log_fn, browser):
            self.calls += 1
            page.url = "https://agentseller-eu.temu.com/labor/bill-download-with-detail"
            return page

        @staticmethod
        def _log(message: str) -> None:
            pass

    ticks = iter(range(0, 500, 10))
    monkeypatch.setattr(temu_fund_details.time, "time", lambda: next(ticks))
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    page = FakePage()
    helper = FakeHelper()
    ctx = SimpleNamespace(page=page, helper=helper, browser=object())

    temu_fund_details.open_agent_target(
        ctx,
        {"name": "欧区", "region": 2, "base": "https://agentseller-eu.temu.com"},
        {"agentSellerExportParams": "params", "agentSellerExportSign": "sign"},
        123,
    )

    assert helper.calls == 1


def test_open_agent_target_runs_temu_login_handler_on_same_page_link_modal(monkeypatch) -> None:
    class FakePage:
        url = ""

        def run_js(self, script: str):
            return "mallid=123"

        def get(self, url: str) -> None:
            self.url = "https://seller.kuajingmaihuo.com/link-agent-seller?region=1"

    class FakeHelper:
        def __init__(self) -> None:
            self.calls = 0

        def _handle_click_for_platform(self, page, platform, current_url, log_fn, browser):
            self.calls += 1
            page.url = "https://agentseller.temu.com/labor/bill-download-with-detail"
            return page

        @staticmethod
        def _log(message: str) -> None:
            pass

    ticks = iter(range(0, 500, 10))
    monkeypatch.setattr(temu_fund_details.time, "time", lambda: next(ticks))
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    helper = FakeHelper()
    ctx = SimpleNamespace(page=FakePage(), helper=helper, browser=object())

    temu_fund_details.open_agent_target(
        ctx,
        {"name": "全球", "region": 1, "base": "https://agentseller.temu.com"},
        {"agentSellerExportParams": "params", "agentSellerExportSign": "sign"},
        123,
    )

    assert helper.calls == 1


def test_match_history_record_ignores_tasks_that_existed_before_export() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )
    old_record = {
        "id": 100,
        "searchExportTimeBegin": period.start_ms,
        "searchExportTimeEnd": period.end_ms,
        "fundDetailExport": True,
        "status": 2,
        "mallId": 123,
    }
    new_record = {**old_record, "id": 101}

    matched = temu_fund_details.match_history_record(
        [old_record, new_record],
        period,
        123,
        excluded_ids={100},
    )

    assert matched == new_record


def test_wait_history_record_falls_back_to_existing_when_export_is_deduplicated(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )
    old_record = {
        "id": 100,
        "searchExportTimeBegin": period.start_ms,
        "searchExportTimeEnd": period.end_ms,
        "fundDetailExport": True,
        "status": 2,
        "mallId": 123,
    }
    monkeypatch.setattr(temu_fund_details, "history_records", lambda page, mall_id: [old_record])

    matched = temu_fund_details.wait_history_record(
        object(),
        period,
        123,
        attempts=1,
        interval=0,
        excluded_ids={100},
        fallback_to_existing=True,
    )

    assert matched == old_record


def test_set_seller_mall_context_updates_cookie_for_agent_link() -> None:
    scripts: list[str] = []

    class FakePage:
        def run_js(self, script: str):
            scripts.append(script)
            return "mallid=456"

    temu_fund_details.set_seller_mall_context(FakePage(), 456)

    assert scripts
    assert "mallid=456" in scripts[0]
    assert "agentseller-mall-info-id" in scripts[0]


def test_export_primes_first_matched_mall_before_fund_queries(monkeypatch, tmp_path) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="weekly",
        start=datetime(2026, 6, 15, tzinfo=tz),
        end=datetime(2026, 6, 21, 23, 59, 59, tzinfo=tz),
    )
    primed: list[int] = []
    query_malls: list[int] = []

    class FakePage:
        url = temu_fund_details.SELLER_BILL_URL

        def get(self, url: str) -> None:
            self.url = url

    ctx = SimpleNamespace(page=FakePage(), browser=object(), helper=SimpleNamespace())
    malls = [
        {"mallId": 222, "mallName": "FaceTrue", "uniqueId": "u222"},
        {"mallId": 333, "mallName": "OtherShop", "uniqueId": "u333"},
    ]

    def fake_post(page, url, payload, mall_id=None):
        if url == temu_fund_details.PAGE_SEARCH_URL:
            query_malls.append(int(mall_id))
            return {"success": True, "errorCode": "1000000", "result": {"total": 1}}
        if url == temu_fund_details.EXPORT_URL:
            return {"success": True, "errorCode": "1000000", "result": 123}
        return {"success": True, "errorCode": "1000000", "result": {}}

    def fake_download(page, record, mall_id, output_path, *args):
        output_path.write_bytes(b"x" * 200)
        return 200, {}

    monkeypatch.setattr(temu_fund_details, "start_temu_browser", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(temu_fund_details, "close_temu_browser", lambda ctx: None)
    monkeypatch.setattr(temu_fund_details, "install_browser_request_recorder", lambda page: False)
    monkeypatch.setattr(temu_fund_details, "browser_get_user_info", lambda page: {"result": {"mallList": malls}})
    monkeypatch.setattr(temu_fund_details, "prime_seller_mall_context", lambda ctx, mall_id: primed.append(int(mall_id)))
    monkeypatch.setattr(temu_fund_details, "browser_post_json", fake_post)
    monkeypatch.setattr(temu_fund_details, "history_records", lambda *args, **kwargs: [])
    monkeypatch.setattr(temu_fund_details, "wait_history_record", lambda *args, **kwargs: {"id": 123})
    monkeypatch.setattr(temu_fund_details, "download_seller_file", fake_download)
    monkeypatch.setattr(temu_fund_details, "AGENT_TARGETS", [{"key": "seller", "name": "卖家中心", "region": 0, "base": ""}])

    result = temu_fund_details.export_temu_fund_details(
        {"id": "temu_fund_details", "export_folder": "资金明细", "shop_selectors": ["B2"]},
        "B2/B3/B5/B6/B7(原B4) 账号1",
        period,
        tmp_path / "auth.py",
        tmp_path,
    )

    assert result.success
    assert primed == [222]
    assert query_malls == [222]


def test_export_holds_auth_slot_until_browser_is_closed(monkeypatch, tmp_path) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    period = PeriodRange(
        period_type="weekly",
        start=datetime(2026, 6, 15, tzinfo=tz),
        end=datetime(2026, 6, 21, 23, 59, 59, tzinfo=tz),
    )
    events: list[str] = []
    slot_held = False

    @contextmanager
    def fake_slot():
        nonlocal slot_held
        slot_held = True
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")
            slot_held = False

    ctx = SimpleNamespace(page=SimpleNamespace(url=temu_fund_details.SELLER_BILL_URL))

    def fake_start(*args, **kwargs):
        assert slot_held
        events.append("start")
        return ctx

    def fake_close(current_ctx):
        assert slot_held
        assert current_ctx is ctx
        events.append("close")

    def fake_post(page, url, payload, mall_id=None):
        if url == temu_fund_details.PAGE_SEARCH_URL:
            return {"success": True, "errorCode": "1000000", "result": {"total": 1}}
        if url == temu_fund_details.EXPORT_URL:
            return {"success": True, "errorCode": "1000000", "result": 123}
        return {"success": True, "errorCode": "1000000", "result": {}}

    def fake_download(page, record, mall_id, output_path, *args):
        assert slot_held
        events.append("download")
        output_path.write_bytes(b"x" * 200)
        return 200, {}

    monkeypatch.setattr(temu_fund_details, "ziniu_auth_slot", fake_slot)
    monkeypatch.setattr(temu_fund_details, "start_temu_browser", fake_start)
    monkeypatch.setattr(temu_fund_details, "close_temu_browser", fake_close)
    monkeypatch.setattr(temu_fund_details, "install_browser_request_recorder", lambda page: False)
    monkeypatch.setattr(
        temu_fund_details,
        "browser_get_user_info",
        lambda page: {"result": {"mallList": [{"mallId": 222, "mallName": "FaceTrue"}]}},
    )
    monkeypatch.setattr(temu_fund_details, "prime_seller_mall_context", lambda ctx, mall_id: None)
    monkeypatch.setattr(temu_fund_details, "ensure_seller_page", lambda ctx: None)
    monkeypatch.setattr(temu_fund_details, "browser_post_json", fake_post)
    monkeypatch.setattr(temu_fund_details, "history_records", lambda *args, **kwargs: [])
    monkeypatch.setattr(temu_fund_details, "wait_history_record", lambda *args, **kwargs: {"id": 123})
    monkeypatch.setattr(temu_fund_details, "download_seller_file", fake_download)
    monkeypatch.setattr(
        temu_fund_details,
        "AGENT_TARGETS",
        [{"key": "seller", "name": "卖家中心", "region": 0, "base": ""}],
    )

    result = temu_fund_details.export_temu_fund_details(
        {"id": "temu_fund_details", "export_folder": "资金明细"},
        "B2",
        period,
        tmp_path / "auth.py",
        tmp_path,
    )

    assert result.success
    assert events == ["enter", "start", "download", "close", "exit"]


def test_existing_export_is_reused_only_when_file_has_content(tmp_path) -> None:
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")
    completed = tmp_path / "completed.xlsx"
    completed.write_bytes(b"x" * 200)

    assert temu_fund_details.existing_export_size(empty) == 0
    assert temu_fund_details.existing_export_size(completed) == 200


def test_validate_temu_outputs_rejects_missing_declared_files(tmp_path) -> None:
    present = tmp_path / "present.xlsx"
    present.write_bytes(b"x" * 200)
    missing = tmp_path / "missing.xlsx"
    mall_results = [
        {
            "label": "B1_Shop",
            "regionResults": [
                {"regionName": "卖家中心", "outputPath": str(present)},
                {"regionName": "全球", "outputPath": str(missing)},
            ],
        }
    ]

    missing_items = temu_fund_details.validate_temu_outputs(mall_results)

    assert missing_items == [f"B1_Shop/全球: {missing}"]


def test_download_seller_file_retries_when_export_task_is_not_ready(monkeypatch, tmp_path) -> None:
    responses = iter(
        [
            {"success": False, "errorCode": "2000000", "errorMsg": "导出任务未完成"},
            {"success": True, "errorCode": "1000000", "result": {"fileUrl": "https://example.test/file.xlsx"}},
        ]
    )
    posted: list[dict] = []

    def fake_post(page, url, payload, mall_id):
        posted.append(payload)
        return next(responses)

    monkeypatch.setattr(temu_fund_details, "browser_post_json", fake_post)
    monkeypatch.setattr(temu_fund_details, "browser_download_file", lambda page, url, output: output.write_bytes(b"PK") or 2)
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)

    output = tmp_path / "seller.xlsx"
    size, debug = temu_fund_details.download_seller_file(
        object(),
        {"id": 123},
        456,
        output,
        attempts=3,
        interval=0,
    )

    assert size == 2
    assert output.exists()
    assert len(posted) == 2
    assert debug["download_attempts"] == 2


def test_temu_same_page_authorization_has_priority_over_existing_agent_tab(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeCheckbox:
        states = SimpleNamespace(is_checked=False)

        def click(self, by_js: bool = False) -> None:
            clicks.append("checkbox")
            self.states.is_checked = True

    class FakeButton:
        def click(self, by_js: bool = False) -> None:
            clicks.append("authorize")

    class SellerTab:
        url = "https://seller.kuajingmaihuo.com/link-agent-seller?region=1"

        def eles(self, selector: str):
            return [FakeCheckbox()]

        def ele(self, selector: str, timeout: float = 0):
            if "确认授权并前往" in selector:
                return FakeButton()
            return None

    class SuccessTab:
        url = "https://agentseller.temu.com/labor/bill-download-with-detail"

    seller_tab = SellerTab()
    success_tab = SuccessTab()
    browser = SimpleNamespace(get_tabs=lambda: [success_tab, seller_tab])

    result = auth_class._handle_click_for_platform(
        seller_tab,
        "temu_business",
        seller_tab.url,
        lambda message: None,
        browser,
    )

    assert clicks == ["checkbox", "authorize"]
    assert result is seller_tab


def test_temu_login_form_does_not_submit_before_autofill(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeButton:
        def __init__(self, name: str) -> None:
            self.name = name

        def click(self, by_js: bool = False) -> None:
            clicks.append(self.name)

    class FakePage:
        url = "https://seller.kuajingmaihuo.com/settle/seller-login"

        def run_js(self, script: str):
            return {
                "phoneVisible": True,
                "phoneValue": "",
                "passwordVisible": True,
                "passwordValue": "",
                "submitDisabled": False,
                "submittedRecently": False,
            }

        def ele(self, selector: str, timeout: float = 0):
            if selector == "text=手机号登录":
                return FakeButton("phone_tab")
            if selector == "text=我已阅读并同意":
                return FakeButton("agreement")
            if selector.startswith("xpath://button") and "登录" in selector:
                return FakeButton("login")
            return None

    page = FakePage()
    result = auth_class._handle_click_for_platform(
        page,
        "temu_business",
        page.url,
        lambda message: None,
        None,
    )

    assert result is page
    assert clicks == []


def test_temu_login_form_submits_filled_form_without_reclicking_phone_tab(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeButton:
        def __init__(self, name: str) -> None:
            self.name = name

        def click(self, by_js: bool = False) -> None:
            clicks.append(self.name)

    class FakePage:
        def run_js(self, script: str):
            if "__financeCrawlerTemuLoginSubmittedAt = Date.now()" in script:
                return True
            return {
                "phoneVisible": True,
                "phoneValue": "demo-phone-1",
                "passwordVisible": True,
                "passwordValue": "saved-password",
                "submitDisabled": False,
                "submittedRecently": False,
            }

        def ele(self, selector: str, timeout: float = 0):
            if selector == "text=手机号登录":
                return FakeButton("phone_tab")
            if selector == "text=我已阅读并同意":
                return FakeButton("agreement")
            if selector.startswith("xpath://button") and "登录" in selector:
                return FakeButton("login")
            return None

    auth_class._click_temu_login_form(FakePage(), lambda message: None)

    assert clicks == ["agreement", "login"]


def test_temu_phone_tab_never_falls_back_to_js_click(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeTab:
        def hover(self) -> None:
            clicks.append("hover")

        def click(self, by_js: bool = False) -> None:
            clicks.append("js" if by_js else "native")
            raise RuntimeError("native click failed")

    class FakePage:
        def run_js(self, script: str):
            return {
                "phoneVisible": False,
                "phoneValue": "",
                "passwordVisible": False,
                "passwordValue": "",
                "submitDisabled": False,
                "switchedRecently": False,
                "submittedRecently": False,
            }

        def ele(self, selector: str, timeout: float = 0):
            return FakeTab() if selector == "text=手机号登录" else None

    auth_class._click_temu_login_form(FakePage(), lambda message: None)

    assert clicks == ["hover", "native"]


def test_temu_login_form_uses_xpath_fallback_when_exact_text_locator_misses(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []
    switched = False

    class FakeButton:
        def __init__(self, name: str) -> None:
            self.name = name

        def hover(self) -> None:
            clicks.append(f"hover:{self.name}")

        def click(self, by_js: bool = False) -> None:
            nonlocal switched
            clicks.append(f"{'js' if by_js else 'native'}:{self.name}")
            if self.name == "phone_tab":
                switched = True

    class FakePage:
        def run_js(self, script: str):
            if "__financeCrawlerTemuPhoneSwitchedAt = Date.now()" in script:
                return True
            if "__financeCrawlerTemuLoginSubmittedAt = Date.now()" in script:
                return True
            return {
                "phoneVisible": switched,
                "phoneValue": "demo-phone" if switched else "",
                "phoneAutofilled": switched,
                "passwordVisible": switched,
                "passwordValue": "saved-password" if switched else "",
                "passwordAutofilled": switched,
                "agreementPresent": False,
                "agreementChecked": False,
                "submitDisabled": False,
                "switchedRecently": False,
                "submittedRecently": False,
            }

        def eles(self, selector: str):
            return []

        def ele(self, selector: str, timeout: float = 0):
            if selector == 'xpath://div[normalize-space(.)="手机号登录"]':
                return FakeButton("phone_tab")
            if selector.startswith("xpath://button") and "登录" in selector:
                return FakeButton("login")
            return None

    assert auth_class._click_temu_login_form(FakePage(), lambda message: None) is True
    assert clicks == ["hover:phone_tab", "native:phone_tab", "native:login"]


def test_temu_login_form_accepts_protected_browser_autofill(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeButton:
        def click(self, by_js: bool = False) -> None:
            clicks.append("login")

    class FakePage:
        def run_js(self, script: str):
            if "__financeCrawlerTemuLoginSubmittedAt = Date.now()" in script:
                return True
            return {
                "phoneVisible": True,
                "phoneValue": "demo-phone-2",
                "phoneAutofilled": True,
                "passwordVisible": True,
                "passwordValue": "",
                "passwordAutofilled": True,
                "submitDisabled": False,
                "switchedRecently": False,
                "submittedRecently": False,
            }

        def eles(self, selector: str):
            return []

        def ele(self, selector: str, timeout: float = 0):
            if selector.startswith("xpath://button") and "登录" in selector:
                return FakeButton()
            return None

    assert auth_class._click_temu_login_form(FakePage(), lambda message: None) is True
    assert clicks == ["login"]


def test_temu_login_form_accepts_hidden_protected_password_after_wait(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeButton:
        def click(self, by_js: bool = False) -> None:
            clicks.append("login")

    class FakePage:
        def run_js(self, script: str):
            if "__financeCrawlerTemuLoginSubmittedAt = Date.now()" in script:
                return True
            return {
                "phoneVisible": True,
                "phoneValue": "demo-phone-2",
                "phoneAutofilled": False,
                "passwordVisible": True,
                "passwordValue": "",
                "passwordAutofilled": False,
                "submitDisabled": False,
                "agreementPresent": False,
                "agreementChecked": False,
                "switchedRecently": True,
                "submittedRecently": False,
            }

        def eles(self, selector: str):
            return []

        def ele(self, selector: str, timeout: float = 0):
            if selector.startswith("xpath://button") and "登录" in selector:
                return FakeButton()
            return None

    assert auth_class._click_temu_login_form(FakePage(), lambda message: None) is True
    assert clicks == ["login"]


def test_temu_login_form_does_not_submit_when_agreement_stays_unchecked(monkeypatch) -> None:
    auth_class = load_auth_module()
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)
    clicks: list[str] = []

    class FakeButton:
        def __init__(self, name: str) -> None:
            self.name = name

        def click(self, by_js: bool = False) -> None:
            clicks.append(self.name)

    class FakeCheckbox(FakeButton):
        states = SimpleNamespace(is_checked=False)

    class FakePage:
        def run_js(self, script: str):
            return {
                "phoneVisible": True,
                "phoneValue": "demo-phone-2",
                "passwordVisible": True,
                "passwordValue": "saved-password",
                "submitDisabled": False,
                "agreementPresent": True,
                "agreementChecked": False,
                "switchedRecently": False,
                "submittedRecently": False,
            }

        def eles(self, selector: str):
            return [FakeCheckbox("checkbox")] if 'input[type="checkbox"]' in selector else []

        def ele(self, selector: str, timeout: float = 0):
            if selector.startswith("xpath://button") and "登录" in selector:
                return FakeButton("login")
            return None

    assert auth_class._click_temu_login_form(FakePage(), lambda message: None) is False
    assert "login" not in clicks


def test_build_agent_authorization_url_includes_shop_identity_and_code() -> None:
    url = temu_fund_details.build_agent_authorization_url(
        "https://agentseller.temu.com",
        "https://agentseller.temu.com/labor/bill-download-with-detail?params=p&sign=s",
        "shop-unique-id",
        "auth-code",
    )

    assert url.startswith("https://agentseller.temu.com/main/authentication?")
    assert "uniqueId=shop-unique-id" in url
    assert "asCode=auth-code" in url
    assert "redirectUrl=" in url


def test_agent_context_uses_accounts_default_mall() -> None:
    malls = [{"mallId": 111}, {"mallId": 222}]

    assert temu_fund_details.agent_context_mall_id(malls) == 111


def test_agent_request_mall_context_varies_by_region() -> None:
    assert temu_fund_details.agent_request_mall_id("global", 111, 222) == 222
    assert temu_fund_details.agent_request_mall_id("eu", 111, 222) == 222
    assert temu_fund_details.agent_request_mall_id("us", 111, 222) == 222


def test_download_agent_file_waits_until_export_task_is_ready(monkeypatch, tmp_path) -> None:
    responses = [
        {"success": False, "errorCode": 2000000, "errorMsg": "导出任务未完成"},
        {"success": True, "errorCode": 1000000, "result": {"fileUrl": "https://example.test/file.xlsx"}},
    ]
    calls: list[tuple[str, dict, int]] = []

    def fake_post(page, url, payload, mall_id=None):
        calls.append((url, payload, mall_id))
        if url.endswith("/api/seller/auth/userInfo"):
            return {"success": True, "errorCode": 1000000}
        if url.endswith("/api/merchant/file/export"):
            return {"success": True, "errorCode": 1000000, "result": 777}
        if url.endswith("/api/merchant/file/export/download"):
            return responses.pop(0)
        raise AssertionError(url)

    monkeypatch.setattr(temu_fund_details, "open_agent_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(temu_fund_details, "browser_post_json", fake_post)
    monkeypatch.setattr(temu_fund_details, "browser_download_file", lambda page, url, output: output.write_bytes(b"PK\x03\x04") or 4)
    monkeypatch.setattr(temu_fund_details.time, "sleep", lambda seconds: None)

    ctx = SimpleNamespace(page=object())
    output = tmp_path / "agent.xlsx"

    size, debug = temu_fund_details.download_agent_file(
        ctx,
        {"key": "global", "base": "https://agentseller.temu.com"},
        {"agentSellerExportParams": "params", "agentSellerExportSign": "sign"},
        mall_id=123,
        agent_mall_id=456,
        unique_id="shop",
        output_path=output,
        attempts=2,
        interval=0,
    )

    download_calls = [call for call in calls if call[0].endswith("/api/merchant/file/export/download")]
    assert len(download_calls) == 2
    assert size == 4
    assert output.read_bytes() == b"PK\x03\x04"
    assert debug["download_attempts"] == 2


def test_resolve_temu_shop_info_matches_stable_fields_after_browser_rename() -> None:
    class FakeHelper:
        def send_http(self, payload):
            return {
                "statusCode": 0,
                "browserList": [
                    {
                        "browserName": "B22/B12/B13-主账号-LY",
                        "browserId": 27453895761118,
                        "browserOauth": "secret",
                        "platform_id": 149,
                        "siteId": 391,
                        "store_username": "demo-store-user",
                    }
                ],
            }

    info, err = temu_fund_details.resolve_temu_shop_info(
        FakeHelper(),
        {
            "label": "LY stable label",
            "platform_id": 149,
            "siteId": 391,
            "store_username": "demo-store-user",
        },
    )

    assert err == ""
    assert info["name"] == "B22/B12/B13-主账号-LY"
    assert info["browserId"] == "27453895761118"


def test_temu_seller_session_ready_rejects_user_info_403(monkeypatch) -> None:
    def fake_post(page, url, payload, mall_id=None):
        return {"error_code": 40001, "error_msg": ""}

    monkeypatch.setattr(temu_fund_details, "browser_post_json", fake_post)

    assert temu_fund_details.temu_seller_session_ready(object()) is False
