from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

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


SELLER_BASE = "https://seller.tiktokshopglobalselling.com"
API_BASE = "https://api16-normal-sg.tiktokshopglobalselling.com"
SETTLEMENT_PAGE_URL = f"{SELLER_BASE}/finance-settlement/settlement-summary/list?cate=2&shop_region=GB"
SELLER_COMMON_URL = f"{SELLER_BASE}/api/v2/seller/common/get?need_verify_account=true&default_region=GB&version=2"
LIST_BILLING_LINE_URL = f"{API_BASE}/api/finance/supplier/list_billing_line"
CREATE_DOWNLOAD_TASK_URL = f"{API_BASE}/api/finance/portal/download/create_download_task"
DOWNLOAD_RECORD_LIST_URL = f"{API_BASE}/api/download_center/get_download_record_list"
GET_DOWNLOAD_FILE_URL = f"{API_BASE}/api/download_center/get_download_file_url"


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
                    headers: {{'accept': 'application/json, text/plain, */*'}},
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
    result = page.run_js(script)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"TK 商家中心接口失败 {url}: {result!r}")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"TK 商家中心响应不是对象 {url}: {data!r}")
    return data


def ensure_base_success(data: dict[str, Any], url: str) -> dict[str, Any]:
    base = data.get("base_resp") or {}
    code = data.get("code", base.get("code"))
    message = data.get("message", base.get("message"))
    if str(code) in {"0", ""}:
        return data
    raise RuntimeError(f"TK 商家中心业务失败 {url}: code={code} message={message} data={data}")


def api_url(url: str, seller_id: str, locale: bool = True) -> str:
    parts = [
        "aid=6556",
        f"oec_seller_id={seller_id}",
        "timezone_offset=-480",
        f"_={int(time.time() * 1000)}",
    ]
    if locale:
        parts.insert(1, "locale=zh-CN")
    return f"{url}?{'&'.join(parts)}"


def get_seller_id(ctx: TiktokBrowserContext, timeout: int) -> str:
    data = browser_json_request(ctx.page, "GET", SELLER_COMMON_URL, None, timeout)
    ensure_base_success(data, SELLER_COMMON_URL)
    seller = ((data.get("data") or {}).get("seller") or {})
    seller_id = str(seller.get("seller_id") or "").strip()
    if not seller_id:
        raise RuntimeError(f"TK 未识别 seller_id: {data}")
    return seller_id


def period_query(period: PeriodRange) -> dict[str, Any]:
    start_date = period.start.strftime("%Y-%m-%d")
    end_date = period.end.strftime("%Y-%m-%d")
    return {
        "start_time_ts": start_date,
        "begin_time_ts": start_date,
        "end_time_ts": end_date,
        "bill_period_begin_time": {
            "gte": str(period.start_ms),
            "lte": str(period.end_ms),
        },
    }


def list_billing_line(page: Any, seller_id: str, query_param: dict[str, Any], timeout: int) -> dict[str, Any]:
    payload = {
        "query_param": query_param,
        "data_type": 2,
        "page_info": {"page_no": 1, "page_size": 10},
    }
    data = browser_json_request(page, "POST", api_url(LIST_BILLING_LINE_URL, seller_id), payload, timeout)
    return ensure_base_success(data, LIST_BILLING_LINE_URL)


def create_download_task(page: Any, seller_id: str, query_param: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
    payload = {
        "task_type": 3,
        "download_params": {
            "list_billing_line_request": {
                "data_type": 2,
                "query_param": query_param,
            }
        },
    }
    data = browser_json_request(page, "POST", api_url(CREATE_DOWNLOAD_TASK_URL, seller_id), payload, timeout)
    ensure_base_success(data, CREATE_DOWNLOAD_TASK_URL)
    task_id = str(((data.get("data") or {}).get("task_id")) or "").strip()
    if not task_id:
        raise RuntimeError(f"TK 销售数据导出未返回 task_id: {data}")
    return task_id, data


def list_download_records(page: Any, seller_id: str, timeout: int) -> dict[str, Any]:
    payload = {"page_info": {"page_no": 1, "page_size": 20}}
    data = browser_json_request(page, "POST", api_url(DOWNLOAD_RECORD_LIST_URL, seller_id, locale=False), payload, timeout)
    return ensure_base_success(data, DOWNLOAD_RECORD_LIST_URL)


def record_matches(row: dict[str, Any], task_id: str, created_after_ms: int) -> bool:
    if int(row.get("status") or 0) != 3:
        return False
    if str(row.get("task_id") or "") == task_id:
        return True
    file_name = str(row.get("file_name") or "")
    source_name = str(row.get("source_name") or "")
    if "已出账货款" not in file_name or "订单明细" not in file_name:
        return False
    if "已出账" not in source_name or "订单明细" not in source_name:
        return False
    download_time = int(row.get("download_time") or 0)
    return download_time >= created_after_ms


def wait_download_record(
    page: Any,
    seller_id: str,
    task_id: str,
    created_after_ms: int,
    attempts: int,
    interval_seconds: int,
    timeout: int,
    account_name: str = "",
    target: str = "下载中心文件",
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_data: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        data = list_download_records(page, seller_id, timeout)
        last_data = data
        records = (((data.get("data") or {}).get("records")) or [])
        record_count = len(records) if isinstance(records, list) else 0
        if isinstance(records, list):
            for row in records:
                if isinstance(row, dict) and record_matches(row, task_id, created_after_ms):
                    if account_name:
                        log_tiktok_poll(account_name, target, attempt, attempts, "已生成")
                    return row, data
        if account_name:
            log_tiktok_poll(account_name, target, attempt, attempts, f"未生成，下载中心记录 {record_count} 条")
        if attempt < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(f"TK 下载中心未找到销售数据文件: task_id={task_id}, last={last_data}")


def get_download_file_url_once(page: Any, seller_id: str, task_id: str, timeout: int) -> tuple[str, dict[str, Any]]:
    payload = {"task_id": task_id}
    data = browser_json_request(page, "POST", api_url(GET_DOWNLOAD_FILE_URL, seller_id, locale=False), payload, timeout)
    ensure_base_success(data, GET_DOWNLOAD_FILE_URL)
    url = str(((data.get("data") or {}).get("url")) or "").strip()
    if not url:
        raise RuntimeError(f"TK 下载中心未返回文件 URL: {data}")
    return url, data


def wait_download_file_url(
    page: Any,
    seller_id: str,
    task_id: str,
    attempts: int,
    interval_seconds: int,
    timeout: int,
    account_name: str = "",
    target: str = "下载链接",
) -> tuple[str, dict[str, Any]]:
    last_error = ""
    last_data: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        try:
            url, data = get_download_file_url_once(page, seller_id, task_id, timeout)
            if account_name:
                log_tiktok_poll(account_name, target, attempt, attempts, "已获取")
            return url, data
        except Exception as exc:
            last_error = str(exc)
            if "任务尚未下载完成" not in last_error and "98001001" not in last_error:
                raise
            last_data = {"error": last_error}
            if account_name:
                log_tiktok_poll(account_name, target, attempt, attempts, "未就绪")
            if attempt < attempts:
                time.sleep(interval_seconds)
    raise RuntimeError(f"TK 下载中心已出记录但未返回文件 URL: task_id={task_id}, last={last_data}")


def download_tiktok_file(page: Any, relative_url: str, output_path: Path, timeout: int) -> tuple[str, int]:
    candidates = []
    if relative_url.startswith("http"):
        candidates.append(relative_url)
    else:
        candidates.append(urljoin(API_BASE, relative_url))
        candidates.append(urljoin(SELLER_BASE, relative_url))

    last_error = ""
    for candidate in candidates:
        try:
            size = browser_download_file(page, candidate, output_path, timeout)
            return candidate, size
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(f"TK 销售数据下载失败: {last_error}")


def export_tiktok_sales_data_with_ctx(
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
        print(f"[TK] {account_name} 打开销售结算页", flush=True)
        ctx.page.get(SETTLEMENT_PAGE_URL)
        time.sleep(3)
        debug["diagnostic_recorder_installed"] = install_browser_request_recorder(ctx.page)
        print(f"[TK] {account_name} 识别 seller_id", flush=True)
        seller_id = get_seller_id(ctx, request_timeout)
        query_param = period_query(period)
        debug.update({"seller_id": seller_id, "query_param": query_param})

        print(f"[TK] {account_name} 查询已出账订单明细", flush=True)
        list_response = list_billing_line(ctx.page, seller_id, query_param, request_timeout)
        debug["list_response"] = list_response

        print(f"[TK] {account_name} 创建销售数据导出任务", flush=True)
        export_started_ms = int(time.time() * 1000) - 180000
        task_id, create_response = create_download_task(ctx.page, seller_id, query_param, request_timeout)
        debug.update({"download_task_id": task_id, "create_response": create_response})

        print(f"[TK] {account_name} 等待下载中心生成文件 task_id={task_id}", flush=True)
        attempts, interval = tiktok_download_poll_options(task)
        record, record_response = wait_download_record(
            ctx.page,
            seller_id,
            task_id,
            export_started_ms,
            attempts,
            interval,
            request_timeout,
            account_name,
            "销售数据文件",
        )
        print(f"[TK] {account_name} 获取下载链接", flush=True)
        file_url, file_url_response = wait_download_file_url(
            ctx.page,
            seller_id,
            task_id,
            attempts,
            interval,
            request_timeout,
            account_name,
            "销售数据下载链接",
        )

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        file_stem = download_stem(account_name, period, module_code(task, "TK销售数据"))
        output_path = download_dir / f"{file_stem}.xlsx"
        print(f"[TK] {account_name} 下载销售数据 xlsx", flush=True)
        full_url, download_bytes = download_tiktok_file(ctx.page, file_url, output_path, request_timeout)

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
                "download_record": record,
                "record_response": record_response,
                "file_url_response": file_url_response,
                "download_url": full_url,
                "output_path": str(output_path),
                "download_bytes": download_bytes,
                **({"browser_diagnostics": collect_browser_diagnostics(ctx.page)} if diagnostic_enabled(task) else {}),
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_sales_data"),
            platform=platform,
            account_name=account_name,
            success=True,
            message="TK 销售数据导出完成，文件数 1",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "output": str(output_path), "download_bytes": download_bytes},
        )
    except Exception as exc:
        try:
            file_stem = download_stem(account_name, period, module_code(task, "TK销售数据"))
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
            task_id=str(task.get("id") or "tiktok_sales_data"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )


def export_tiktok_sales_data(
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
        ctx = start_tiktok_browser(account_name, auth_path, login_timeout, target_url=SETTLEMENT_PAGE_URL)
        return export_tiktok_sales_data_with_ctx(
            task=task,
            account_name=account_name,
            period=period,
            ctx=ctx,
            output_root=output_root,
            request_timeout=request_timeout,
        )
    finally:
        close_tiktok_browser(ctx)
