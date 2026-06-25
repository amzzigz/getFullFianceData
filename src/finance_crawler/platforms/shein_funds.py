from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from finance_crawler.auth import auth_login
from finance_crawler.debug_files import write_capture_file
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange


API_BASE_URL = "https://sso.geiwohuo.com"
SUPPLIER_INFO_URL = f"{API_BASE_URL}/sso/public/account/supplier/getSupplierOperateInfo"


def safe_name(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(text or "").strip()).strip("_") or "unknown"


def export_folder_name(task: dict[str, Any]) -> str:
    return safe_name(str(task.get("export_folder") or task.get("task_name") or "导出文件"))


def build_session(cookie_str: str, user_agent: str, origin_url: str | None = None) -> requests.Session:
    session = requests.Session()
    for chunk in (cookie_str or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        session.cookies.set(name.strip(), value.strip(), domain=".geiwohuo.com", path="/")
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": API_BASE_URL,
            "Referer": f"{API_BASE_URL}/",
            "Origin-Url": origin_url or f"{API_BASE_URL}/#/mws/seller/withdraw-details",
            "User-Agent": user_agent or "Mozilla/5.0",
            "x-sso-scene": "gmpsso",
            "gmpsso-language": "CN",
            "x-bbl-route": "",
        }
    )
    return session


def post_json(session: requests.Session, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) != "0":
        raise RuntimeError(f"接口失败 {url}: code={data.get('code')} msg={data.get('msg')}")
    return data


def is_supplier_redirect(data: dict[str, Any]) -> bool:
    return str(data.get("code")) == "20302" or "子系统登录重定向" in str(data.get("msg") or "")


def resolve_supplier_id(session: requests.Session, timeout: int) -> int:
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
    supplier_id = int((data.get("info") or {}).get("supplierId") or 0)
    if supplier_id <= 0:
        raise RuntimeError(f"未能获取 supplierId: {data}")
    return supplier_id


def build_transfer_payload(task: dict[str, Any], supplier_id: int, period: PeriodRange) -> dict[str, Any]:
    api = task.get("api") or {}
    prefix = str(api.get("time_field_prefix") or "transferSuccessTime")
    return {
        "reqSystemCode": str(api.get("req_system_code") or "mws-front"),
        "supplierId": supplier_id,
        "pageNum": int(api.get("page_num") or 1),
        "pageSize": int(api.get("page_size") or 100),
        f"{prefix}Start": period.start_ms,
        f"{prefix}End": period.end_ms,
    }


def pick_extension(response: requests.Response) -> str:
    if response.content.startswith(b"PK\x03\x04"):
        return ".xlsx"
    disposition = response.headers.get("Content-Disposition", "")
    if ".xlsx" in disposition.lower():
        return ".xlsx"
    return ".xls"


def export_shein_funds(
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
    try:
        target_page = str(task.get("target_page") or f"{API_BASE_URL}/#/mws/seller/withdraw-details")
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
        supplier_id = resolve_supplier_id(session, request_timeout)
        payload = build_transfer_payload(task, supplier_id, period)
        api = task.get("api") or {}

        list_payload = post_json(session, str(api["list_url"]), payload, request_timeout)
        count = int((list_payload.get("info") or {}).get("count") or 0)
        payload["pageSize"] = max(count, int(payload.get("pageSize") or 100), 100)
        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        file_stem = download_stem(account_name, period, module_code(task, "withdraw"))

        if count <= 0:
            capture = {
                "captured_at": datetime.now().isoformat(),
                "task_id": task.get("id"),
                "platform": platform,
                "account_name": account_name,
                "supplier_id": supplier_id,
                "period": period.to_dict(),
                "request_payload": payload,
                "list_count": count,
                "export_skipped": True,
                "skip_reason": "列表记录数为 0，不调用导出接口。",
                "auth_context": {
                    "final_url": auth_result.final_url,
                    "user_agent": auth_result.user_agent,
                    "cookie_length": len(auth_result.cookie or ""),
                },
            }
            capture_path = write_capture_file(task, output_root, platform, period, file_stem, capture)
            return TaskResult(
                task_id=str(task.get("id") or "shein_funds"),
                platform=platform,
                account_name=account_name,
                success=True,
                message=f"{platform.upper()} 提现明细无记录，已跳过导出。",
                capture_path=capture_path,
                data={"period": period.to_dict(), "supplier_id": supplier_id, "list_count": count},
            )

        response = session.post(str(api["export_url"]), json=payload, timeout=request_timeout)
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("导出接口返回空文件。")
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower() or response.content.lstrip().startswith(b"{"):
            raise RuntimeError(f"导出接口未返回 Excel: {response.text[:300]}")

        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        output_path = download_dir / f"{file_stem}{pick_extension(response)}"
        output_path.write_bytes(response.content)

        capture = {
            "captured_at": datetime.now().isoformat(),
            "task_id": task.get("id"),
            "platform": platform,
            "account_name": account_name,
            "supplier_id": supplier_id,
            "period": period.to_dict(),
            "request_payload": payload,
            "list_count": count,
            "export_status": response.status_code,
            "export_content_type": response.headers.get("Content-Type", ""),
            "export_content_length": len(response.content),
            "auth_context": {
                "final_url": auth_result.final_url,
                "user_agent": auth_result.user_agent,
                "cookie_length": len(auth_result.cookie or ""),
            },
        }
        capture_path = write_capture_file(task, output_root, platform, period, file_stem, capture)

        return TaskResult(
            task_id=str(task.get("id") or "shein_funds"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"{platform.upper()} 提现明细导出完成，记录数 {count}",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "supplier_id": supplier_id, "list_count": count},
        )
    except Exception as exc:
        return TaskResult(
            task_id=str(task.get("id") or "shein_funds"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )
