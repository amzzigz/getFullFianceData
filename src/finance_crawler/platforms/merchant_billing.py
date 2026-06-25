from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from finance_crawler.auth import auth_login
from finance_crawler.debug_files import write_capture_file
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.download_center import (
    DOWNLOAD_PAGE_URL,
    download_file,
    file_id_from_row,
    wait_download_file_url,
)
from finance_crawler.platforms.shein_funds import API_BASE_URL, build_session, export_folder_name, safe_name


SUPPLIER_INFO_URL = f"{API_BASE_URL}/sso/public/account/supplier/getSupplierOperateInfo"
REPORT_ORDER_LIST_URL = f"{API_BASE_URL}/gsfs/finance/reportOrder/dualMode/reportOrderList"
EXPORT_REPORT_ORDER_URL = f"{API_BASE_URL}/gsfs/common/file/export/dualModeReportOrder"


def is_supplier_redirect(data: dict[str, Any]) -> bool:
    return str(data.get("code")) == "20302" or "子系统登录重定向" in str(data.get("msg") or "")


def resolve_supplier_context(session: requests.Session, timeout: int) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for attempt in range(1, 9):
        response = session.post(SUPPLIER_INFO_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if str(data.get("code")) == "0":
            break
        if is_supplier_redirect(data) and attempt < 8:
            time.sleep(3)
            continue
        break
    if str(data.get("code")) != "0":
        raise RuntimeError(f"接口失败 {SUPPLIER_INFO_URL}: code={data.get('code')} msg={data.get('msg')}")
    info = data.get("info") or {}
    if not isinstance(info, dict):
        raise RuntimeError(f"未能获取供应商上下文: {data}")
    return info


def post_json(session: requests.Session, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) not in {"0", "200"}:
        raise RuntimeError(f"接口失败 {url}: code={data.get('code')} msg={data.get('msg')}")
    return data


def report_order_payload(period: PeriodRange, include_pagination: bool = True) -> dict[str, Any]:
    payload = {
        "addTimeStart": period.start.strftime("%Y-%m-%d %H:%M:%S"),
        "addTimeEnd": period.end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if include_pagination:
        payload.update({"page": 1, "perPage": 30})
    else:
        payload.update({"type": 1, "mode": 2})
    return payload


def download_keywords(task: dict[str, Any]) -> list[str]:
    configured = task.get("download_keywords")
    if isinstance(configured, list):
        values = [str(item) for item in configured if str(item or "").strip()]
        if values:
            return values
    return ["简易平台收入"]


def output_extension(file_url: str, selected_row: dict[str, Any]) -> str:
    ext = str(selected_row.get("fileExtension") or "").strip().lower()
    if ext:
        return f".{ext.lstrip('.')}"
    lowered = file_url.split("?", 1)[0].lower()
    for candidate in (".zip", ".xlsx", ".xls", ".csv"):
        if lowered.endswith(candidate):
            return candidate
    return ".xlsx"


def naive_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def export_merchant_billing(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    platform = str(task.get("platform") or "shein")
    capture_path = ""
    debug: dict[str, Any] = {}
    try:
        target_page = str(task.get("target_page") or f"{API_BASE_URL}/#/gsfs/finance/reportOrder/dualMode")
        auth_result = task.get("_auth_result") or auth_login(
            account_name,
            auth_path,
            fallback_timeout_seconds=login_timeout,
            target_url=target_page,
        )
        if not auth_result.success:
            raise RuntimeError(f"紫鸟鉴权失败: {auth_result.message}")
        if not auth_result.cookie:
            raise RuntimeError("紫鸟鉴权成功，但未返回 cookie。")

        session = build_session(
            auth_result.cookie,
            auth_result.user_agent,
            target_page,
        )
        context = resolve_supplier_context(session, request_timeout)
        query_payload = report_order_payload(period, include_pagination=True)
        query_data = post_json(session, REPORT_ORDER_LIST_URL, query_payload, request_timeout)
        query_records = query_data.get("info", {}).get("data") or []
        if not isinstance(query_records, list):
            query_records = []

        export_payload = report_order_payload(period, include_pagination=False)
        export_started_at = datetime.now(period.start.tzinfo).replace(tzinfo=None)
        export_data = post_json(session, EXPORT_REPORT_ORDER_URL, export_payload, request_timeout)
        debug.update({
            "supplier_context": context,
            "period": period.to_dict(),
            "query_url": REPORT_ORDER_LIST_URL,
            "query_payload": query_payload,
            "query_count": len(query_records),
            "query_sample": query_records[:3],
            "export_url": EXPORT_REPORT_ORDER_URL,
            "export_payload": export_payload,
            "export_response": export_data,
            "export_started_at": export_started_at.isoformat(),
        })

        session.headers.update(
            {
                "Origin-Url": DOWNLOAD_PAGE_URL,
                "x-bbl-route": "/download-management/list",
                "time-zone": str(task.get("timezone") or "Asia/Shanghai"),
            }
        )
        chosen_row, file_url, records, list_payload, file_url_response = wait_download_file_url(
            session,
            period,
            request_timeout,
            download_keywords(task),
            str(task.get("download_extension") or "xlsx"),
            created_after=export_started_at - timedelta(minutes=3),
            allow_unanchored_fallback=True,
            fallback_created_after=naive_datetime(period.end),
            attempts=int(task.get("download_attempts") or 24),
            interval_seconds=int(task.get("download_interval_seconds") or 5),
        )
        file_id = file_id_from_row(chosen_row)

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        file_stem = download_stem(account_name, period, module_code(task, "merchant_bill"))
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        output_path = download_dir / f"{file_stem}{output_extension(file_url, chosen_row)}"

        final_resp = download_file(session, file_url, output_path, request_timeout)

        capture = {
            "captured_at": datetime.now().isoformat(),
            "task_id": task.get("id"),
            "platform": platform,
            "account_name": account_name,
            **debug,
            "download_list_payload": list_payload,
            "download_list_count": len(records),
            "download_list_sample": records[:3],
            "selected_download_row": chosen_row,
            "selected_file_id": file_id,
            "file_url_response": file_url_response,
            "file_url": file_url,
            "final_download_status": final_resp.status_code,
            "final_download_content_type": final_resp.headers.get("Content-Type", ""),
            "final_download_content_length": len(final_resp.content),
            "auth_context": {
                "final_url": auth_result.final_url,
                "user_agent": auth_result.user_agent,
                "cookie_length": len(auth_result.cookie or ""),
            },
        }
        capture_path = write_capture_file(task, output_root, platform, period, file_stem, capture)

        return TaskResult(
            task_id=str(task.get("id") or "merchant_billing"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"商家账单下载完成，文件记录数 {len(records)}",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "download_list_count": len(records), "selected_file_id": file_id},
        )
    except Exception as exc:
        try:
            period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
            file_stem = download_stem(account_name, period, module_code(task, "merchant_bill"))
            capture_path = write_capture_file(
                task,
                output_root,
                platform,
                period,
                file_stem,
                {
                    "captured_at": datetime.now().isoformat(),
                    "task_id": task.get("id"),
                    "platform": platform,
                    "account_name": account_name,
                    "success": False,
                    "error": str(exc),
                    **debug,
                },
                failed=True,
            )
        except Exception:
            pass
        return TaskResult(
            task_id=str(task.get("id") or "merchant_billing"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )
