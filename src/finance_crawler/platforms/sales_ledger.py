from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
from finance_crawler.platforms.merchant_billing import resolve_supplier_context
from finance_crawler.platforms.shein_funds import API_BASE_URL, build_session, export_folder_name, safe_name


CHANGE_DETAIL_URL = f"{API_BASE_URL}/mils/changeDetail/page"
EXPORT_URL = f"{API_BASE_URL}/mils/exportApi/doExport/inventory_change_detail"
DISPLAY_CHANGE_TYPES = [
    "1", "2", "3", "4", "15", "17", "20", "22", "6", "7", "9",
    "10", "11", "12", "13", "16", "18", "19", "21", "23", "24",
]


def download_center_anchor(account_name: str) -> tuple[datetime, str]:
    source_tz = ZoneInfo("Asia/Shanghai")
    if account_name.strip().upper().startswith("SPP1"):
        target_tz = ZoneInfo("America/Los_Angeles")
        now_at_source = datetime.now(source_tz)
        return now_at_source.astimezone(target_tz).replace(tzinfo=None), "America/Los_Angeles"
    return datetime.now(source_tz).replace(tzinfo=None), "Asia/Shanghai"


def post_json(session: requests.Session, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) not in {"0", "200"}:
        raise RuntimeError(f"接口失败 {url}: code={data.get('code')} msg={data.get('msg')}")
    return data


def build_ledger_payload(period: PeriodRange, include_pagination: bool = True) -> dict[str, Any]:
    payload = {
        "displayChangeTypeList": DISPLAY_CHANGE_TYPES,
        "addTimeStart": period.start.strftime("%Y-%m-%d %H:%M:%S"),
        "addTimeEnd": period.end.strftime("%Y-%m-%d %H:%M:%S"),
        "changeTypeIndex": "1",
    }
    if include_pagination:
        payload.update({
            "pageNumber": 1,
            "pageSize": 50,
        })
    return payload


def output_extension(file_url: str, selected_row: dict[str, Any]) -> str:
    ext = str(selected_row.get("fileExtension") or "").strip().lower()
    if ext:
        return f".{ext.lstrip('.')}"
    lowered = file_url.split("?", 1)[0].lower()
    for candidate in (".zip", ".xlsx", ".xls", ".csv"):
        if lowered.endswith(candidate):
            return candidate
    return ".zip"


def is_sales_ledger_no_data_download_error(message: str) -> bool:
    text = str(message or "")
    return "台账变动明细" in text and "MILS-导出文件失败" in text


def export_sales_ledger(
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
        target_page = str(task.get("target_page") or f"{API_BASE_URL}/#/mils/report")
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
        debug["auth_context"] = {
            "final_url": auth_result.final_url,
            "user_agent": auth_result.user_agent,
            "cookie_length": len(auth_result.cookie or ""),
        }

        session = build_session(
            auth_result.cookie,
            auth_result.user_agent,
            target_page,
        )
        context = resolve_supplier_context(session, request_timeout)
        query_payload = build_ledger_payload(period, include_pagination=True)
        query_data = post_json(session, CHANGE_DETAIL_URL, query_payload, request_timeout)
        query_records = query_data.get("info", {}).get("data") or []
        if not isinstance(query_records, list):
            query_records = []
        debug.update({
            "supplier_context": context,
            "period": period.to_dict(),
            "ledger_query_url": CHANGE_DETAIL_URL,
            "ledger_query_payload": query_payload,
            "ledger_query_count": len(query_records),
            "ledger_query_sample": query_records[:3],
        })

        export_payload = build_ledger_payload(period, include_pagination=False)
        export_started_at, download_center_timezone = download_center_anchor(account_name)
        export_data = post_json(session, EXPORT_URL, export_payload, request_timeout)
        debug.update({
            "export_url": EXPORT_URL,
            "export_payload": export_payload,
            "export_response": export_data,
            "export_started_at": export_started_at.isoformat(),
            "download_center_timezone": download_center_timezone,
        })

        session.headers.update(
            {
                "Origin-Url": DOWNLOAD_PAGE_URL,
                "x-bbl-route": "/download-management/list",
                "time-zone": "Asia/Shanghai",
            }
        )
        selected_row, file_url, records, download_payload, file_url_response = wait_download_file_url(
            session,
            period,
            request_timeout,
            ["台账变动明细"],
            "zip",
            created_after=export_started_at - timedelta(minutes=3),
            allow_unanchored_fallback=False,
        )
        file_id = file_id_from_row(selected_row)

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        file_stem = download_stem(account_name, period, module_code(task, "sales"))
        download_dir = output_root / "downloads" / "shein" / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        output_path = download_dir / f"{file_stem}{output_extension(file_url, selected_row)}"
        final_resp = download_file(session, file_url, output_path, request_timeout)

        capture = {
            "captured_at": datetime.now().isoformat(),
            "task_id": task.get("id"),
            "platform": platform,
            "account_name": account_name,
            **debug,
            "download_list_payload": download_payload,
            "download_list_count": len(records),
            "download_list_sample": records[:3],
            "selected_download_row": selected_row,
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
            task_id=str(task.get("id") or "sales_ledger"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"销售台账下载完成，文件记录数 {len(records)}",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "selected_file_id": file_id},
        )
    except Exception as exc:
        no_data = is_sales_ledger_no_data_download_error(str(exc))
        try:
            period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
            file_stem = download_stem(account_name, period, module_code(task, "sales"))
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
                    "no_data": no_data,
                    **debug,
                },
                failed=True,
            )
        except Exception:
            pass
        return TaskResult(
            task_id=str(task.get("id") or "sales_ledger"),
            platform=platform,
            account_name=account_name,
            success=True if no_data else False,
            message="销售台账无数据，下载中心未生成文件。" if no_data else str(exc),
            capture_path=capture_path,
            data={"period": period.to_dict(), "no_data": True} if no_data else {},
            status="no_data" if no_data else "failed",
        )
