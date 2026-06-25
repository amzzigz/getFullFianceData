import sys
import threading
import time
from types import SimpleNamespace

from finance_crawler import auth


def test_page_disconnected_errors_are_detected_for_fast_retry():
    assert auth.is_page_disconnected_error(Exception("与页面的连接已断开。"))
    assert auth.is_page_disconnected_error(RuntimeError("page disconnected"))
    assert not auth.is_page_disconnected_error(Exception("login timeout"))


def test_browser_target_switch_errors_are_recoverable():
    assert auth.is_browser_tab_connection_error(RuntimeError("Set changed size during iteration"))
    assert auth.is_browser_tab_connection_error(RuntimeError("No such target id: ABC123"))


def test_get_initial_browser_tab_retries_target_switch(monkeypatch):
    calls = 0
    page = object()

    class FakeBrowser:
        @property
        def latest_tab(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Set changed size during iteration")
            return page

    sleeps = []
    monkeypatch.setattr(auth.time, "sleep", sleeps.append)

    assert auth.get_initial_browser_tab(FakeBrowser(), 12345) is page
    assert calls == 2
    assert sleeps == [1]


def test_shein_login_urls_are_not_ready_pages():
    assert auth.is_shein_login_url("https://sso.geiwohuo.com/#/login")
    assert auth.is_shein_login_url("https://sso.geiwohuo.com/login?redirect=x")
    assert not auth.is_shein_login_url("https://sso.geiwohuo.com/#/gsfs/finance/reportOrder/dualMode")


def test_auth_login_serializes_uncached_ziniu_starts(tmp_path, monkeypatch):
    auth.configure_ziniu_auth_concurrency(1)
    auth_path = tmp_path / "auth.py"
    auth_path.write_text("# placeholder\n", encoding="utf-8")
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    start_barrier = threading.Barrier(2)

    def fake_auth_login_unlocked(account_name, received_auth_path, fallback_timeout_seconds=30, target_url=""):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with active_lock:
            active -= 1
        return auth.AuthResult(
            True,
            "success",
            account=account_name,
            platform="shein",
            cookie=f"sso={account_name}; session=ok",
            user_agent="ua",
            final_url=target_url,
        )

    monkeypatch.setattr(auth, "_auth_login_unlocked", fake_auth_login_unlocked)
    auth._AUTH_CACHE.clear()
    results = []

    def worker(index):
        start_barrier.wait(timeout=2)
        results.append(
            auth.auth_login(
                f"A{index}-主账号",
                auth_path,
                target_url=f"https://sso.geiwohuo.com/#/gsfs/{index}",
            )
        )

    threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert len(results) == 2
    assert all(result.success for result in results)
    assert max_active == 1


def test_auth_login_allows_configured_ziniu_concurrency(tmp_path, monkeypatch):
    auth.configure_ziniu_auth_concurrency(2)
    auth_path = tmp_path / "auth.py"
    auth_path.write_text("# placeholder\n", encoding="utf-8")
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    start_barrier = threading.Barrier(2)

    def fake_auth_login_unlocked(account_name, received_auth_path, fallback_timeout_seconds=30, target_url=""):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with active_lock:
            active -= 1
        return auth.AuthResult(
            True,
            "success",
            account=account_name,
            platform="shein",
            cookie=f"sso={account_name}; session=ok",
            user_agent="ua",
            final_url=target_url,
        )

    monkeypatch.setattr(auth, "_auth_login_unlocked", fake_auth_login_unlocked)
    auth._AUTH_CACHE.clear()
    results = []

    def worker(index):
        start_barrier.wait(timeout=2)
        results.append(
            auth.auth_login(
                f"A{index}-主账号",
                auth_path,
                target_url=f"https://sso.geiwohuo.com/#/gsfs/{index}",
            )
        )

    threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert len(results) == 2
    assert all(result.success for result in results)
    assert max_active == 2
    auth.configure_ziniu_auth_concurrency(1)


def test_shein_target_auth_reuses_account_level_cookie(tmp_path, monkeypatch):
    auth.configure_ziniu_auth_concurrency(1)
    auth_path = tmp_path / "auth.py"
    auth_path.write_text("# placeholder\n", encoding="utf-8")
    calls = []

    def fake_auth_login_unlocked(account_name, received_auth_path, fallback_timeout_seconds=30, target_url=""):
        calls.append(target_url)
        return auth.AuthResult(
            True,
            "success",
            account=account_name,
            platform="shein",
            cookie="sso=1; session=2;" + "x" * 60,
            user_agent="ua",
            final_url=target_url,
        )

    monkeypatch.setattr(auth, "_auth_login_unlocked", fake_auth_login_unlocked)
    auth._AUTH_CACHE.clear()

    first = auth.auth_login(
        "A20-主账号-CX(原A8)-6579YB",
        auth_path,
        target_url="https://sso.geiwohuo.com/#/mils/report",
    )
    second = auth.auth_login(
        "A20-主账号-CX(原A8)-6579YB",
        auth_path,
        target_url="https://sso.geiwohuo.com/#/gsfs/finance/reportOrder/dualMode",
    )

    assert first.success
    assert second.success
    assert second.message == "success (cached)"
    assert calls == ["https://sso.geiwohuo.com/#/mils/report"]


def test_shared_shein_login_uses_existing_browser_and_reconnects_tab(monkeypatch, tmp_path):
    existing_only_calls = []
    reconnect_calls = []
    target_url = "https://sso.geiwohuo.com/#/gsfs/finance/reportOrder/dualMode"

    class FakeOptions:
        def set_local_port(self, port):
            assert port == 12345
            return self

        def existing_only(self, on_off=True):
            existing_only_calls.append(on_off)
            return self

    class RecoverablePage:
        connected = False
        current_url = "https://sso.geiwohuo.com"

        def get(self, url):
            self.current_url = url

        @property
        def url(self):
            if not self.connected:
                raise RuntimeError("与页面的连接已断开")
            return self.current_url

        def reconnect(self, wait=0):
            reconnect_calls.append(wait)
            self.connected = True

        def cookies(self):
            return [
                {"name": "sso_token", "value": "x" * 40},
                {"name": "session_id", "value": "y" * 40},
            ]

        user_agent = "ua"

        def quit(self):
            return None

    page = RecoverablePage()

    class FakeBrowser:
        def __init__(self, options):
            self.latest_tab = page

        def new_tab(self, url):
            page.current_url = url
            return page

        def get_tabs(self, url=None):
            return []

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {
            "statusCode": "0",
            "browserOauth": "oauth",
            "debuggingPort": 12345,
        },
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(auth, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)

    result = auth._shein_shared_cookie_login_unlocked(
        "A1-主账号",
        tmp_path / "auth.py",
        [target_url],
        timeout_seconds=1,
    )

    assert result.success
    assert result.final_url == target_url
    assert existing_only_calls == [True]
    assert reconnect_calls == [1]


def test_shared_shein_login_reuses_latest_tab_without_creating_target(monkeypatch, tmp_path):
    navigations = []
    new_tab_calls = []
    latest_tab_calls = 0
    target_url = "https://sso.geiwohuo.com/#/mils/report"

    class FakeOptions:
        def set_local_port(self, port):
            return self

        def existing_only(self, on_off=True):
            return self

    class ExistingPage:
        current_url = "about:blank"

        @property
        def url(self):
            return self.current_url

        def get(self, url):
            navigations.append(url)
            self.current_url = url

        def cookies(self):
            return [
                {"name": "sso_token", "value": "x" * 40},
                {"name": "session_id", "value": "y" * 40},
            ]

        user_agent = "ua"

        def quit(self):
            return None

    page = ExistingPage()

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

        def new_tab(self, url):
            new_tab_calls.append(url)
            raise AssertionError("existing ZiNiao target must be reused")

        def get_tabs(self, url=None):
            return []

    helper = SimpleNamespace(
        ensure_client_online=lambda: (True, ""),
        get_shop_info=lambda account: ({"browserId": "1"}, ""),
        build_start_browser_payload=lambda info: {"action": "startBrowser"},
        send_http=lambda payload: {
            "statusCode": "0",
            "browserOauth": "oauth",
            "debuggingPort": 12345,
        },
        _handle_click_for_platform=lambda current_page, *args, **kwargs: current_page,
        _log=lambda message: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions),
    )
    monkeypatch.setattr(auth, "load_ziniu_helper", lambda auth_path: helper)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)

    result = auth._shein_shared_cookie_login_unlocked(
        "A1-主账号",
        tmp_path / "auth.py",
        [target_url],
        timeout_seconds=1,
    )

    assert result.success
    assert new_tab_calls == []
    assert latest_tab_calls == 2
    assert navigations == ["https://sso.geiwohuo.com", target_url]


def test_recover_browser_tab_selects_existing_expected_host_tab():
    class GonePage:
        @property
        def url(self):
            raise RuntimeError("与页面的连接已断开")

        def reconnect(self, wait=0):
            raise RuntimeError("target not found")

    replacement = SimpleNamespace(url="https://sso.geiwohuo.com/#/mils/report")
    requested_hosts = []

    class FakeBrowser:
        latest_tab = GonePage()

        def get_tabs(self, url=None):
            requested_hosts.append(url)
            return [replacement] if url == "geiwohuo.com" else []

    recovered = auth.recover_browser_tab(
        FakeBrowser(),
        FakeBrowser.latest_tab,
        RuntimeError("与页面的连接已断开"),
        "geiwohuo.com",
    )

    assert recovered is replacement
    assert requested_hosts == ["geiwohuo.com"]
