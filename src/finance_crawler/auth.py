from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass
class AuthResult:
    success: bool
    message: str
    account: str
    platform: str = ""
    cookie: str = ""
    user_agent: str = ""
    final_url: str = ""
    browser_oauth: str = ""


_ZINIAO_AUTH_CONCURRENCY = 1
# ZiNiao's local webdriver/startBrowser endpoint is unstable under concurrent starts.
_AUTH_SEMAPHORE = threading.BoundedSemaphore(_ZINIAO_AUTH_CONCURRENCY)
_AUTH_CACHE_LOCK = threading.RLock()
_AUTH_CACHE: dict[tuple[str, str], tuple[float, AuthResult]] = {}
_AUTH_CACHE_TTL_SECONDS = 40 * 60
_SHEIN_ACCOUNT_CACHE_TARGET = "__shein_account__"


def configure_ziniu_client_environment(
    install_dir: str | Path | None,
    host: str = "127.0.0.1",
    port: int | str = 16851,
) -> None:
    install_path = str(install_dir or "").strip()
    if install_path:
        os.environ["ZINIAO_INSTALL_DIR"] = install_path
    host_value = str(host or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port_value = int(port or 16851)
    except (TypeError, ValueError):
        port_value = 16851
    os.environ["ZINIAO_API_URL"] = f"http://{host_value}:{port_value}"
    os.environ["ZINIAO_WEBDRIVER_PORT"] = str(port_value)


def configure_ziniu_auth_concurrency(value: int | str | None) -> int:
    global _AUTH_SEMAPHORE, _ZINIAO_AUTH_CONCURRENCY
    try:
        concurrency = int(value or 1)
    except (TypeError, ValueError):
        concurrency = 1
    concurrency = max(1, concurrency)
    if concurrency != _ZINIAO_AUTH_CONCURRENCY:
        _ZINIAO_AUTH_CONCURRENCY = concurrency
        _AUTH_SEMAPHORE = threading.BoundedSemaphore(concurrency)
    return _ZINIAO_AUTH_CONCURRENCY


def ziniu_auth_concurrency() -> int:
    return _ZINIAO_AUTH_CONCURRENCY


@contextmanager
def ziniu_auth_slot():
    with _AUTH_SEMAPHORE:
        yield


def is_page_disconnected_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return "连接已断开" in message or "disconnected" in message


def is_browser_tab_connection_error(exc: Exception) -> bool:
    try:
        from DrissionPage.errors import PageDisconnectedError, TargetNotFoundError

        if isinstance(exc, (PageDisconnectedError, TargetNotFoundError)):
            return True
    except ImportError:
        pass
    message = str(exc or "").lower()
    return (
        is_page_disconnected_error(exc)
        or "target not found" in message
        or "targetnotfound" in message
        or "set changed size during iteration" in message
        or "no such target id" in message
    )


def get_initial_browser_tab(browser: Any, debug_port: int, max_attempts: int = 3) -> Any:
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            return browser.latest_tab
        except Exception as exc:
            if not is_browser_tab_connection_error(exc) or attempt >= max_attempts:
                raise
            time.sleep(1)


def recover_browser_tab(browser: Any, page: Any, disconnect_error: Exception, expected_host: str) -> Any:
    reconnect_error: Exception = disconnect_error
    reconnect = getattr(page, "reconnect", None)
    if callable(reconnect):
        try:
            reconnect(wait=1)
            getattr(page, "url")
            return page
        except Exception as exc:
            if not is_browser_tab_connection_error(exc):
                raise
            reconnect_error = exc

    try:
        tabs = browser.get_tabs(url=expected_host)
    except Exception as exc:
        if not is_browser_tab_connection_error(exc):
            raise
        reconnect_error = exc
        tabs = []
    if tabs:
        return tabs[0]

    try:
        latest_tab = browser.latest_tab
        latest_url = str(getattr(latest_tab, "url", "") or "").lower()
        if (
            expected_host.lower() in latest_url
            or not latest_url
            or "about:blank" in latest_url
            or latest_url.startswith("data:,")
        ):
            return latest_tab
    except Exception as exc:
        if not is_browser_tab_connection_error(exc):
            raise
        reconnect_error = exc

    raise reconnect_error


def is_shein_login_url(url: str) -> bool:
    value = str(url or "").lower()
    return "login" in value


def load_ziniu_helper(auth_path: str | Path) -> Any:
    path = Path(auth_path)
    if not path.exists():
        raise FileNotFoundError(f"紫鸟鉴权脚本不存在: {path}")
    module_name = f"finance_ziniu_auth_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载紫鸟鉴权脚本: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.ZiniuAuthLogin()


def auth_login(
    account_name: str,
    auth_path: str | Path,
    fallback_timeout_seconds: int = 30,
    target_url: str = "",
) -> AuthResult:
    key = _auth_cache_key(account_name, auth_path, target_url)
    cached = _get_cached_auth(key)
    if cached:
        return cached
    shein_account_key = _shein_account_auth_cache_key(account_name, auth_path, target_url)
    if shein_account_key:
        cached = _get_cached_auth(shein_account_key)
        if cached:
            return cached
    with ziniu_auth_slot():
        cached = _get_cached_auth(key)
        if cached:
            return cached
        if shein_account_key:
            cached = _get_cached_auth(shein_account_key)
            if cached:
                return cached
        result = _auth_login_unlocked(
            account_name,
            auth_path,
            fallback_timeout_seconds=fallback_timeout_seconds,
            target_url=target_url,
        )
        if result.success and result.cookie:
            _set_cached_auth(key, result)
            if shein_account_key:
                _set_cached_auth(shein_account_key, result)
        return result


def _auth_cache_key(account_name: str, auth_path: str | Path, target_url: str = "") -> tuple[str, str, str]:
    return str(Path(auth_path).resolve()).lower(), str(account_name or "").strip(), str(target_url or "").strip()


def _shein_account_auth_cache_key(
    account_name: str,
    auth_path: str | Path,
    target_url: str = "",
) -> tuple[str, str, str] | None:
    if not _is_shein_like_account(account_name):
        return None
    if "geiwohuo.com" not in str(target_url or "").lower():
        return None
    return _auth_cache_key(account_name, auth_path, _SHEIN_ACCOUNT_CACHE_TARGET)


def _get_cached_auth(key: tuple[str, str, str]) -> AuthResult | None:
    now = time.time()
    with _AUTH_CACHE_LOCK:
        item = _AUTH_CACHE.get(key)
        if not item:
            return None
        expires_at, result = item
        if expires_at <= now:
            _AUTH_CACHE.pop(key, None)
            return None
        return replace(result, message="success (cached)")


def _set_cached_auth(key: tuple[str, str, str], result: AuthResult) -> None:
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE[key] = (time.time() + _AUTH_CACHE_TTL_SECONDS, replace(result))


def _auth_login_unlocked(
    account_name: str,
    auth_path: str | Path,
    fallback_timeout_seconds: int = 30,
    target_url: str = "",
) -> AuthResult:
    if _is_shein_like_account(account_name) and "geiwohuo.com" in str(target_url).lower():
        return shein_mws_cookie_login_fallback(
            account_name,
            auth_path,
            target_url=target_url,
            timeout_seconds=max(60, fallback_timeout_seconds),
        )

    helper = load_ziniu_helper(auth_path)
    raw = helper.auth_login(account_name)
    message = str(raw.get("message") or "")
    final_url = str(raw.get("final_url") or "")
    cookie = str(raw.get("cookie") or "")
    needs_shein_fallback = (
        _is_shein_like_account(account_name)
        and (
            not raw.get("success")
            or not cookie
            or "login timeout" in message
            or "about:blank" in final_url
            or not final_url
        )
    )
    if needs_shein_fallback:
        return shein_mws_cookie_login_fallback(
            account_name,
            auth_path,
            timeout_seconds=fallback_timeout_seconds,
        )
    return AuthResult(
        success=bool(raw.get("success")),
        message=message,
        account=str(raw.get("account") or account_name),
        platform=str(raw.get("platform") or ""),
        cookie=cookie,
        user_agent=str(raw.get("user_agent") or ""),
        final_url=final_url,
        browser_oauth=str(raw.get("browser_oauth") or ""),
    )


def _is_shein_like_account(account_name: str) -> bool:
    value = (account_name or "").strip().lower()
    return bool(value.startswith("a") or value.startswith("f") or value.startswith("spp"))


def shein_mws_cookie_login_fallback(
    account_name: str,
    auth_path: str | Path,
    target_url: str = "https://sso.geiwohuo.com/#/mws/seller/new-account-overview",
    timeout_seconds: int = 60,
) -> AuthResult:
    return _shein_shared_cookie_login_unlocked(
        account_name,
        auth_path,
        [target_url],
        timeout_seconds=timeout_seconds,
    )


def shein_shared_cookie_login(
    account_name: str,
    auth_path: str | Path,
    target_urls: list[str],
    timeout_seconds: int = 60,
) -> AuthResult:
    with ziniu_auth_slot():
        return _shein_shared_cookie_login_unlocked(
            account_name,
            auth_path,
            target_urls,
            timeout_seconds=timeout_seconds,
        )


def _shein_shared_cookie_login_unlocked(
    account_name: str,
    auth_path: str | Path,
    target_urls: list[str],
    timeout_seconds: int = 60,
) -> AuthResult:
    helper = load_ziniu_helper(auth_path)
    ok, err = helper.ensure_client_online()
    if not ok:
        return AuthResult(False, f"client not ready: {err}", account=account_name, platform="shein")

    info, info_err = helper.get_shop_info(account_name)
    if not info:
        return AuthResult(False, info_err, account=account_name, platform="shein")

    payload = helper.build_start_browser_payload(info)
    response = None
    for attempt in range(1, 4):
        response = helper.send_http(payload)
        if response and str(response.get("statusCode")) == "0":
            break
        try:
            helper.ensure_client_online()
        except Exception:
            pass
        time.sleep(2 * attempt)
    if not response or str(response.get("statusCode")) != "0":
        return AuthResult(
            False,
            f"startBrowser failed after retry: {response}",
            account=account_name,
            platform="shein",
        )

    browser_oauth = str(response.get("browserOauth") or info.get("browserOauth") or "")
    debug_port = int(response.get("debuggingPort") or 0)
    browser = None
    page = None
    final_url = ""
    try:
        from DrissionPage import Chromium, ChromiumOptions

        browser = Chromium(ChromiumOptions().set_local_port(debug_port).existing_only())
        unique_targets = []
        for target_url in target_urls or []:
            value = str(target_url or "").strip()
            if value and value not in unique_targets:
                unique_targets.append(value)
        if not unique_targets:
            unique_targets = ["https://sso.geiwohuo.com/#/mws/seller/new-account-overview"]

        bootstrap_url = "https://sso.geiwohuo.com"
        page = get_initial_browser_tab(browser, debug_port)

        end_at = time.time() + max(10, timeout_seconds)
        cookie_str = ""
        user_agent = ""
        last_error = ""
        remaining_targets = [bootstrap_url, *unique_targets]
        current_target = ""
        target_requested = False
        reconnect_attempts = 0
        while time.time() < end_at:
            try:
                if remaining_targets and (remaining_targets[0] != current_target or not target_requested):
                    current_target = remaining_targets[0]
                    page.get(current_target)
                    target_requested = True
                final_url = str(page.url or "")
                on_login_page = is_shein_login_url(final_url)
                if on_login_page:
                    try:
                        page = helper._handle_click_for_platform(page, "shein", final_url.lower(), helper._log, browser)
                    except Exception:
                        pass
                    time.sleep(2)
                    final_url = str(page.url or "")
                    on_login_page = is_shein_login_url(final_url)
                cookies = page.cookies()
                cookie_str = "; ".join(
                    f"{item.get('name')}={item.get('value')}"
                    for item in cookies
                    if item.get("name") and item.get("value") is not None
                )
                cookie_names = {str(item.get("name") or "").lower() for item in cookies}
                has_auth_cookie = any(
                    key in name
                    for name in cookie_names
                    for key in ("sso", "token", "session", "sid", "auth")
                )
                user_agent = str(getattr(page, "user_agent", "") or "")
                if (
                    "geiwohuo.com" in final_url.lower()
                    and not on_login_page
                    and len(cookie_str) >= 50
                    and has_auth_cookie
                    and "blank" not in final_url.lower()
                ):
                    if remaining_targets:
                        remaining_targets.pop(0)
                        current_target = ""
                        target_requested = False
                        time.sleep(2)
                        continue
                    time.sleep(3)
                    return AuthResult(
                        True,
                        "success",
                        account=account_name,
                        platform="shein",
                        cookie=cookie_str,
                        user_agent=user_agent,
                        final_url=final_url,
                        browser_oauth=browser_oauth,
                    )
            except Exception as exc:
                last_error = str(exc)
                if is_browser_tab_connection_error(exc) and reconnect_attempts < 3:
                    reconnect_attempts += 1
                    try:
                        page = recover_browser_tab(browser, page, exc, "geiwohuo.com")
                        target_requested = False
                        helper._log(
                            f"[auth] SHEIN tab reconnected "
                            f"{reconnect_attempts}/3 | debug_port={debug_port}"
                        )
                        continue
                    except Exception as reconnect_exc:
                        last_error = str(reconnect_exc)
                        if reconnect_attempts < 3:
                            continue
                if is_browser_tab_connection_error(exc):
                    return AuthResult(
                        False,
                        f"shein page disconnected during login: {last_error}",
                        account=account_name,
                        platform="shein",
                        browser_oauth=browser_oauth,
                        final_url=final_url,
                    )
            time.sleep(1)
        return AuthResult(
            False,
            f"shein mws fallback login timeout, final_url={final_url}, last_error={last_error}",
            account=account_name,
            platform="shein",
            browser_oauth=browser_oauth,
            final_url=final_url,
        )
    except Exception as exc:
        return AuthResult(
            False,
            f"mws runtime exception: {exc}",
            account=account_name,
            platform="shein",
            browser_oauth=browser_oauth,
            final_url=final_url,
        )
    finally:
        if browser_oauth:
            try:
                helper.send_http(
                    {
                        "action": "stopBrowser",
                        "requestId": str(uuid.uuid4()),
                        "browserOauth": browser_oauth,
                    }
                )
            except Exception:
                pass
        try:
            if page:
                page.quit()
        except Exception:
            pass
