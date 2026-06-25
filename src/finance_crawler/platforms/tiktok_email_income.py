from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

from finance_crawler.debug_files import write_capture_file
from finance_crawler.diagnostics import collect_browser_diagnostics, diagnostic_enabled, install_browser_request_recorder
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import export_folder_name
from finance_crawler.platforms.tiktok_common import log_tiktok_poll, tiktok_download_poll_options
from finance_crawler.platforms.tiktok_withdrawals import (
    TiktokBrowserContext,
    browser_download_file,
    close_tiktok_browser,
    js_json,
    start_tiktok_browser,
)


SELLER_US_BASE = "https://seller.us.tiktokshopglobalselling.com"
BILLS_PAGE_URL = f"{SELLER_US_BASE}/finance/bills?shop_region=US&subTab=bills&tab=statements"
SELLER_COMMON_PATH = "/api/v3/seller/common/get"
STATEMENT_LIST_PATH = "/api/v1/pay/statement/list/detail"
FILE_EXPORT_PATH = "/api/v2/pay/settlement/file/export"
FILE_LIST_PATH = "/api/v2/pay/settlement/file/list"
FILE_DOWNLOAD_PATH = "/api/v1/pay/settlement/file/download"
DEFAULT_TIMEZONE_NAME = "America/Anchorage"


def is_tiktok_email_no_data_error(message: str) -> bool:
    text = str(message or "")
    return "暂无数据可导出" in text or "code=22008000" in text


def is_page_refresh_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return "页面被刷新" in message or "page was refreshed" in message


def browser_json_request(
    page: Any,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: int,
) -> dict[str, Any]:
    body_expr = "undefined" if payload is None else f"JSON.stringify({js_json(payload)})"
    script = f"""
        return (async () => {{
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), {max(5, timeout) * 1000});
            try {{
                const options = {{
                    method: {js_json(method.upper())},
                    credentials: 'include',
                    headers: {{
                        'accept': 'application/json, text/plain, */*',
                        'x-tt-oec-region': 'US'
                    }},
                    signal: controller.signal
                }};
                const body = {body_expr};
                if (body !== undefined) {{
                    options.headers['content-type'] = 'application/json';
                    options.body = body;
                }}
                const r = await fetch({js_json(url)}, options);
                const text = await r.text();
                let data = null;
                try {{ data = text ? JSON.parse(text) : {{}}; }} catch (e) {{ data = {{rawText: text}}; }}
                return {{ok: r.ok, status: r.status, url: r.url, data}};
            }} finally {{
                clearTimeout(timer);
            }}
        }})();
    """
    result = None
    for attempt in range(1, 4):
        try:
            result = page.run_js(script)
            break
        except Exception as exc:
            if attempt >= 3 or not is_page_refresh_error(exc):
                raise
            time.sleep(1)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"TK 邮箱分支接口失败 {url}: {result!r}")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"TK 邮箱分支响应不是对象 {url}: {data!r}")
    return data


def ensure_success(data: dict[str, Any], url: str) -> dict[str, Any]:
    code = data.get("code", (data.get("base_resp") or {}).get("code"))
    message = data.get("message", (data.get("base_resp") or {}).get("message"))
    if str(code) in {"0", ""}:
        return data
    raise RuntimeError(f"TK 邮箱分支业务失败 {url}: code={code} message={message} data={data}")


def api_url(
    path: str,
    seller_id: str = "",
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
    extra: dict[str, Any] | None = None,
) -> str:
    params: dict[str, Any] = {
        "locale": "zh-CN",
        "language": "zh-CN",
        "aid": "6556",
        "app_name": "i18n_ecom_shop",
        "device_platform": "web",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Mozilla",
        "browser_online": "true",
        "timezone_name": timezone_name,
        "_": int(time.time() * 1000),
    }
    if seller_id:
        params["oec_seller_id"] = seller_id
        params["seller_id"] = seller_id
    if extra:
        params.update({key: value for key, value in extra.items() if value is not None})
    return f"{SELLER_US_BASE}{path}?{urlencode(params)}"


def build_income_export_payload(period: PeriodRange) -> dict[str, Any]:
    return {
        "period": {
            "begin_date": str(int(period.start.timestamp())),
            "end_date": str(int(period.end.timestamp())),
        },
        "file_type": 1,
        "statement_version": 0,
    }


def build_statement_list_params(period: PeriodRange) -> dict[str, Any]:
    return {
        "pagination_type": 1,
        "from": 0,
        "size": 10,
        "bill_period_time_lower": str(period.start_ms),
        "bill_period_time_upper": str(period.end_ms + 999),
        "page_type": 5,
        "need_total_amount": "true",
        "statement_version": 0,
    }


def parse_seller_info(data: dict[str, Any], url: str) -> dict[str, Any]:
    ensure_success(data, url)
    seller = ((data.get("data") or {}).get("seller") or {})
    seller_id = str(seller.get("seller_id") or "").strip()
    if not seller_id:
        raise RuntimeError(f"TK 邮箱分支未识别 seller_id: {data}")
    return {"seller_id": seller_id, "seller": seller, "raw": data}


def get_seller_info(page: Any, timezone_name: str, timeout: int) -> dict[str, Any]:
    url = api_url(
        SELLER_COMMON_PATH,
        timezone_name=timezone_name,
        extra={"version": 3, "need_verify_account": "true", "only_get_seller": 1},
    )
    data = browser_json_request(page, "GET", url, None, timeout)
    return parse_seller_info(data, url)


def list_statement_detail(
    page: Any,
    seller_id: str,
    timezone_name: str,
    period: PeriodRange,
    timeout: int,
) -> dict[str, Any]:
    url = api_url(STATEMENT_LIST_PATH, seller_id, timezone_name, build_statement_list_params(period))
    data = browser_json_request(page, "GET", url, None, timeout)
    return ensure_success(data, url)


def create_income_export(
    page: Any,
    seller_id: str,
    timezone_name: str,
    period: PeriodRange,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = build_income_export_payload(period)
    url = api_url(FILE_EXPORT_PATH, seller_id, timezone_name)
    data = browser_json_request(page, "POST", url, payload, timeout)
    return payload, ensure_success(data, url)


def list_income_files(page: Any, seller_id: str, timezone_name: str, timeout: int) -> dict[str, Any]:
    url = api_url(FILE_LIST_PATH, seller_id, timezone_name, {"file_type": 1, "version": 2})
    data = browser_json_request(page, "GET", url, None, timeout)
    return ensure_success(data, url)


def normalize_files(data: dict[str, Any]) -> list[dict[str, Any]]:
    files = ((data.get("data") or {}).get("files")) or []
    return [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []


def choose_ready_income_file(files: list[dict[str, Any]], created_after_ms: int = 0) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for item in files:
        status = int(item.get("status") or 0)
        file_name = str(item.get("file_name") or "").lower()
        file_id = str(item.get("file_id") or "").strip()
        create_time = int(item.get("create_time") or item.get("export_time") or 0)
        if status not in {2, 3} or not file_id or "income" not in file_name:
            continue
        if created_after_ms and create_time and create_time < created_after_ms:
            continue
        candidates.append(item)
    if not candidates:
        return {}
    return sorted(
        candidates,
        key=lambda item: int(item.get("create_time") or item.get("export_time") or 0),
        reverse=True,
    )[0]


def wait_income_file(
    page: Any,
    seller_id: str,
    timezone_name: str,
    created_after_ms: int,
    attempts: int,
    interval_seconds: int,
    timeout: int,
    account_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_data: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        data = list_income_files(page, seller_id, timezone_name, timeout)
        last_data = data
        files = normalize_files(data)
        ready = choose_ready_income_file(files, created_after_ms)
        if ready:
            log_tiktok_poll(account_name, "收入明细文件", attempt, attempts, "已生成")
            return ready, data
        states = ", ".join(str(item.get("status") or "0") for item in files) or "空"
        log_tiktok_poll(account_name, "收入明细文件", attempt, attempts, f"状态 {states}")
        if attempt < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(f"TK 邮箱分支收入文件未生成: last={last_data}")


def get_income_download_url(
    page: Any,
    seller_id: str,
    timezone_name: str,
    file_id: str,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    url = api_url(FILE_DOWNLOAD_PATH, seller_id, timezone_name, {"file_id": file_id})
    data = browser_json_request(page, "GET", url, None, timeout)
    ensure_success(data, url)
    download_url = str(((data.get("data") or {}).get("url")) or "").strip()
    if not download_url:
        raise RuntimeError(f"TK 邮箱分支未返回下载 URL: {data}")
    return urljoin(SELLER_US_BASE, download_url), data


def open_bills_page(ctx: TiktokBrowserContext, target_url: str, timeout: int = 30) -> dict[str, Any]:
    listener = getattr(ctx.page, "listen", None)
    waiter = getattr(ctx.page, "wait", None)
    listening = False
    try:
        if listener:
            listener.start(SELLER_COMMON_PATH, method="GET")
            listening = True
        ctx.page.get(target_url)
        if waiter:
            waiter.url_change("/finance/bills", timeout=timeout, raise_err=False)
            waiter.doc_loaded(timeout=timeout, raise_err=False)
        if not listener:
            return {}
        packet = listener.wait(timeout=timeout, raise_err=False)
        if not packet or bool(getattr(packet, "is_failed", False)):
            return {}
        response = getattr(packet, "response", None)
        body = getattr(response, "body", None)
        status = int(getattr(response, "status", 0) or 0)
        if not isinstance(body, dict) or status < 200 or status >= 400:
            return {}
        return parse_seller_info(body, str(getattr(packet, "url", "") or SELLER_COMMON_PATH))
    finally:
        if listening:
            listener.stop()


def export_tiktok_email_income_with_ctx(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    ctx: TiktokBrowserContext,
    output_root: Path,
    request_timeout: int = 60,
) -> TaskResult:
    platform = str(task.get("platform") or "E1E2")
    capture_path = ""
    debug: dict[str, Any] = {"period": period.to_dict()}
    timezone_name = str(task.get("timezone") or DEFAULT_TIMEZONE_NAME)
    target_url = str(task.get("target_page") or BILLS_PAGE_URL)
    try:
        print(f"[TK邮箱] {account_name} 打开 Bills 页", flush=True)
        seller_info = open_bills_page(ctx, target_url, request_timeout)
        recorder_installed = install_browser_request_recorder(ctx.page)
        debug["diagnostic_recorder_installed"] = recorder_installed
        if seller_info:
            print(f"[TK邮箱] {account_name} Seller API 监听已就绪", flush=True)
            debug["seller_source"] = "listener"
        else:
            print(f"[TK邮箱] {account_name} Seller API 监听未命中，回退浏览器请求", flush=True)
            debug["seller_source"] = "browser_fetch"
            seller_info = get_seller_info(ctx.page, timezone_name, request_timeout)
        seller_id = str(seller_info["seller_id"])
        debug["seller"] = seller_info

        print(f"[TK邮箱] {account_name} 校验 statement list", flush=True)
        try:
            statement_data = list_statement_detail(ctx.page, seller_id, timezone_name, period, request_timeout)
            debug["statement_list_response"] = statement_data
        except RuntimeError as exc:
            debug["statement_list_skipped"] = {"reason": str(exc)}
            print(f"[TK邮箱] {account_name} statement list 校验失败，继续创建导出: {exc}", flush=True)

        created_after_ms = int(time.time() * 1000) - 180000
        print(f"[TK邮箱] {account_name} 创建收入明细导出", flush=True)
        export_payload, export_response = create_income_export(
            ctx.page,
            seller_id,
            timezone_name,
            period,
            request_timeout,
        )
        debug["export_payload"] = export_payload
        debug["export_response"] = export_response

        attempts, interval = tiktok_download_poll_options(task)
        income_file, list_response = wait_income_file(
            ctx.page,
            seller_id,
            timezone_name,
            created_after_ms,
            attempts,
            interval,
            request_timeout,
            account_name,
        )
        file_id = str(income_file["file_id"])
        download_url, download_response = get_income_download_url(
            ctx.page,
            seller_id,
            timezone_name,
            file_id,
            request_timeout,
        )

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        file_stem = download_stem(account_name, period, module_code(task, "销售数据"))
        output_path = download_dir / f"{file_stem}.xlsx"
        print(f"[TK邮箱] {account_name} 下载收入明细 xlsx", flush=True)
        download_bytes = browser_download_file(ctx.page, download_url, output_path, request_timeout)

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
                "file": income_file,
                "file_list_response": list_response,
                "download_response": download_response,
                "download_url": download_url,
                "output_path": str(output_path),
                "download_bytes": download_bytes,
                **({"browser_diagnostics": collect_browser_diagnostics(ctx.page)} if diagnostic_enabled(task) else {}),
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_email_income"),
            platform=platform,
            account_name=account_name,
            success=True,
            message="E1E2 销售数据导出完成，文件数 1",
            output_path=str(output_path),
            capture_path=capture_path,
            data={
                "period": period.to_dict(),
                "seller_id": seller_id,
                "file_id": file_id,
                "output": str(output_path),
                "download_bytes": download_bytes,
            },
        )
    except Exception as exc:
        no_data = is_tiktok_email_no_data_error(str(exc))
        try:
            file_stem = download_stem(account_name, period, module_code(task, "销售数据"))
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
                failed=not no_data,
            )
        except Exception:
            pass
        if no_data:
            return TaskResult(
                task_id=str(task.get("id") or "tiktok_email_income"),
                platform=platform,
                account_name=account_name,
                success=True,
                message="E1E2 销售数据暂无数据可导出。",
                capture_path=capture_path,
                data={"period": period.to_dict(), "no_data": True},
                status="no_data",
            )
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_email_income"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )


def export_tiktok_email_income(
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
        ctx = start_tiktok_browser(
            account_name,
            auth_path,
            login_timeout,
            target_url=str(task.get("target_page") or BILLS_PAGE_URL),
        )
        return export_tiktok_email_income_with_ctx(
            task=task,
            account_name=account_name,
            period=period,
            ctx=ctx,
            output_root=output_root,
            request_timeout=request_timeout,
        )
    finally:
        close_tiktok_browser(ctx)
