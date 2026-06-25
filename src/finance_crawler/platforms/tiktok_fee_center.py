from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finance_crawler.debug_files import write_capture_file
from finance_crawler.diagnostics import collect_browser_diagnostics, diagnostic_enabled, install_browser_request_recorder
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import export_folder_name
from finance_crawler.platforms.tiktok_common import log_tiktok_poll, tiktok_download_poll_options
from finance_crawler.platforms.tiktok_sales_data import (
    API_BASE,
    CREATE_DOWNLOAD_TASK_URL,
    DOWNLOAD_RECORD_LIST_URL,
    GET_DOWNLOAD_FILE_URL,
    SELLER_BASE,
    api_url,
    browser_json_request,
    download_tiktok_file,
    ensure_base_success,
    get_seller_id,
    wait_download_file_url,
)
from finance_crawler.platforms.tiktok_withdrawals import (
    TiktokBrowserContext,
    close_tiktok_browser,
    js_json,
    start_tiktok_browser,
)


FEE_CENTER_PAGE_URL = f"{SELLER_BASE}/finance-settlement/fee-center/payFee?shop_region=GB"
LIST_INVOICE_ITEM_URL = f"{API_BASE}/api/finance/supplier/list_invoice_item"
FREE_SAMPLE_LIST_URL = (
    f"{API_BASE}/api/finance_management/settlement/supplier/"
    "list_free_sample_logistics_bill_item_records"
)
FREE_SAMPLE_EXPORT_URL = (
    f"{API_BASE}/api/finance_management/settlement/supplier/"
    "down_load_free_sample_logistics_bill_item_records"
)
EPR_POB_LIST_URL = f"{API_BASE}/api/finance_management/settlement/supplier/list_epr_feebill_item_records"
EPR_POB_EXPORT_URL = f"{API_BASE}/api/finance_management/settlement/supplier/down_load_epr_fee_bill_item_records"


@dataclass(frozen=True)
class FeeExportSpec:
    id: str
    title: str
    file_keywords: tuple[str, ...]
    source_keywords: tuple[str, ...]
    export_url: str
    list_url: str
    output_suffix: str


FEE_EXPORTS = (
    FeeExportSpec(
        id="logistics",
        title="物流供应链服务费",
        file_keywords=("物流供应链服务费账单明细",),
        source_keywords=("揽收", "退货服务费账单明细"),
        export_url=CREATE_DOWNLOAD_TASK_URL,
        list_url=LIST_INVOICE_ITEM_URL,
        output_suffix="物流供应链服务费",
    ),
    FeeExportSpec(
        id="free_sample",
        title="免费样品物流费",
        file_keywords=("免费样品", "物流费明细"),
        source_keywords=("免费样品物流费费用明细",),
        export_url=FREE_SAMPLE_EXPORT_URL,
        list_url=FREE_SAMPLE_LIST_URL,
        output_suffix="免费样品物流费",
    ),
    FeeExportSpec(
        id="epr_pob",
        title="EPR POB费用",
        file_keywords=("EPR_POB", "费用明细"),
        source_keywords=("EPR POB费用明细",),
        export_url=EPR_POB_EXPORT_URL,
        list_url=EPR_POB_LIST_URL,
        output_suffix="EPR_POB费用",
    ),
)


def build_fee_query(spec: FeeExportSpec, period: PeriodRange) -> dict[str, Any]:
    if spec.id == "logistics":
        return {
            "param": {
                "invoice_date_begin": str(period.start_ms),
                "invoice_date_end": str(period.end_ms),
            },
            "query_source": "a_logistics_fee",
        }
    if spec.id == "free_sample":
        return {
            "query_param": {
                "bill_time_start": period.start_ms,
                "bill_time_end": period.end_ms,
                "bill_item_status": 3,
            }
        }
    if spec.id == "epr_pob":
        return {
            "query_param": {
                "bill_period_begin_time_start": period.start_ms,
                "bill_period_begin_time_end": period.end_ms,
                "bill_status_list": [100],
            }
        }
    raise RuntimeError(f"未知 TK 费用中心子模块: {spec.id}")


def build_fee_list_payload(spec: FeeExportSpec, period: PeriodRange) -> dict[str, Any]:
    payload = build_fee_query(spec, period)
    return {**payload, "page_info": {"page_no": 1, "page_size": 10}}


def build_fee_export_payload(spec: FeeExportSpec, period: PeriodRange) -> dict[str, Any]:
    query = build_fee_query(spec, period)
    if spec.id == "logistics":
        return {
            "task_type": 8,
            "download_params": {
                "list_invoice_items_request": query,
            },
        }
    return query


def browser_text_request(
    page: Any,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
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
                return {{ok: r.ok, status: r.status, url: r.url, text}};
            }} finally {{
                clearTimeout(timer);
            }}
        }})();
    """
    result = page.run_js(script)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"TK 费用中心接口失败 {url}: {result!r}")
    text = str(result.get("text") or "")
    try:
        data = __import__("json").loads(text) if text else {}
    except Exception:
        data = {"rawText": text}
    if not isinstance(data, dict):
        data = {"raw": data}
    return text, data


def extract_task_id(raw_text: str, data: dict[str, Any], spec: FeeExportSpec) -> str:
    match = re.search(r'"task_id"\s*:\s*"?(\d+)"?', raw_text or "")
    if match:
        return match.group(1)
    task_id = str(((data.get("data") or {}).get("task_id")) or data.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"TK {spec.title} 导出未返回 task_id: {data}")
    return task_id


def create_fee_download_task(
    page: Any,
    seller_id: str,
    spec: FeeExportSpec,
    period: PeriodRange,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    payload = build_fee_export_payload(spec, period)
    raw_text, data = browser_text_request(page, "POST", api_url(spec.export_url, seller_id), payload, timeout)
    ensure_base_success(data, spec.export_url)
    return extract_task_id(raw_text, data, spec), data


def list_fee_rows(page: Any, seller_id: str, spec: FeeExportSpec, period: PeriodRange, timeout: int) -> dict[str, Any]:
    data = browser_json_request(
        page,
        "POST",
        api_url(spec.list_url, seller_id),
        build_fee_list_payload(spec, period),
        timeout,
    )
    return ensure_base_success(data, spec.list_url)


def list_download_records(page: Any, seller_id: str, timeout: int) -> dict[str, Any]:
    payload = {"page_info": {"page_no": 1, "page_size": 20}}
    data = browser_json_request(page, "POST", api_url(DOWNLOAD_RECORD_LIST_URL, seller_id, locale=False), payload, timeout)
    return ensure_base_success(data, DOWNLOAD_RECORD_LIST_URL)


def record_matches_fee(row: dict[str, Any], spec: FeeExportSpec, task_id: str, created_after_ms: int) -> bool:
    if int(row.get("status") or 0) != 3:
        return False
    if str(row.get("task_id") or "") == str(task_id):
        return True
    file_name = str(row.get("file_name") or "")
    source_name = str(row.get("source_name") or "")
    if not all(keyword in file_name for keyword in spec.file_keywords):
        return False
    if not all(keyword in source_name for keyword in spec.source_keywords):
        return False
    return int(row.get("download_time") or 0) >= created_after_ms


def wait_fee_download_record(
    page: Any,
    seller_id: str,
    spec: FeeExportSpec,
    task_id: str,
    created_after_ms: int,
    attempts: int,
    interval_seconds: int,
    timeout: int,
    account_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_data: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        data = list_download_records(page, seller_id, timeout)
        last_data = data
        records = (((data.get("data") or {}).get("records")) or [])
        record_count = len(records) if isinstance(records, list) else 0
        if isinstance(records, list):
            for row in records:
                if isinstance(row, dict) and record_matches_fee(row, spec, task_id, created_after_ms):
                    if account_name:
                        log_tiktok_poll(account_name, f"{spec.title}文件", attempt, attempts, "已生成")
                    return row, data
        if account_name:
            log_tiktok_poll(account_name, f"{spec.title}文件", attempt, attempts, f"未生成，下载中心记录 {record_count} 条")
        if attempt < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(f"TK 下载中心未找到{spec.title}文件: task_id={task_id}, last={last_data}")


def export_one_fee(
    ctx: TiktokBrowserContext,
    seller_id: str,
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    output_root: Path,
    spec: FeeExportSpec,
    request_timeout: int,
) -> dict[str, Any]:
    print(f"[TK] {account_name} 查询{spec.title}", flush=True)
    list_response = list_fee_rows(ctx.page, seller_id, spec, period, request_timeout)

    print(f"[TK] {account_name} 创建{spec.title}导出任务", flush=True)
    export_started_ms = int(time.time() * 1000) - 180000
    task_id, create_response = create_fee_download_task(ctx.page, seller_id, spec, period, request_timeout)

    attempts, interval = tiktok_download_poll_options(task)
    print(f"[TK] {account_name} 等待{spec.title}下载中心文件 task_id={task_id}", flush=True)
    record, record_response = wait_fee_download_record(
        ctx.page,
        seller_id,
        spec,
        task_id,
        export_started_ms,
        attempts,
        interval,
        request_timeout,
        account_name,
    )
    file_url, file_url_response = wait_download_file_url(
        ctx.page,
        seller_id,
        task_id,
        attempts,
        interval,
        request_timeout,
        account_name,
        f"{spec.title}下载链接",
    )

    period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
    download_dir = output_root / "downloads" / "tiktok" / period.period_type / period_label / export_folder_name(task)
    download_dir.mkdir(parents=True, exist_ok=True)
    file_stem = download_stem(account_name, period, module_code(task, "TK费用中心"), spec.output_suffix)
    output_path = download_dir / f"{file_stem}.xlsx"
    print(f"[TK] {account_name} 下载{spec.title} xlsx", flush=True)
    full_url, download_bytes = download_tiktok_file(ctx.page, file_url, output_path, request_timeout)
    return {
        "spec_id": spec.id,
        "title": spec.title,
        "task_id": task_id,
        "output_path": str(output_path),
        "download_bytes": download_bytes,
        "download_url": full_url,
        "list_response": list_response,
        "create_response": create_response,
        "download_record": record,
        "record_response": record_response,
        "file_url_response": file_url_response,
    }


def export_tiktok_fee_center_with_ctx(
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
        print(f"[TK] {account_name} 打开费用中心", flush=True)
        ctx.page.get(FEE_CENTER_PAGE_URL)
        time.sleep(3)
        debug["diagnostic_recorder_installed"] = install_browser_request_recorder(ctx.page)
        print(f"[TK] {account_name} 识别 seller_id", flush=True)
        seller_id = get_seller_id(ctx, request_timeout)
        debug["seller_id"] = seller_id

        outputs: list[dict[str, Any]] = []
        for spec in FEE_EXPORTS:
            outputs.append(export_one_fee(ctx, seller_id, task, account_name, period, output_root, spec, request_timeout))

        file_stem = download_stem(account_name, period, module_code(task, "TK费用中心"))
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
                "outputs": outputs,
                **({"browser_diagnostics": collect_browser_diagnostics(ctx.page)} if diagnostic_enabled(task) else {}),
            },
        )
        output_paths = [item["output_path"] for item in outputs]
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_fee_center"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"TK 费用中心导出完成，文件数 {len(output_paths)}",
            output_path="; ".join(output_paths),
            capture_path=capture_path,
            data={"period": period.to_dict(), "outputs": output_paths},
        )
    except Exception as exc:
        try:
            file_stem = download_stem(account_name, period, module_code(task, "TK费用中心"))
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
            task_id=str(task.get("id") or "tiktok_fee_center"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )


def export_tiktok_fee_center(
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
        ctx = start_tiktok_browser(account_name, auth_path, login_timeout, target_url=FEE_CENTER_PAGE_URL)
        return export_tiktok_fee_center_with_ctx(
            task=task,
            account_name=account_name,
            period=period,
            ctx=ctx,
            output_root=output_root,
            request_timeout=request_timeout,
        )
    finally:
        close_tiktok_browser(ctx)
