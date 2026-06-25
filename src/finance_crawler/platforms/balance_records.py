from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from finance_crawler.auth import auth_login
from finance_crawler.debug_files import write_capture_file
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import (
    API_BASE_URL,
    build_session,
    export_folder_name,
    pick_extension,
    post_json,
    resolve_supplier_id,
    safe_name,
)


CURRENCY_URL = f"{API_BASE_URL}/mws/mwms/sso/metadata/query/supplier/currency"
INACTIVE_POP_BALANCE_ACCOUNTS = {"A21POP", "A23POP"}


def resolve_currency(session: requests.Session, timeout: int) -> str:
    data = post_json(session, CURRENCY_URL, {"reqSystemCode": "mws-front"}, timeout)
    info = data.get("info") or []
    if isinstance(info, list) and info:
        return str(info[0])
    if isinstance(info, str) and info:
        return info
    raise RuntimeError(f"未能获取币种: {data}")


def build_balance_payload(task: dict[str, Any], supplier_id: int, period: PeriodRange, currency: str) -> dict[str, Any]:
    api = task.get("api") or {}
    return {
        "reqSystemCode": str(api.get("req_system_code") or "mws-front"),
        "supplierId": supplier_id,
        "pageNum": int(api.get("page_num") or 1),
        "pageSize": int(api.get("page_size") or 100),
        "createTimeStart": period.start_ms,
        "createTimeEnd": period.end_ms,
        "currency": currency,
    }


def decode_export_response(response: requests.Response) -> tuple[bytes, str]:
    content_type = response.headers.get("Content-Type", "")
    if response.content.startswith(b"PK\x03\x04") or "excel" in content_type.lower():
        return response.content, pick_extension(response)

    if response.content.lstrip().startswith(b"{"):
        data = response.json()
        if str(data.get("code")) != "0":
            raise RuntimeError(f"导出接口失败: code={data.get('code')} msg={data.get('msg')}")
        info = data.get("info") or {}
        blob = info.get("blob") if isinstance(info, dict) else None
        filename = str(info.get("filename") or "") if isinstance(info, dict) else ""
        if isinstance(blob, str) and blob.startswith("data:"):
            encoded = blob.split(",", 1)[1]
            ext = ".xlsx" if ".xlsx" in filename.lower() else ".xls"
            return base64.b64decode(encoded), ext
        if isinstance(blob, str):
            try:
                raw = base64.b64decode(blob)
                ext = ".xlsx" if raw.startswith(b"PK\x03\x04") else ".xls"
                return raw, ext
            except Exception as exc:
                raise RuntimeError(f"无法解析导出 blob: {exc}") from exc
        raise RuntimeError(f"导出接口 JSON 缺少 blob: {data}")

    raise RuntimeError(f"导出接口返回未知内容: content_type={content_type}, length={len(response.content)}")


def export_balance_records(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    platform = str(task.get("platform") or "pop")
    capture_path = ""
    try:
        target_page = str(task.get("target_page") or f"{API_BASE_URL}/#/mws/seller/balance-changes")
        auth_result = task.get("_auth_result") or auth_login(
            account_name,
            auth_path,
            fallback_timeout_seconds=login_timeout,
            target_url=target_page,
        )
        if not auth_result.success:
            raise RuntimeError(f"紫鸟鉴权失败: {auth_result.message}")

        session = build_session(
            auth_result.cookie,
            auth_result.user_agent,
            target_page,
        )
        supplier_id = resolve_supplier_id(session, request_timeout)
        currency = resolve_currency(session, request_timeout)
        payload = build_balance_payload(task, supplier_id, period, currency)
        api = task.get("api") or {}

        list_payload = post_json(session, str(api["list_url"]), payload, request_timeout)
        count = int((list_payload.get("info") or {}).get("count") or 0)
        payload["pageSize"] = max(count, int(payload.get("pageSize") or 100), 100)
        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        file_stem = download_stem(account_name, period, module_code(task, "balance"))

        if count <= 0:
            capture = {
                "captured_at": datetime.now().isoformat(),
                "task_id": task.get("id"),
                "platform": platform,
                "account_name": account_name,
                "supplier_id": supplier_id,
                "currency": currency,
                "period": period.to_dict(),
                "request_payload": payload,
                "list_count": count,
                "export_skipped": True,
                "skip_reason": "列表记录数为 0，不调用导出接口。",
            }
            capture_path = write_capture_file(task, output_root, platform, period, file_stem, capture)
            return TaskResult(
                task_id=str(task.get("id") or ""),
                platform=platform,
                account_name=account_name,
                success=True,
                message=f"{platform.upper()} 资金流水无记录，已跳过导出。",
                capture_path=capture_path,
                data={"period": period.to_dict(), "supplier_id": supplier_id, "currency": currency, "list_count": count},
            )

        response = session.post(str(api["export_url"]), json=payload, timeout=request_timeout)
        response.raise_for_status()
        raw_file, ext = decode_export_response(response)
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        output_path = download_dir / f"{file_stem}{ext}"
        output_path.write_bytes(raw_file)

        capture = {
            "captured_at": datetime.now().isoformat(),
            "task_id": task.get("id"),
            "platform": platform,
            "account_name": account_name,
            "supplier_id": supplier_id,
            "currency": currency,
            "period": period.to_dict(),
            "request_payload": payload,
            "list_count": count,
            "export_status": response.status_code,
            "export_content_type": response.headers.get("Content-Type", ""),
            "export_content_length": len(raw_file),
        }
        capture_path = write_capture_file(task, output_root, platform, period, file_stem, capture)
        return TaskResult(
            task_id=str(task.get("id") or ""),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"{platform.upper()} 资金流水导出完成，记录数 {count}",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "supplier_id": supplier_id, "currency": currency, "list_count": count},
        )
    except Exception as exc:
        message = str(exc)
        inactive_pop_balance = (
            platform == "pop"
            and account_name.strip().upper() in INACTIVE_POP_BALANCE_ACCOUNTS
            and "未能获取币种" in message
            and "'info': []" in message
        )
        if inactive_pop_balance:
            return TaskResult(
                task_id=str(task.get("id") or ""),
                platform=platform,
                account_name=account_name,
                success=True,
                message=f"{account_name} POP 资金流水模块未启用，无币种数据，已跳过。",
                capture_path=capture_path,
                data={"period": period.to_dict(), "no_data": True, "reason": "currency_info_empty"},
                status="no_data",
            )
        return TaskResult(
            task_id=str(task.get("id") or ""),
            platform=platform,
            account_name=account_name,
            success=False,
            message=message,
            capture_path=capture_path,
        )
