from __future__ import annotations

import time
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
    download_center_records,
    download_file,
    file_id_from_row,
    matching_download_records,
    try_get_file_url,
)
from finance_crawler.platforms.merchant_billing import resolve_supplier_context
from finance_crawler.platforms.sales_ledger import output_extension
from finance_crawler.platforms.shein_funds import API_BASE_URL, build_session, export_folder_name, safe_name


PAYED_LIST_URL = f"{API_BASE_URL}/gsfs/finance/platform/payedList"
PAYED_STATISTICS_URL = f"{API_BASE_URL}/gsfs/finance/platform/payedList/statistics"
EXPORT_PAYED_DETAIL_URL = f"{API_BASE_URL}/gsfs/common/file/export/exportPlatformPayedCheckDetail"
DEFAULT_KEYWORD_GROUPS = [
    ["已完成账单", "账单商品维度"],
]


def post_json(session: requests.Session, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) not in {"0", "200"}:
        raise RuntimeError(f"接口失败 {url}: code={data.get('code')} msg={data.get('msg')}")
    return data


def platform_income_payload(period: PeriodRange, include_pagination: bool = True) -> dict[str, Any]:
    payload = {
        "payBeginTime": period.start.strftime("%Y-%m-%d %H:%M:%S"),
        "payEndTime": period.end.strftime("%Y-%m-%d %H:%M:%S"),
        "page": 1,
        "perPage": 50,
    }
    if include_pagination:
        payload["pageSize"] = 50
    else:
        payload.update({"type": 30, "mode": 2, "pageSize": 50})
    return payload


def download_center_anchor(period: PeriodRange) -> tuple[datetime, str]:
    tzinfo = period.start.tzinfo or ZoneInfo("Asia/Shanghai")
    timezone_name = getattr(tzinfo, "key", str(tzinfo))
    return datetime.now(tzinfo).replace(tzinfo=None), timezone_name


def is_download_ready(row: dict[str, Any]) -> bool:
    status = row.get("fileStatus")
    return status in (None, "", 1, "1")


def keyword_groups(task: dict[str, Any]) -> list[list[str]]:
    configured = task.get("download_keyword_groups") or task.get("download_keywords")
    groups: list[list[str]] = []
    if isinstance(configured, list):
        for item in configured:
            if isinstance(item, list):
                group = [str(value) for value in item if str(value or "").strip()]
            else:
                group = [str(item)] if str(item or "").strip() else []
            if group:
                groups.append(group)
    return groups or DEFAULT_KEYWORD_GROUPS


def wait_platform_income_file_url(
    session: requests.Session,
    period: PeriodRange,
    timeout: int,
    groups: list[list[str]],
    extension: str = "xlsx",
    created_after: datetime | None = None,
    fallback_created_after: datetime | None = None,
    attempts: int = 36,
    interval_seconds: int = 5,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, Any], dict[str, Any], list[str], bool]:
    last_records: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}
    last_file_response: dict[str, Any] = {}
    last_group: list[str] = []

    def candidates_after(anchor: datetime | None) -> list[tuple[dict[str, Any], list[str]]]:
        candidates: list[tuple[dict[str, Any], list[str]]] = []
        for group in groups:
            for row in matching_download_records(last_records, group, extension, created_after=anchor):
                if not is_download_ready(row):
                    continue
                if not any(existing is row or existing == row for existing, _ in candidates):
                    candidates.append((row, group))
        return candidates

    def resolve_first_url(
        candidates: list[tuple[dict[str, Any], list[str]]],
        reused_existing: bool,
    ) -> tuple[dict[str, Any], str, dict[str, Any], list[str], bool] | None:
        nonlocal last_file_response, last_group
        for row, group in candidates[:12]:
            try:
                file_id = file_id_from_row(row)
                url, data = try_get_file_url(session, file_id, timeout)
                last_group = group
                last_file_response = {
                    "candidate": row,
                    "keyword_group": group,
                    "reused_existing": reused_existing,
                    "response": data,
                }
                if url:
                    return row, url, data, group, reused_existing
            except RuntimeError as exc:
                last_file_response = {
                    "error": str(exc),
                    "candidate": row,
                    "keyword_group": group,
                    "reused_existing": reused_existing,
                }
        return None

    for attempt in range(1, max(1, attempts) + 1):
        last_payload, last_records = download_center_records(session, period, timeout)
        candidates = candidates_after(created_after)
        if not candidates:
            last_file_response = {
                "error": f"下载中心没有匹配文件: keyword_groups={groups}, extension={extension}"
            }
        result = resolve_first_url(candidates, reused_existing=False)
        if result:
            row, url, data, group, reused_existing = result
            return row, url, last_records, last_payload, data, group, reused_existing
        if attempt < attempts:
            time.sleep(interval_seconds)

    if fallback_created_after:
        last_payload, last_records = download_center_records(session, period, timeout)
        result = resolve_first_url(candidates_after(fallback_created_after), reused_existing=True)
        if result:
            row, url, data, group, reused_existing = result
            return row, url, last_records, last_payload, data, group, reused_existing

    raise RuntimeError(
        "下载中心轮询后仍未拿到文件链接: "
        f"keyword_groups={groups}, extension={extension}, "
        f"last_keyword_group={last_group}, "
        f"last_file_response={last_file_response}, "
        f"last_records_sample={last_records[:5]}"
    )


def export_platform_income(
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
        target_page = str(task.get("target_page") or f"{API_BASE_URL}/#/gsfs/finance-management/list")
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
        query_payload = platform_income_payload(period, include_pagination=True)
        query_data = post_json(session, PAYED_LIST_URL, query_payload, request_timeout)
        statistics_data = post_json(session, PAYED_STATISTICS_URL, query_payload, request_timeout)
        query_records = query_data.get("info", {}).get("data") or []
        if not isinstance(query_records, list):
            query_records = []

        export_payload = platform_income_payload(period, include_pagination=False)
        export_started_at, download_center_timezone = download_center_anchor(period)
        export_data = post_json(session, EXPORT_PAYED_DETAIL_URL, export_payload, request_timeout)
        debug.update({
            "supplier_context": context,
            "period": period.to_dict(),
            "query_url": PAYED_LIST_URL,
            "query_payload": query_payload,
            "query_count": len(query_records),
            "query_sample": query_records[:3],
            "statistics_url": PAYED_STATISTICS_URL,
            "statistics_response": statistics_data,
            "export_url": EXPORT_PAYED_DETAIL_URL,
            "export_payload": export_payload,
            "export_response": export_data,
            "export_started_at": export_started_at.isoformat(),
            "download_center_timezone": download_center_timezone,
        })

        session.headers.update(
            {
                "Origin-Url": DOWNLOAD_PAGE_URL,
                "x-bbl-route": "/download-management/list",
                "time-zone": download_center_timezone,
            }
        )
        recent_fallback_hours = int(task.get("download_recent_fallback_hours") or 0)
        selected_row, file_url, records, download_payload, file_url_response, matched_keywords, reused_existing = (
            wait_platform_income_file_url(
                session,
                period,
                request_timeout,
                keyword_groups(task),
                str(task.get("download_extension") or "xlsx"),
                created_after=export_started_at - timedelta(minutes=3),
                fallback_created_after=(
                    export_started_at - timedelta(hours=recent_fallback_hours)
                    if recent_fallback_hours > 0
                    else None
                ),
                attempts=int(task.get("download_attempts") or 18),
                interval_seconds=int(task.get("download_interval_seconds") or 5),
            )
        )
        file_id = file_id_from_row(selected_row)

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        file_stem = download_stem(account_name, period, module_code(task, "income"))
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
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
            "matched_download_keywords": matched_keywords,
            "reused_existing_download": reused_existing,
            "file_url_response": file_url_response,
            "file_url": file_url,
            "final_download_status": final_resp.status_code,
            "final_download_content_type": final_resp.headers.get("Content-Type", ""),
            "final_download_content_length": len(final_resp.content),
        }
        capture_path = write_capture_file(task, output_root, platform, period, file_stem, capture)
        return TaskResult(
            task_id=str(task.get("id") or "platform_income"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"销售数据平台费用下载完成，文件记录数 {len(records)}",
            output_path=str(output_path),
            capture_path=capture_path,
            data={
                "period": period.to_dict(),
                "selected_file_id": file_id,
                "matched_keywords": matched_keywords,
                "reused_existing_download": reused_existing,
            },
        )
    except Exception as exc:
        try:
            period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
            file_stem = download_stem(account_name, period, module_code(task, "income"))
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
            task_id=str(task.get("id") or "platform_income"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )
