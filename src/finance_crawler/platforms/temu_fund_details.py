from __future__ import annotations

import json
import re
import socket
import time
import uuid
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from finance_crawler.auth import is_page_disconnected_error, load_ziniu_helper, ziniu_auth_slot
from finance_crawler.debug_files import write_capture_file
from finance_crawler.diagnostics import collect_browser_diagnostics, diagnostic_enabled, install_browser_request_recorder
from finance_crawler.filenames import account_code, ascii_slug, download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import export_folder_name, safe_name


SELLER_BASE = "https://seller.kuajingmaihuo.com"
SELLER_BILL_URL = f"{SELLER_BASE}/labor/bill"
PAGE_SEARCH_URL = f"{SELLER_BASE}/api/merchant/fund/detail/pageSearch"
EXPORT_URL = f"{SELLER_BASE}/api/merchant/file/export"
HISTORY_URL = f"{SELLER_BASE}/api/merchant/file/export/history/page"
DOWNLOAD_URL = f"{SELLER_BASE}/api/merchant/file/export/download"
USER_INFO_URL = f"{SELLER_BASE}/bg/quiet/api/mms/userInfo"
OBTAIN_CODE_URL = f"{SELLER_BASE}/bg/quiet/api/auth/obtainCode"
TASK_TYPE = 19
AGENT_TASK_TYPE = 31
AGENT_TARGETS = [
    {"key": "seller", "name": "卖家中心", "region": 0, "base": ""},
    {"key": "global", "name": "全球", "region": 1, "base": "https://agentseller.temu.com"},
    {"key": "eu", "name": "欧区", "region": 2, "base": "https://agentseller-eu.temu.com"},
    {"key": "us", "name": "美国", "region": 3, "base": "https://agentseller-us.temu.com"},
]


@dataclass
class TemuBrowserContext:
    helper: Any
    browser: Any
    page: Any
    browser_oauth: str
    debug_port: int = 0
    reconnect_attempts: int = 0


_TEMU_START_BLOCK_REASON = ""


def temu_account_label(account_name: Any) -> str:
    if isinstance(account_name, dict):
        return str(
            account_name.get("label")
            or account_name.get("name")
            or account_name.get("browserName")
            or account_name.get("store_username")
            or ""
        )
    return str(account_name or "")


def account_codes(account_name: Any) -> list[str]:
    head = re.split(r"[-\s]", temu_account_label(account_name).strip(), maxsplit=1)[0]
    return [item for item in re.findall(r"B\d+", head.upper())]


def mall_label(account_name: str, mall: dict[str, Any], index: int) -> str:
    codes = account_codes(account_name)
    code = codes[index] if index < len(codes) else (codes[0] if codes else safe_name(account_name))
    name = ascii_slug(str(mall.get("mallName") or mall.get("name") or mall.get("mallId") or f"shop{index + 1}"), f"shop{index + 1}")
    return f"{code}_{name}"


def shop_matches(account_name: str, mall: dict[str, Any], index: int, selectors: list[str]) -> bool:
    if not selectors:
        return True
    label = mall_label(account_name, mall, index).lower()
    name = str(mall.get("mallName") or "").lower()
    mall_id = str(mall.get("mallId") or "").lower()
    code = label.split("_", 1)[0].lower()
    haystack = " ".join([label, name, mall_id, code])
    return any(str(selector or "").strip().lower() in haystack for selector in selectors)


def ensure_success(data: dict[str, Any], url: str) -> dict[str, Any]:
    if data.get("success") is True and str(data.get("errorCode")) in {"1000000", "0", ""}:
        return data
    raise RuntimeError(f"TEMU 接口失败 {url}: errorCode={data.get('errorCode')} errorMsg={data.get('errorMsg')}")


def _same_value(left: Any, right: Any) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def _matches_temu_account_spec(shop: dict[str, Any], account_spec: dict[str, Any]) -> bool:
    for key in ("store_username", "platform_id"):
        if account_spec.get(key) not in (None, "") and not _same_value(shop.get(key), account_spec.get(key)):
            return False
    site_id = account_spec.get("siteId", account_spec.get("site_id"))
    if site_id not in (None, "") and not _same_value(shop.get("siteId"), site_id):
        return False
    name_hint = str(account_spec.get("name") or account_spec.get("browserName") or "").strip()
    if name_hint and name_hint not in str(shop.get("browserName") or ""):
        return False
    return True


def resolve_temu_shop_info(helper: Any, account_name: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(account_name, dict):
        return helper.get_shop_info(str(account_name))

    res = helper.send_http({"action": "getBrowserList", "requestId": str(uuid.uuid4())})
    if not res:
        return None, "getBrowserList no response"
    error = (
        helper._browser_list_error(res)
        if hasattr(helper, "_browser_list_error")
        else (
            ""
            if str(res.get("statusCode")) == "0"
            else f"getBrowserList failed: statusCode={res.get('statusCode')}, statusMessage={res.get('statusMessage')}"
        )
    )
    if error:
        return None, error

    matches = [
        shop for shop in res.get("browserList", [])
        if isinstance(shop, dict) and _matches_temu_account_spec(shop, account_name)
    ]
    if not matches:
        return None, f"TEMU 账号未匹配到稳定定位信息: {temu_account_label(account_name)}"
    if len(matches) > 1:
        names = [str(shop.get("browserName") or "") for shop in matches[:5]]
        return None, f"TEMU 稳定定位信息匹配到多个账号: {names}"

    shop = matches[0]
    browser_id = shop.get("browserId") or shop.get("id")
    return {
        "browserId": str(browser_id) if browser_id else None,
        "browserOauth": shop.get("browserOauth"),
        "name": shop.get("browserName", ""),
        "raw": shop,
    }, ""


def start_temu_browser(
    account_name: Any,
    auth_path: Path,
    timeout_seconds: int,
    auth_slot_held: bool = False,
) -> TemuBrowserContext:
    if not auth_slot_held:
        with ziniu_auth_slot():
            return start_temu_browser(account_name, auth_path, timeout_seconds, auth_slot_held=True)

    return _start_temu_browser_unlocked(account_name, auth_path, timeout_seconds)


def is_temu_tab_connection_error(exc: Exception) -> bool:
    try:
        from DrissionPage.errors import PageDisconnectedError, TargetNotFoundError

        if isinstance(exc, (PageDisconnectedError, TargetNotFoundError)):
            return True
    except ImportError:
        pass
    message = str(exc or "").lower()
    return (
        is_page_disconnected_error(exc)
        or "set changed size during iteration" in message
        or "no such target id" in message
    )


def get_initial_temu_tab(browser: Any, debug_port: int, max_attempts: int = 3) -> Any:
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return browser.latest_tab
        except Exception as exc:
            if not is_temu_tab_connection_error(exc) or attempt >= attempts:
                raise
            print(
                f"[TEMU] 初始标签页正在切换，等待重新接管 "
                f"{attempt}/{attempts} | debug_port={debug_port}",
                flush=True,
            )
            time.sleep(1)


def recover_temu_tab(
    browser: Any,
    page: Any,
    disconnect_error: Exception,
    expected_hosts: tuple[str, ...] = (),
) -> Any:
    reconnect_error: Exception = disconnect_error
    reconnect = getattr(page, "reconnect", None)
    if callable(reconnect):
        try:
            reconnect(wait=1)
            getattr(page, "url")
            return page
        except Exception as exc:
            if not is_temu_tab_connection_error(exc):
                raise
            reconnect_error = exc

    url_hints = tuple(dict.fromkeys((*expected_hosts, "kuajingmaihuo.com", "temu.com")))
    for url_hint in url_hints:
        try:
            tabs = browser.get_tabs(url=url_hint)
        except Exception as exc:
            if not is_temu_tab_connection_error(exc):
                raise
            reconnect_error = exc
            continue
        if tabs:
            return tabs[0]

    try:
        latest_tab = browser.latest_tab
        if latest_tab is not page:
            return latest_tab
    except Exception as exc:
        if not is_temu_tab_connection_error(exc):
            raise
        reconnect_error = exc

    raise reconnect_error


def attach_temu_browser(
    chromium_factory: Any,
    options_factory: Any,
    debug_port: int,
    max_attempts: int = 3,
) -> tuple[Any, Any]:
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            options = options_factory().set_local_port(debug_port).existing_only()
            browser = chromium_factory(options)
            return browser, get_initial_temu_tab(browser, debug_port)
        except Exception as exc:
            if not is_temu_tab_connection_error(exc) or attempt >= attempts:
                raise
            print(
                f"[TEMU] 浏览器控制端正在切换，等待重新接管 "
                f"{attempt}/{attempts} | debug_port={debug_port}",
                flush=True,
            )
            time.sleep(1)


def run_temu_page_operation(
    ctx: Any,
    operation: Any,
    expected_hosts: tuple[str, ...] = (),
) -> Any:
    while True:
        try:
            return operation(ctx.page)
        except Exception as exc:
            if not is_temu_tab_connection_error(exc):
                raise
            reconnect_error = exc
            while True:
                reconnect_attempts = int(getattr(ctx, "reconnect_attempts", 0) or 0) + 1
                setattr(ctx, "reconnect_attempts", reconnect_attempts)
                if reconnect_attempts > 3:
                    raise reconnect_error
                print(
                    f"[TEMU] 业务页面连接断开，尝试恢复 "
                    f"{reconnect_attempts}/3 | debug_port={getattr(ctx, 'debug_port', 0)}",
                    flush=True,
                )
                try:
                    ctx.page = recover_temu_tab(
                        ctx.browser,
                        ctx.page,
                        reconnect_error,
                        expected_hosts=expected_hosts,
                    )
                    break
                except Exception as recover_exc:
                    if not is_temu_tab_connection_error(recover_exc):
                        raise
                    reconnect_error = recover_exc


def is_temu_page_context(value: Any) -> bool:
    return hasattr(value, "page") and hasattr(value, "browser")


def _start_temu_browser_unlocked(account_name: Any, auth_path: Path, timeout_seconds: int) -> TemuBrowserContext:
    if _TEMU_START_BLOCK_REASON:
        raise RuntimeError(f"TEMU 浏览器清理未完成，已阻断后续启动: {_TEMU_START_BLOCK_REASON}")

    helper = load_ziniu_helper(auth_path)
    ok, err = helper.ensure_client_online()
    if not ok:
        raise RuntimeError(f"紫鸟客户端不可用: {err}")
    info, info_err = resolve_temu_shop_info(helper, account_name)
    if not info:
        raise RuntimeError(f"紫鸟账号未找到: {info_err}")

    payload = helper.build_start_browser_payload(info)
    response = None
    for attempt in range(1, 4):
        response = helper.send_http(payload)
        if response and str(response.get("statusCode")) == "0":
            break
        helper.ensure_client_online()
        time.sleep(2 * attempt)
    if not response or str(response.get("statusCode")) != "0":
        raise RuntimeError(f"startBrowser failed: {response}")

    browser_oauth = str(response.get("browserOauth") or info.get("browserOauth") or "")
    debug_port = int(response.get("debuggingPort") or 0)
    page = None
    try:
        from DrissionPage import Chromium, ChromiumOptions

        browser, page = attach_temu_browser(Chromium, ChromiumOptions, debug_port)
        end_at = time.time() + max(45, timeout_seconds)
        reconnect_attempts = 0
        while time.time() < end_at:
            try:
                current_url = str(getattr(page, "url", "") or "")
                lower_url = current_url.lower()
                if not current_url or "about:blank" in lower_url or lower_url.startswith("data:,"):
                    page.get(SELLER_BILL_URL)
                    time.sleep(2)
                    continue
                if "seller.kuajingmaihuo.com" in lower_url and "login" not in lower_url:
                    if temu_seller_session_ready(page):
                        time.sleep(3)
                        return TemuBrowserContext(
                            helper=helper,
                            browser=browser,
                            page=page,
                            browser_oauth=browser_oauth,
                            debug_port=debug_port,
                        )
                    try:
                        page.get(SELLER_BILL_URL)
                    except Exception as exc:
                        if is_temu_tab_connection_error(exc):
                            raise
                elif "temu.com" not in lower_url:
                    page.get(SELLER_BILL_URL)
                try:
                    page = helper._handle_click_for_platform(
                        page,
                        "temu_business",
                        lower_url,
                        helper._log,
                        browser,
                    )
                except Exception as exc:
                    if is_temu_tab_connection_error(exc):
                        raise
                time.sleep(2)
            except Exception as exc:
                if is_temu_tab_connection_error(exc) and reconnect_attempts < 3:
                    reconnect_attempts += 1
                    print(
                        f"[TEMU] 页面连接断开，尝试原位重连 "
                        f"{reconnect_attempts}/3 | debug_port={debug_port}",
                        flush=True,
                    )
                    page = recover_temu_tab(browser, page, exc)
                    continue
                raise
        raise RuntimeError(f"TEMU 登录超时: {getattr(page, 'url', '')}")
    except Exception:
        stopped = stop_temu_browser_session(helper, browser_oauth, debug_port=debug_port)
        try:
            if page:
                page.quit()
        except Exception:
            pass
        if not stopped:
            raise RuntimeError("TEMU 浏览器启动失败且未确认停止，已阻断后续启动。")
        raise


def wait_for_debug_port_closed(
    debug_port: int,
    timeout_seconds: float = 10,
    poll_interval: float = 0.5,
) -> bool:
    if debug_port <= 0:
        return False
    end_at = time.monotonic() + max(0, timeout_seconds)
    while True:
        try:
            with socket.create_connection(("127.0.0.1", debug_port), timeout=0.5):
                pass
        except OSError:
            return True
        if time.monotonic() >= end_at:
            return False
        time.sleep(max(0, poll_interval))


def stop_temu_browser_session(
    helper: Any,
    browser_oauth: str,
    max_attempts: int = 2,
    debug_port: int = 0,
) -> bool:
    global _TEMU_START_BLOCK_REASON
    if not browser_oauth:
        if debug_port > 0:
            if wait_for_debug_port_closed(debug_port):
                return True
            _TEMU_START_BLOCK_REASON = (
                f"browserOauth missing and debug port {debug_port} still open"
            )
            print(f"[TEMU] 缺少 browserOauth 且浏览器仍在运行 | debug_port={debug_port}", flush=True)
            return False
        return True
    last_error = ""
    attempts = max(1, max_attempts)
    stop_accepted = False
    for attempt in range(1, attempts + 1):
        try:
            response = helper.send_http(
                {
                    "action": "stopBrowser",
                    "requestId": str(uuid.uuid4()),
                    "browserOauth": browser_oauth,
                }
            )
            if response and str(response.get("statusCode")) == "0":
                stop_accepted = True
                break
            last_error = str(response)
        except Exception as exc:
            last_error = str(exc)
        if attempt < attempts:
            time.sleep(1)

    if debug_port > 0:
        if wait_for_debug_port_closed(debug_port):
            return True
        _TEMU_START_BLOCK_REASON = f"debug port {debug_port} still open after stopBrowser"
        print(f"[TEMU] 浏览器未确认停止 | debug_port={debug_port}", flush=True)
        return False

    if stop_accepted:
        time.sleep(3)
        return True

    _TEMU_START_BLOCK_REASON = f"stopBrowser failed: {last_error}"
    print(f"[TEMU] stopBrowser 失败 | {last_error}", flush=True)
    return False


def close_temu_browser(ctx: TemuBrowserContext | None) -> None:
    if not ctx:
        return
    stopped = stop_temu_browser_session(
        ctx.helper,
        ctx.browser_oauth,
        debug_port=ctx.debug_port,
    )
    try:
        ctx.page.quit()
    except Exception:
        pass
    if not stopped:
        raise RuntimeError("TEMU 浏览器未确认停止，已阻断后续启动。")


def run_page_or_context(
    page_or_ctx: Any,
    operation: Any,
    expected_hosts: tuple[str, ...] = (),
) -> Any:
    if is_temu_page_context(page_or_ctx):
        return run_temu_page_operation(page_or_ctx, operation, expected_hosts)
    return operation(page_or_ctx)


def ensure_seller_page(ctx: TemuBrowserContext) -> None:
    current_url = str(
        run_temu_page_operation(
            ctx,
            lambda page: getattr(page, "url", ""),
            ("seller.kuajingmaihuo.com",),
        )
        or ""
    ).lower()
    if "seller.kuajingmaihuo.com" in current_url and "login" not in current_url:
        return
    run_temu_page_operation(
        ctx,
        lambda page: page.get(SELLER_BILL_URL),
        ("seller.kuajingmaihuo.com",),
    )
    time.sleep(2)


def set_seller_mall_context(page_or_ctx: Any, mall_id: int | str) -> None:
    value = str(mall_id)
    script = f"""
        localStorage.setItem("agentseller-mall-info-id", "{value}");
        document.cookie = "mallid={value}; path=/; domain=.kuajingmaihuo.com; SameSite=Lax";
        document.cookie = "mallid={value}; path=/; SameSite=Lax";
        return document.cookie;
        """
    cookies = run_page_or_context(
        page_or_ctx,
        lambda page: page.run_js(script),
        ("seller.kuajingmaihuo.com",),
    )
    if f"mallid={value}" not in str(cookies or ""):
        raise RuntimeError(f"TEMU 切换店铺 Cookie 失败: mallId={value}")


def prime_seller_mall_context(ctx: TemuBrowserContext, mall_id: int | str) -> None:
    ensure_seller_page(ctx)
    set_seller_mall_context(ctx, mall_id)
    run_temu_page_operation(
        ctx,
        lambda page: page.get(SELLER_BILL_URL),
        ("seller.kuajingmaihuo.com",),
    )
    time.sleep(2)


def js_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def browser_post_json(page_or_ctx: Any, url: str, payload: dict[str, Any], mall_id: int | str | None = None) -> dict[str, Any]:
    headers = {"content-type": "application/json"}
    if mall_id not in (None, ""):
        headers["mallid"] = str(mall_id)
    script = f"""
        return (async () => {{
            const r = await fetch({js_json(url)}, {{
                method: 'POST',
                credentials: 'include',
                headers: {js_json(headers)},
                body: JSON.stringify({js_json(payload)})
            }});
            const text = await r.text();
            let data = null;
            try {{ data = text ? JSON.parse(text) : {{}}; }} catch (e) {{ data = {{rawText: text}}; }}
            return {{ok: r.ok, status: r.status, url: r.url, data}};
        }})();
    """
    last_error = ""
    result = None
    expected_host = urlparse(url).netloc
    for attempt in range(1, 4):
        try:
            result = run_page_or_context(
                page_or_ctx,
                lambda page: page.run_js(script),
                (expected_host,) if expected_host else (),
            )
            break
        except Exception as exc:
            last_error = str(exc)
            if is_temu_page_context(page_or_ctx) and is_temu_tab_connection_error(exc):
                raise
            if attempt >= 3:
                raise
            time.sleep(2)
    if not isinstance(result, dict):
        raise RuntimeError(f"浏览器 fetch 返回异常: {result!r}, last_error={last_error}")
    if not result.get("ok"):
        raise RuntimeError(f"浏览器 fetch 失败 {url}: status={result.get('status')} data={result.get('data')}")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"接口响应不是 JSON 对象 {url}: {data!r}")
    return data


def browser_get_user_info(page: Any) -> dict[str, Any]:
    data = browser_post_json(page, USER_INFO_URL, {}, None)
    ensure_success(data, "/api/seller/auth/userInfo")
    return data


def temu_seller_session_ready(page: Any) -> bool:
    try:
        data = browser_post_json(page, USER_INFO_URL, {}, None)
        ensure_success(data, USER_INFO_URL)
        return True
    except Exception as exc:
        if is_temu_tab_connection_error(exc):
            raise
        return False


def malls_from_user_info(user_info: dict[str, Any]) -> list[dict[str, Any]]:
    result = user_info.get("result") or {}
    mall_list = result.get("mallList")
    if isinstance(mall_list, list):
        return [mall for mall in mall_list if isinstance(mall, dict)]
    malls: list[dict[str, Any]] = []
    for company in result.get("companyList") or []:
        if not isinstance(company, dict):
            continue
        for mall in company.get("malInfoList") or []:
            if isinstance(mall, dict):
                malls.append(mall)
    return malls


def agent_context_mall_id(malls: list[dict[str, Any]]) -> int | str:
    return next((mall.get("mallId") for mall in malls if mall.get("mallId") not in (None, "")), "")


def agent_request_mall_id(region: str, default_mall_id: int | str, target_mall_id: int | str) -> int | str:
    return target_mall_id


def decode_agent_export_params(record: dict[str, Any]) -> dict[str, Any]:
    params = str(record.get("agentSellerExportParams") or "")
    if not params:
        return {}
    try:
        padded = params + ("=" * (-len(params) % 4))
        decoded = b64decode(padded).decode("utf-8")
        value = json.loads(decoded)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def history_record_matches(record: dict[str, Any], period: PeriodRange, mall_id: int | str) -> bool:
    if int(record.get("searchExportTimeBegin") or 0) != period.start_ms:
        return False
    if int(record.get("searchExportTimeEnd") or 0) != period.end_ms:
        return False
    if bool(record.get("fundDetailExport")) is not True:
        return False
    if int(record.get("status") or 0) != 2:
        return False

    params = decode_agent_export_params(record)
    if params:
        if str(params.get("mallId") or "") != str(mall_id):
            return False
        if int(params.get("beginTime") or 0) != period.start_ms:
            return False
        if int(params.get("endTime") or 0) != period.end_ms:
            return False
    elif record.get("mallId") not in (None, "") and str(record.get("mallId")) != str(mall_id):
        return False
    return True


def match_history_record(
    records: list[dict[str, Any]],
    period: PeriodRange,
    mall_id: int | str,
    excluded_ids: set[int | str] | None = None,
) -> dict[str, Any] | None:
    excluded = {str(item) for item in excluded_ids or set()}
    for row in records:
        if str(row.get("id") or "") in excluded:
            continue
        if history_record_matches(row, period, mall_id):
            return row
    return None


def history_records(page: Any, mall_id: int | str) -> list[dict[str, Any]]:
    data = ensure_success(
        browser_post_json(page, HISTORY_URL, {"pageSize": 10, "pageNum": 1, "taskType": TASK_TYPE}, mall_id),
        HISTORY_URL,
    )
    records = (data.get("result") or {}).get("merchantMerchantFileExportHistoryList") or []
    return [row for row in records if isinstance(row, dict)]


def wait_history_record(
    page: Any,
    period: PeriodRange,
    mall_id: int | str,
    attempts: int,
    interval: int,
    excluded_ids: set[int | str] | None = None,
    fallback_to_existing: bool = False,
) -> dict[str, Any]:
    last_records: list[dict[str, Any]] = []
    for attempt in range(1, max(1, attempts) + 1):
        last_records = history_records(page, mall_id)
        row = match_history_record(last_records, period, mall_id, excluded_ids=excluded_ids)
        if row:
            return row
        if attempt < attempts:
            time.sleep(interval)
    if fallback_to_existing:
        row = match_history_record(last_records, period, mall_id)
        if row:
            return row
    sample = [
        {
            "id": row.get("id"),
            "status": row.get("status"),
            "begin": row.get("searchExportTimeBegin"),
            "end": row.get("searchExportTimeEnd"),
            "params": decode_agent_export_params(row),
        }
        for row in last_records[:3]
    ]
    raise RuntimeError(
        f"导出历史未找到当前店铺匹配完成记录: mallId={mall_id}, begin={period.start_ms}, end={period.end_ms}, sample={sample}"
    )


def browser_download_file(page_or_ctx: Any, file_url: str, output_path: Path) -> int:
    script = f"""
        return (async () => {{
            const r = await fetch({js_json(file_url)}, {{credentials: 'include'}});
            const buffer = await r.arrayBuffer();
            const bytes = new Uint8Array(buffer);
            let binary = '';
            const chunkSize = 0x8000;
            for (let i = 0; i < bytes.length; i += chunkSize) {{
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
            }}
            return {{
                ok: r.ok,
                status: r.status,
                contentType: r.headers.get('content-type') || '',
                length: bytes.length,
                bodyBase64: btoa(binary)
            }};
        }})();
    """
    result = None
    last_error = ""
    expected_host = urlparse(file_url).netloc
    for attempt in range(1, 4):
        try:
            result = run_page_or_context(
                page_or_ctx,
                lambda page: page.run_js(script),
                (expected_host,) if expected_host else (),
            )
            break
        except Exception as exc:
            last_error = str(exc)
            if is_temu_page_context(page_or_ctx) and is_temu_tab_connection_error(exc):
                raise
            if attempt >= 3:
                raise
            time.sleep(2)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"浏览器下载失败: {result!r}, last_error={last_error}")
    raw = b64decode(str(result.get("bodyBase64") or ""))
    if raw.lstrip().lower().startswith(b"<!doctype html") or raw.lstrip().lower().startswith(b"<html"):
        raise RuntimeError(
            f"下载得到 HTML，不是 Excel: status={result.get('status')} contentType={result.get('contentType')}"
        )
    output_path.write_bytes(raw)
    return len(raw)


def existing_export_size(path: Path) -> int:
    try:
        size = path.stat().st_size
        return size if size >= 100 else 0
    except OSError:
        return 0


def validate_temu_outputs(mall_results: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for mall in mall_results:
        label = str(mall.get("label") or mall.get("mallName") or mall.get("mallId") or "unknown")
        for region in mall.get("regionResults") or []:
            output_path = Path(str(region.get("outputPath") or ""))
            if not output_path or existing_export_size(output_path) <= 0:
                region_name = str(region.get("regionName") or region.get("region") or "unknown")
                missing.append(f"{label}/{region_name}: {output_path}")
    return missing


def build_agent_authorization_url(base: str, target_url: str, unique_id: str, code: str) -> str:
    return f"{base}/main/authentication?{urlencode({'redirectUrl': target_url, 'uniqueId': unique_id, 'asCode': code})}"


def obtain_agent_code(page_or_ctx: Any, base: str, mall_id: int | str) -> str:
    data = ensure_success(
        browser_post_json(page_or_ctx, OBTAIN_CODE_URL, {"redirectUrl": f"{base}/main/authentication"}, mall_id),
        OBTAIN_CODE_URL,
    )
    code = str((data.get("result") or {}).get("code") or "")
    if not code:
        raise RuntimeError(f"TEMU obtainCode 未返回授权码: mallId={mall_id}")
    return code


def open_agent_target(
    ctx: TemuBrowserContext,
    target: dict[str, Any],
    record: dict[str, Any],
    mall_id: int | str,
    unique_id: str = "",
) -> None:
    params = str(record.get("agentSellerExportParams") or "")
    sign = str(record.get("agentSellerExportSign") or "")
    if not params or not sign:
        raise RuntimeError(f"导出历史缺少 agentseller 参数: id={record.get('id')}")

    base = str(target["base"])
    expected_host = urlparse(base).netloc
    target_url = f"{base}/labor/bill-download-with-detail?params={quote(params, safe='')}&sign={quote(sign, safe='')}"
    link_url = f"{SELLER_BASE}/link-agent-seller?region={target['region']}&targetUrl={quote(target_url, safe='')}"
    ensure_seller_page(ctx)
    set_seller_mall_context(ctx, mall_id)
    candidates = [link_url, target_url]
    if unique_id:
        code = obtain_agent_code(ctx, base, mall_id)
        candidates.insert(0, build_agent_authorization_url(base, target_url, unique_id, code))
    for candidate_url in candidates:
        candidate_host = urlparse(candidate_url).netloc
        run_temu_page_operation(
            ctx,
            lambda page, url=candidate_url: page.get(url),
            tuple(filter(None, (candidate_host, expected_host))),
        )
        end_at = time.time() + 35
        while time.time() < end_at:
            current_url = str(
                run_temu_page_operation(
                    ctx,
                    lambda page: getattr(page, "url", ""),
                    (expected_host,),
                )
                or ""
            )
            lower_url = current_url.lower()
            current_host = urlparse(current_url).netloc
            if current_host == expected_host and "authentication" not in lower_url and "login" not in lower_url:
                time.sleep(2)
                return
            if "authentication" in lower_url or "login" in lower_url or "link-agent-seller" in lower_url:
                handled_page = run_temu_page_operation(
                    ctx,
                    lambda page: ctx.helper._handle_click_for_platform(
                        page,
                        "temu_business",
                        lower_url,
                        ctx.helper._log,
                        ctx.browser,
                    ),
                    (expected_host,),
                )
                if handled_page is not None:
                    ctx.page = handled_page
            time.sleep(2)
        current_url = str(
            run_temu_page_operation(
                ctx,
                lambda page: getattr(page, "url", ""),
                (expected_host,),
            )
            or ""
        )
    raise RuntimeError(f"agentseller 授权跳转超时: target={target['name']} mallId={mall_id} url={current_url}")


def download_seller_file(
    page_or_ctx: Any,
    record: dict[str, Any],
    mall_id: int | str,
    output_path: Path,
    attempts: int = 18,
    interval: int = 5,
) -> tuple[int, dict[str, Any]]:
    download_payload = {"id": record.get("id"), "taskType": TASK_TYPE}
    download_data: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        download_data = browser_post_json(page_or_ctx, DOWNLOAD_URL, download_payload, mall_id)
        if download_data.get("success") is True:
            break
        if str(download_data.get("errorCode")) != "2000000" or attempt >= max(1, attempts):
            ensure_success(download_data, DOWNLOAD_URL)
        time.sleep(max(0, interval))
    download_data = ensure_success(download_data, DOWNLOAD_URL)
    file_url = ((download_data.get("result") or {}).get("fileUrl") or "").strip()
    if not file_url:
        raise RuntimeError(f"下载接口未返回 fileUrl: {download_data}")
    return browser_download_file(page_or_ctx, file_url, output_path), {
        "payload": download_payload,
        "download_attempts": attempt,
        "response": download_data,
    }


def download_agent_file(
    ctx: TemuBrowserContext,
    target: dict[str, Any],
    record: dict[str, Any],
    mall_id: int | str,
    agent_mall_id: int | str,
    unique_id: str,
    output_path: Path,
    attempts: int = 18,
    interval: int = 5,
) -> tuple[int, dict[str, Any]]:
    open_agent_target(ctx, target, record, mall_id, unique_id)
    base = str(target["base"])
    request_mall_id = agent_request_mall_id(str(target["key"]), agent_mall_id, mall_id)
    user_info_data = ensure_success(
        browser_post_json(ctx, f"{base}/api/seller/auth/userInfo", {}, request_mall_id),
        f"{base}/api/seller/auth/userInfo",
    )
    params = str(record.get("agentSellerExportParams") or "")
    sign = str(record.get("agentSellerExportSign") or "")
    export_payload = {"taskType": AGENT_TASK_TYPE, "params": params, "sign": sign}
    export_data = ensure_success(
        browser_post_json(ctx, f"{base}/api/merchant/file/export", export_payload, request_mall_id),
        f"{base}/api/merchant/file/export",
    )
    file_id = export_data.get("result")
    if file_id in (None, ""):
        raise RuntimeError(f"agentseller 导出未返回文件 id: {export_data}")
    download_payload = {"id": file_id, "taskType": AGENT_TASK_TYPE}
    download_url = f"{base}/api/merchant/file/export/download"
    download_data: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        download_data = browser_post_json(ctx, download_url, download_payload, request_mall_id)
        if download_data.get("success") is True:
            break
        if str(download_data.get("errorCode")) != "2000000" or attempt >= max(1, attempts):
            ensure_success(download_data, download_url)
        time.sleep(max(0, interval))
    download_data = ensure_success(download_data, download_url)
    file_url = ((download_data.get("result") or {}).get("fileUrl") or "").strip()
    if not file_url:
        raise RuntimeError(f"agentseller 下载接口未返回 fileUrl: {download_data}")
    return browser_download_file(ctx, file_url, output_path), {
        "user_info_error_code": user_info_data.get("errorCode"),
        "export_payload": export_payload,
        "export_response": export_data,
        "download_payload": download_payload,
        "download_attempts": attempt,
        "download_response": download_data,
    }


def export_temu_fund_details(
    task: dict[str, Any],
    account_name: Any,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    with ziniu_auth_slot():
        return _export_temu_fund_details_unlocked(
            task,
            account_name,
            period,
            auth_path,
            output_root,
            request_timeout,
            login_timeout,
        )


def _export_temu_fund_details_unlocked(
    task: dict[str, Any],
    account_name: Any,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    platform = "temu"
    account_label = temu_account_label(account_name)
    ctx: TemuBrowserContext | None = None
    debug: dict[str, Any] = {"period": period.to_dict()}
    outputs: list[str] = []
    mall_results: list[dict[str, Any]] = []
    capture_path = ""
    try:
        ctx = start_temu_browser(account_name, auth_path, login_timeout, auth_slot_held=True)
        debug["diagnostic_recorder_installed"] = install_browser_request_recorder(ctx.page)
        user_info = browser_get_user_info(ctx)
        malls = malls_from_user_info(user_info)
        if not malls:
            raise RuntimeError("TEMU 未获取到店铺列表。")
        debug["mall_count"] = len(malls)
        debug["mall_sample"] = malls[:5]
        agent_mall_id = agent_context_mall_id(malls)
        shop_selectors = [str(item) for item in task.get("shop_selectors") or [] if str(item or "").strip()]
        if shop_selectors:
            malls = [mall for index, mall in enumerate(malls) if shop_matches(account_label, mall, index, shop_selectors)]
            debug["shop_selectors"] = shop_selectors
            debug["matched_mall_count"] = len(malls)
            if not malls:
                raise RuntimeError(f"TEMU 未匹配到店铺: {shop_selectors}")
        first_mall_id = next((mall.get("mallId") for mall in malls if mall.get("mallId")), None)
        if first_mall_id:
            prime_seller_mall_context(ctx, first_mall_id)

        attempts = int(task.get("download_attempts") or 18)
        interval = int(task.get("download_interval_seconds") or 5)
        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)

        for index, mall in enumerate(malls):
            ensure_seller_page(ctx)
            mall_id = mall.get("mallId")
            if not mall_id:
                continue
            label = mall_label(account_label, mall, index)
            unique_id = str(mall.get("uniqueId") or "")
            query_payload = {
                "beginTime": period.start_ms,
                "endTime": period.end_ms,
                "pageSize": int(task.get("page_size") or 10),
                "pageNum": 1,
            }
            query_data = ensure_success(browser_post_json(ctx, PAGE_SEARCH_URL, query_payload, mall_id), PAGE_SEARCH_URL)
            total = int((query_data.get("result") or {}).get("total") or 0)
            export_payload = {
                "fundDetailExport": True,
                "taskType": TASK_TYPE,
                "beginTime": period.start_ms,
                "endTime": period.end_ms,
            }
            previous_history_ids = {
                row.get("id")
                for row in history_records(ctx, mall_id)
                if row.get("id") not in (None, "")
            }
            export_data = browser_post_json(ctx, EXPORT_URL, export_payload, mall_id)
            if not (
                export_data.get("success") is True
                or str(export_data.get("errorCode")) == "2000000"
            ):
                ensure_success(export_data, EXPORT_URL)

            record = wait_history_record(
                ctx,
                period,
                mall_id,
                attempts,
                interval,
                excluded_ids=previous_history_ids,
                fallback_to_existing=str(export_data.get("errorCode") or "") == "2000000",
            )
            region_results: list[dict[str, Any]] = []
            for target in AGENT_TARGETS:
                region_code = str(target["name"])
                file_stem = download_stem(account_code(label), period, module_code(task, "fund"), label.split("_", 1)[-1], region_code)
                output_path = download_dir / f"{file_stem}.xlsx"
                existing_bytes = existing_export_size(output_path)
                if existing_bytes:
                    download_bytes = existing_bytes
                    download_debug = {"reusedExisting": True}
                elif target["key"] == "seller":
                    ensure_seller_page(ctx)
                    download_bytes, download_debug = download_seller_file(
                        ctx,
                        record,
                        mall_id,
                        output_path,
                        attempts,
                        interval,
                    )
                else:
                    download_bytes, download_debug = download_agent_file(
                        ctx,
                        target,
                        record,
                        mall_id,
                        agent_mall_id,
                        unique_id,
                        output_path,
                        attempts,
                        interval,
                    )
                outputs.append(str(output_path))
                region_results.append(
                    {
                        "region": target["key"],
                        "regionName": target["name"],
                        "outputPath": str(output_path),
                        "downloadBytes": download_bytes,
                        "debug": download_debug,
                    }
                )
            mall_results.append(
                {
                    "mallId": mall_id,
                    "mallName": mall.get("mallName"),
                    "label": label,
                    "queryTotal": total,
                    "historyId": record.get("id"),
                    "regionResults": region_results,
                }
            )

        missing_outputs = validate_temu_outputs(mall_results)
        if missing_outputs:
            sample = "; ".join(missing_outputs[:8])
            suffix = f" ... 共 {len(missing_outputs)} 个缺失" if len(missing_outputs) > 8 else ""
            raise RuntimeError(f"TEMU 导出结果不完整，缺少文件: {sample}{suffix}")

        file_stem = download_stem(account_label, period, module_code(task, "fund"))
        capture_path = write_capture_file(
            task,
            output_root,
            platform,
            period,
            file_stem,
            {
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task_id": task.get("id"),
                "platform": platform,
                "account_name": account_label,
                **debug,
                "mall_results": mall_results,
                **({"browser_diagnostics": collect_browser_diagnostics(ctx.page)} if diagnostic_enabled(task) else {}),
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "temu_fund_details"),
            platform=platform,
            account_name=account_label,
            success=True,
            message=f"TEMU 资金明细完成，店铺数 {len(mall_results)}，文件数 {len(outputs)}",
            output_path="; ".join(outputs[:3]) + (" ..." if len(outputs) > 3 else ""),
            capture_path=capture_path,
            data={"period": period.to_dict(), "mall_count": len(mall_results), "outputs": outputs},
        )
    except Exception as exc:
        try:
            period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
            file_stem = download_stem(account_label, period, module_code(task, "fund"))
            capture_path = write_capture_file(
                task,
                output_root,
                platform,
                period,
                file_stem,
                {
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "task_id": task.get("id"),
                    "platform": platform,
                    "account_name": account_label,
                    "success": False,
                    "error": str(exc),
                    **debug,
                    "mall_results": mall_results,
                    "browser_diagnostics": collect_browser_diagnostics(ctx.page) if ctx else {},
                },
                failed=True,
            )
        except Exception:
            pass
        return TaskResult(
            task_id=str(task.get("id") or "temu_fund_details"),
            platform=platform,
            account_name=account_label,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )
    finally:
        close_temu_browser(ctx)
