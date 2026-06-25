from __future__ import annotations

import json
import time
import uuid
from base64 import b64decode
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from finance_crawler.auth import load_ziniu_helper, ziniu_auth_slot
from finance_crawler.debug_files import write_capture_file
from finance_crawler.diagnostics import collect_browser_diagnostics, diagnostic_enabled, install_browser_request_recorder
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import export_folder_name
from finance_crawler.platforms.tiktok_common import log_tiktok_poll, tiktok_download_poll_options


SELLER_WALLET_URL = "https://seller.tiktokshopglobalselling.com/seller-wallet/full-service?shop_region=GB"
SELLER_OPEN_WALLET_URL = (
    "https://api16-normal-sg.tiktokshopglobalselling.com/api/finance/wallet/v1/get_open_wallet_url_v2"
)
PIPO_BASE = "https://cashier-my4a.pipopay.com"
EXCHANGE_SESSION_URL = f"{PIPO_BASE}/wallet/v1/user/exchange_session"
QUERY_LIST_URL = f"{PIPO_BASE}/wallet_bill/v1/query_list"
CREATE_FILE_TASK_URL = f"{PIPO_BASE}/wallet_bill/v1/create_file_task"
QUERY_FILE_TASK_URL = f"{PIPO_BASE}/wallet_bill/v1/query_file_task"


@dataclass
class TiktokBrowserContext:
    helper: Any
    browser: Any
    page: Any
    browser_oauth: str


def js_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def stop_tiktok_browser_session(helper: Any, browser_oauth: str, max_attempts: int = 2) -> bool:
    if not browser_oauth:
        return True
    last_error = ""
    for _ in range(max(1, max_attempts)):
        try:
            response = helper.send_http(
                {
                    "action": "stopBrowser",
                    "requestId": str(uuid.uuid4()),
                    "browserOauth": browser_oauth,
                }
            )
            if response and str(response.get("statusCode")) == "0":
                return True
            last_error = str(response)
        except Exception as exc:
            last_error = str(exc)
    print(f"[TK] stopBrowser 失败 | {last_error}")
    return False


def close_tiktok_browser(ctx: TiktokBrowserContext | None) -> None:
    if not ctx:
        return
    stop_tiktok_browser_session(ctx.helper, ctx.browser_oauth)
    try:
        ctx.page.quit()
    except Exception:
        pass


def is_tiktok_business_page_url(url: str) -> bool:
    lower_url = str(url or "").lower()
    return "tiktokshopglobalselling.com" in lower_url and "login" not in lower_url


def start_tiktok_browser(
    account_name: str,
    auth_path: Path,
    timeout_seconds: int,
    target_url: str = SELLER_WALLET_URL,
) -> TiktokBrowserContext:
    with ziniu_auth_slot():
        helper = load_ziniu_helper(auth_path)
        ok, err = helper.ensure_client_online()
        if not ok:
            raise RuntimeError(f"紫鸟客户端不可用: {err}")
        info, info_err = helper.get_shop_info(account_name)
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
        browser = page = None
        try:
            from DrissionPage import Chromium, ChromiumOptions

            browser = Chromium(ChromiumOptions().set_local_port(debug_port))
            try:
                page = browser.new_tab(target_url)
            except Exception:
                page = browser.latest_tab
                page.get(target_url)

            end_at = time.time() + max(30, timeout_seconds)
            while time.time() < end_at:
                current_url = str(getattr(page, "url", "") or "")
                lower_url = current_url.lower()
                if is_tiktok_business_page_url(lower_url):
                    time.sleep(4)
                    return TiktokBrowserContext(
                        helper=helper,
                        browser=browser,
                        page=page,
                        browser_oauth=browser_oauth,
                    )
                try:
                    page = helper._handle_click_for_platform(
                        page,
                        "tiktok",
                        lower_url,
                        helper._log,
                        browser,
                        account=account_name,
                    )
                except Exception:
                    pass
                time.sleep(2)
            raise RuntimeError(f"TK 登录超时: {getattr(page, 'url', '')}")
        except Exception:
            stop_tiktok_browser_session(helper, browser_oauth)
            try:
                if page:
                    page.quit()
            except Exception:
                pass
            raise


def find_cashier_url(page: Any, timeout_seconds: int) -> str:
    script = """
        return (() => {
            const urls = [];
            const push = (v) => {
                if (v && typeof v === 'string' && v.includes('cashier-my4a.pipopay.com')) urls.push(v);
            };
            document.querySelectorAll('iframe, a, form').forEach((el) => {
                push(el.src);
                push(el.href);
                push(el.action);
            });
            try {
                performance.getEntriesByType('resource').forEach((entry) => push(entry.name));
            } catch (e) {}
            try {
                Object.keys(localStorage || {}).forEach((key) => push(localStorage.getItem(key)));
            } catch (e) {}
            return Array.from(new Set(urls)).filter((url) => url.includes('/pipo/fe/business_wallet/wallet/views/main'));
        })();
    """
    end_at = time.time() + max(10, timeout_seconds)
    last_candidates: list[str] = []
    while time.time() < end_at:
        try:
            candidates = page.run_js(script)
            if isinstance(candidates, list):
                last_candidates = [str(item) for item in candidates if item]
                chosen = choose_cashier_url(last_candidates)
                if chosen:
                    return chosen
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"未找到 TK 钱包 cashier URL: sample={last_candidates[:3]}")


def seller_post_json(page: Any, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    script = f"""
        return (async () => {{
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), {max(5, timeout) * 1000});
            try {{
                const r = await fetch({js_json(url)}, {{
                    method: 'POST',
                    credentials: 'include',
                    headers: {{
                        'accept': 'application/json, text/plain, */*',
                        'content-type': 'application/json'
                    }},
                    body: JSON.stringify({js_json(payload)}),
                    signal: controller.signal
                }});
                const text = await r.text();
                let data = null;
                try {{ data = text ? JSON.parse(text) : {{}}; }} catch (e) {{ data = {{rawText: text}}; }}
                return {{ok: r.ok, status: r.status, url: r.url, data}};
            }} finally {{
                clearTimeout(timer);
            }}
        }})();
    """
    result = page.run_js(script)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"TK seller 接口失败 {url}: {result!r}")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"TK seller 响应不是对象 {url}: {data!r}")
    return data


def extract_open_wallet_url(data: dict[str, Any]) -> str:
    base = data.get("base_resp") or {}
    code = base.get("code", data.get("code"))
    if str(code) not in {"0", ""}:
        raise RuntimeError(f"TK 获取钱包入口失败: {data}")
    url = str(((data.get("data") or {}).get("open_wallet_url")) or "").strip()
    if not url:
        raise RuntimeError(f"TK 钱包入口响应缺少 open_wallet_url: {data}")
    return url


def get_open_wallet_url_from_seller(page: Any, timeout: int) -> str:
    data = seller_post_json(page, SELLER_OPEN_WALLET_URL, {"aid": "554251"}, timeout)
    return extract_open_wallet_url(data)


def resolve_cashier_url(ctx: TiktokBrowserContext, detect_seconds: int, timeout: int) -> str:
    try:
        return get_open_wallet_url_from_seller(ctx.page, timeout)
    except Exception as exc:
        print(f"[TK] 钱包入口接口失败，回退页面扫描: {exc}", flush=True)
        return find_cashier_url(ctx.page, detect_seconds)


def open_seller_wallet_page(ctx: TiktokBrowserContext, timeout: int = 15) -> None:
    url = f"{SELLER_WALLET_URL}&_={int(time.time())}"
    try:
        ctx.page.run_js(f"location.href = {js_json(url)}")
    except Exception as exc:
        print(f"[TK] 钱包页 JS 跳转失败，尝试短超时导航: {exc}", flush=True)
        try:
            ctx.page.get(url, timeout=timeout)
        except Exception as nav_exc:
            print(f"[TK] 钱包页加载未完整结束，继续检查页面状态: {nav_exc}", flush=True)
    end_at = time.time() + max(5, timeout)
    while time.time() < end_at:
        current_url = str(getattr(ctx.page, "url", "") or "")
        if "seller.tiktokshopglobalselling.com" in current_url and "seller-wallet" in current_url:
            return
        try:
            ctx.page.run_js(f"location.href = {js_json(url)}")
        except Exception:
            pass
        time.sleep(1)


def choose_cashier_url(candidates: list[str]) -> str:
    usable = [
        candidate for candidate in candidates
        if "wuid=" in candidate and "merchant_id=" in candidate
    ]
    if not usable:
        return ""
    now = int(time.time())

    def score(candidate: str) -> tuple[int, int, int]:
        params = parse_qs(urlparse(candidate).query)
        session_exp = jwt_exp((params.get("fp_session_id") or [""])[0])
        token_exp = jwt_exp((params.get("fp_token") or [""])[0])
        best_exp = max(session_exp, token_exp)
        is_alive = 1 if best_exp > now + 60 else 0
        has_token = 1 if session_exp and token_exp else 0
        return is_alive, best_exp, has_token

    return sorted(usable, key=score, reverse=True)[0]


def jwt_exp(token: str) -> int:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return 0
    try:
        payload = parts[1] + ("=" * (-len(parts[1]) % 4))
        data = json.loads(b64decode(payload.replace("-", "+").replace("_", "/")).decode("utf-8"))
        claims = data.get("standard_claims") if isinstance(data, dict) else {}
        return int((claims or {}).get("exp") or data.get("exp") or 0)
    except Exception:
        return 0


def parse_cashier_params(cashier_url: str) -> dict[str, str]:
    query = parse_qs(urlparse(cashier_url).query)
    params = {key: values[0] for key, values in query.items() if values}
    required = ["wuid", "merchant_id"]
    missing = [key for key in required if not params.get(key)]
    if missing:
        raise RuntimeError(f"cashier URL 缺少参数 {missing}: {cashier_url}")
    return params


def pipo_auth_headers_from_url(url: str) -> dict[str, str]:
    query = parse_qs(urlparse(str(url or "")).query)
    headers: dict[str, str] = {}
    session_id = (query.get("fp_session_id") or [""])[0]
    fp_token = (query.get("fp_token") or [""])[0]
    if session_id:
        headers["pipo-fp-session-id"] = session_id
    if fp_token:
        headers["pipo-fp-token"] = fp_token
    return headers


def cashier_bootstrap_ready_from_urls(urls: list[str]) -> bool:
    joined = "\n".join(str(url) for url in urls)
    return "/cashier/v1/user/info" in joined and "/wallet/v1/get_wallet_index" in joined


def cashier_bootstrap_urls(page: Any) -> list[str]:
    script = """
        return (() => {
            const urls = [];
            const push = (v) => {
                if (v && typeof v === 'string' && v.includes('pipopay.com')) urls.push(v);
            };
            push(location.href);
            try {
                performance.getEntriesByType('resource').forEach((entry) => push(entry.name));
            } catch (e) {}
            return Array.from(new Set(urls));
        })();
    """
    try:
        urls = page.run_js(script)
    except Exception:
        return []
    return [str(url) for url in urls] if isinstance(urls, list) else []


def open_cashier_page(ctx: TiktokBrowserContext, cashier_url: str) -> None:
    ctx.page.get(cashier_url)
    end_at = time.time() + 30
    while time.time() < end_at:
        current_url = str(getattr(ctx.page, "url", "") or "")
        if "cashier-my4a.pipopay.com" in current_url:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"TK 钱包页打开超时: {getattr(ctx.page, 'url', '')}")

    bootstrap_deadline = time.time() + 45
    last_urls: list[str] = []
    while time.time() < bootstrap_deadline:
        last_urls = cashier_bootstrap_urls(ctx.page)
        if cashier_bootstrap_ready_from_urls(last_urls):
            time.sleep(1)
            return
        time.sleep(1)
    print(f"[TK] Pipo 钱包启动标记未完全出现，继续尝试接口: sample={last_urls[-3:]}", flush=True)


def refresh_cashier_page(ctx: TiktokBrowserContext, detect_seconds: int) -> tuple[str, dict[str, str]]:
    open_seller_wallet_page(ctx)
    time.sleep(3)
    cashier_url = resolve_cashier_url(ctx, detect_seconds, 60)
    params = parse_cashier_params(cashier_url)
    open_cashier_page(ctx, cashier_url)
    return cashier_url, params


def pipo_post_form(page: Any, url: str, merchant_id: str, biz_content: dict[str, Any], timeout: int) -> dict[str, Any]:
    script = f"""
        return (async () => {{
            const body = new URLSearchParams();
            body.set('merchant_id', {js_json(merchant_id)});
            body.set('request_time', new Date().toISOString());
            body.set('biz_content', JSON.stringify({js_json(biz_content)}));
            const currentUrl = String(location.href || '');
            const params = new URLSearchParams(currentUrl.split('?')[1] || '');
            const headers = {{
                'accept': 'application/json, text/plain, */*',
                'content-type': 'application/x-www-form-urlencoded',
                'slardar-id': 'pipo'
            }};
            const sessionId = params.get('fp_session_id');
            const fpToken = params.get('fp_token');
            if (sessionId) headers['pipo-fp-session-id'] = sessionId;
            if (fpToken) headers['pipo-fp-token'] = fpToken;
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), {max(5, timeout) * 1000});
            try {{
                const r = await fetch({js_json(url)}, {{
                    method: 'POST',
                    credentials: 'include',
                    headers,
                    body,
                    signal: controller.signal
                }});
                const text = await r.text();
                let data = null;
                try {{ data = text ? JSON.parse(text) : {{}}; }} catch (e) {{ data = {{rawText: text}}; }}
                return {{ok: r.ok, status: r.status, url: r.url, data}};
            }} finally {{
                clearTimeout(timer);
            }}
        }})();
    """
    result = page.run_js(script)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"Pipo 接口失败 {url}: {result!r}")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Pipo 响应不是对象 {url}: {data!r}")
    if data.get("response") and isinstance(data.get("response"), str):
        try:
            inner = json.loads(str(data["response"]))
        except Exception:
            inner = {}
        data["_inner_response"] = inner
    return data


def ensure_pipo_success(payload: dict[str, Any], url: str) -> dict[str, Any]:
    inner = payload.get("_inner_response") or payload
    if str(inner.get("result_code") or "").lower() == "success" or str(inner.get("error_code") or "") == "0":
        return inner
    raise RuntimeError(f"Pipo 业务失败 {url}: {payload}")


def is_login_expired(payload: dict[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return "LOGIN_STATUS_EXPIRED" in text or "Login status expired" in text


def is_pipo_parameter_error(payload: dict[str, Any] | str) -> bool:
    text = json.dumps(payload, ensure_ascii=False, default=str) if isinstance(payload, dict) else str(payload)
    return "sy0007" in text.lower() and "parameter error" in text.lower()


def period_utc_seconds(period: PeriodRange) -> tuple[int, int]:
    start_day = period.start.date()
    end_exclusive_day = period.end.date() + timedelta(days=1)
    return utc_day_seconds(start_day), utc_day_seconds(end_exclusive_day)


def utc_day_seconds(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())


def wait_file_task(
    page: Any,
    merchant_id: str,
    task_id: str,
    attempts: int,
    interval_seconds: int,
    timeout: int,
    account_name: str = "",
    target: str = "导出文件",
) -> tuple[str, dict[str, Any]]:
    last_inner: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        payload = pipo_post_form(page, QUERY_FILE_TASK_URL, merchant_id, {"task_id": task_id}, timeout)
        inner = ensure_pipo_success(payload, QUERY_FILE_TASK_URL)
        last_inner = inner
        status = str(inner.get("task_status") or "").upper()
        download_url = str(inner.get("download_url") or "")
        if status in {"TASK_STATUS_SUCESS", "TASK_STATUS_SUCCESS", "SUCCESS"} and download_url:
            if account_name:
                log_tiktok_poll(account_name, target, attempt, attempts, "已生成")
            return download_url, inner
        if account_name:
            log_tiktok_poll(account_name, target, attempt, attempts, f"状态 {status or 'UNKNOWN'}")
        if attempt < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(f"TK 文件任务未完成: task_id={task_id}, last={last_inner}")


def probe_withdrawal_list(
    page: Any,
    merchant_id: str,
    wuid: str,
    start_seconds: int,
    end_seconds: int,
    timeout: int,
) -> dict[str, Any]:
    payload = {
        "wuid": wuid,
        "page_size": 10,
        "in_out_type": None,
        "wallet_type": "SELLER",
        "bill_type": "WITHDRAW",
        "page_num": 1,
        "bill_currency_code": None,
        "biz_reference_id": None,
        "start_time_stamp": start_seconds,
        "end_time_stamp": end_seconds,
    }
    response = pipo_post_form(page, QUERY_LIST_URL, merchant_id, payload, timeout)
    return ensure_pipo_success(response, QUERY_LIST_URL)


def create_withdrawal_file_task(
    page: Any,
    merchant_id: str,
    create_payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    create_response = pipo_post_form(page, CREATE_FILE_TASK_URL, merchant_id, create_payload, timeout)
    return ensure_pipo_success(create_response, CREATE_FILE_TASK_URL)


def browser_download_file(page: Any, file_url: str, output_path: Path, timeout: int) -> int:
    script = f"""
        return (async () => {{
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), {max(10, timeout) * 1000});
            try {{
                const r = await fetch({js_json(file_url)}, {{
                    method: 'GET',
                    credentials: 'include',
                    headers: {{'accept': '*/*'}},
                    signal: controller.signal
                }});
                const buffer = await r.arrayBuffer();
                const bytes = new Uint8Array(buffer);
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {{
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                }}
                return {{
                    ok: r.ok,
                    status: r.status,
                    contentType: r.headers.get('content-type') || '',
                    bodyBase64: btoa(binary)
                }};
            }} finally {{
                clearTimeout(timer);
            }}
        }})();
    """
    result = page.run_js(script)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"TK 文件下载失败: {result!r}")
    raw = b64decode(str(result.get("bodyBase64") or ""))
    if raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise RuntimeError(f"TK 下载得到 HTML: status={result.get('status')} contentType={result.get('contentType')}")
    output_path.write_bytes(raw)
    return len(raw)


def export_tiktok_withdrawals_with_ctx(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    ctx: TiktokBrowserContext,
    output_root: Path,
    request_timeout: int = 60,
) -> TaskResult:
    platform = "tiktok"
    capture_path = ""
    debug: dict[str, Any] = {"period": period.to_dict()}
    try:
        print(f"[TK] {account_name} 打开钱包页", flush=True)
        open_seller_wallet_page(ctx)
        time.sleep(3)
        print(f"[TK] {account_name} 查找 Pipo 钱包链接", flush=True)
        detect_seconds = int(task.get("cashier_detect_seconds") or 45)
        cashier_url = resolve_cashier_url(ctx, detect_seconds, request_timeout)
        params = parse_cashier_params(cashier_url)
        print(f"[TK] {account_name} 进入 Pipo 钱包页", flush=True)
        open_cashier_page(ctx, cashier_url)
        debug["diagnostic_recorder_installed"] = install_browser_request_recorder(ctx.page)

        merchant_id = str(params["merchant_id"])
        wuid = str(params["wuid"])
        start_seconds, end_seconds = period_utc_seconds(period)
        debug.update(
            {
                "cashier_url": cashier_url,
                "merchant_id": merchant_id,
                "wuid": wuid,
                "start_time_stamp": start_seconds,
                "end_time_stamp": end_seconds,
            }
        )

        print(f"[TK] {account_name} 校验提现列表", flush=True)
        try:
            list_response = probe_withdrawal_list(
                ctx.page,
                merchant_id,
                wuid,
                start_seconds,
                end_seconds,
                request_timeout,
            )
            debug["list_response"] = list_response
        except RuntimeError as exc:
            if not is_pipo_parameter_error(str(exc)):
                raise
            debug["list_probe_skipped"] = {"reason": str(exc)}
            print(f"[TK] {account_name} 提现列表校验参数不兼容，跳过校验继续导出", flush=True)

        print(f"[TK] {account_name} 尝试刷新钱包 session", flush=True)
        exchange_payload = pipo_post_form(
            ctx.page,
            EXCHANGE_SESSION_URL,
            merchant_id,
            {"set_cookie": True},
            request_timeout,
        )
        debug["exchange_response"] = exchange_payload
        try:
            ensure_pipo_success(exchange_payload, EXCHANGE_SESSION_URL)
        except RuntimeError as exc:
            if not is_login_expired(exchange_payload):
                raise
            print(f"[TK] {account_name} Pipo session 过期，刷新页面后再试一次", flush=True)
            cashier_url, params = refresh_cashier_page(ctx, detect_seconds)
            merchant_id = str(params["merchant_id"])
            wuid = str(params["wuid"])
            debug.update({"refreshed_cashier_url": cashier_url, "refresh_reason": str(exc)})
            exchange_payload = pipo_post_form(
                ctx.page,
                EXCHANGE_SESSION_URL,
                merchant_id,
                {"set_cookie": True},
                request_timeout,
            )
            debug["exchange_response_after_refresh"] = exchange_payload
            try:
                ensure_pipo_success(exchange_payload, EXCHANGE_SESSION_URL)
            except RuntimeError:
                if not is_login_expired(exchange_payload):
                    raise
                print(f"[TK] {account_name} 刷新 session 仍过期，继续尝试创建导出任务", flush=True)

        print(f"[TK] {account_name} 创建提现明细导出任务", flush=True)
        create_payload = {
            "language": "zh",
            "task_type": "TRANSACTION_HISTORY",
            "wuid": wuid,
            "start_time_stamp": start_seconds,
            "end_time_stamp": end_seconds,
            "bill_type": "WITHDRAW",
            "in_out_type": None,
            "biz_reference_id": "",
        }
        try:
            create_inner = create_withdrawal_file_task(ctx.page, merchant_id, create_payload, request_timeout)
        except RuntimeError as exc:
            if not is_pipo_parameter_error(str(exc)):
                raise
            print(f"[TK] {account_name} 创建导出任务参数不兼容，刷新钱包入口后重试一次", flush=True)
            open_seller_wallet_page(ctx)
            time.sleep(3)
            cashier_url = resolve_cashier_url(ctx, detect_seconds, request_timeout)
            params = parse_cashier_params(cashier_url)
            open_cashier_page(ctx, cashier_url)
            merchant_id = str(params["merchant_id"])
            wuid = str(params["wuid"])
            debug.update({"merchant_id": merchant_id, "wuid": wuid, "retry_cashier_url": cashier_url})
            create_payload["wuid"] = wuid
            create_inner = create_withdrawal_file_task(ctx.page, merchant_id, create_payload, request_timeout)
        task_id = str(create_inner.get("task_id") or "")
        if not task_id:
            raise RuntimeError(f"TK 导出未返回 task_id: {create_response}")

        print(f"[TK] {account_name} 等待导出文件生成 task_id={task_id}", flush=True)
        attempts, interval = tiktok_download_poll_options(task)
        download_url, query_inner = wait_file_task(
            ctx.page,
            merchant_id,
            task_id,
            attempts,
            interval,
            request_timeout,
            account_name,
            "提现明细文件",
        )
        full_download_url = urljoin(PIPO_BASE, download_url)

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        file_stem = download_stem(account_name, period, module_code(task, "TK提现明细"))
        output_path = download_dir / f"{file_stem}.csv"
        print(f"[TK] {account_name} 下载 CSV", flush=True)
        download_bytes = browser_download_file(ctx.page, full_download_url, output_path, request_timeout)

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
                "account_name": account_name,
                **debug,
                "create_payload": create_payload,
                "create_response": create_inner,
                "query_response": query_inner,
                "download_url": full_download_url,
                "output_path": str(output_path),
                "download_bytes": download_bytes,
                **({"browser_diagnostics": collect_browser_diagnostics(ctx.page)} if diagnostic_enabled(task) else {}),
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_withdrawals"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"TK 提现明细导出完成，文件数 1",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "output": str(output_path), "download_bytes": download_bytes},
        )
    except Exception as exc:
        try:
            file_stem = download_stem(account_name, period, module_code(task, "TK提现明细"))
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
                    "account_name": account_name,
                    "success": False,
                    "error": str(exc),
                    **debug,
                    "browser_diagnostics": collect_browser_diagnostics(ctx.page),
                },
                failed=True,
            )
        except Exception:
            pass
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_withdrawals"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )


def export_tiktok_withdrawals(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    ctx: TiktokBrowserContext | None = None
    try:
        ctx = start_tiktok_browser(account_name, auth_path, login_timeout)
        return export_tiktok_withdrawals_with_ctx(
            task=task,
            account_name=account_name,
            period=period,
            ctx=ctx,
            output_root=output_root,
            request_timeout=request_timeout,
        )
    finally:
        close_tiktok_browser(ctx)
