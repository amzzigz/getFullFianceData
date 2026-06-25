from __future__ import annotations

import hashlib
import json
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
from finance_crawler.platforms.shein_funds import export_folder_name


ACS_BASE = "https://seller-acs.aliexpress.com"
APP_KEY = "30267743"


def build_session(cookie_str: str, user_agent: str) -> requests.Session:
    session = requests.Session()
    for chunk in (cookie_str or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        for domain in (".aliexpress.com", ".seller-acs.aliexpress.com", ".csp.aliexpress.com"):
            session.cookies.set(name.strip(), value.strip(), domain=domain, path="/")
    session.headers.update(
        {
            "Accept": "application/json",
            "Origin": "https://csp.aliexpress.com",
            "Referer": "https://csp.aliexpress.com/",
            "User-Agent": user_agent or "Mozilla/5.0",
            "x-referer": "csp.aliexpress.com/m_apps/newhome/choice",
        }
    )
    return session


def token_from_session(session: requests.Session) -> str:
    for cookie in session.cookies:
        if cookie.name == "_m_h5_tk" and cookie.value:
            return cookie.value.split("_", 1)[0]
    return ""


def mtop_sign(token: str, timestamp_ms: str, data_text: str) -> str:
    raw = f"{token}&{timestamp_ms}&{APP_KEY}&{data_text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def compact_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def mtop_get(
    session: requests.Session,
    api: str,
    data: dict[str, Any],
    channel_id: str,
    timeout: int,
    app_key: str = APP_KEY,
) -> dict[str, Any]:
    data_text = compact_json(data)
    timestamp_ms = str(int(time.time() * 1000))
    token = token_from_session(session)
    sign = mtop_sign(token, timestamp_ms, data_text)
    params = {
        "jsv": "2.7.2",
        "appKey": app_key,
        "t": timestamp_ms,
        "sign": sign,
        "v": "1.0",
        "timeout": "30000",
        "H5Request": "true",
        "url": api,
        "__channel-id__": channel_id,
        "api": api,
        "type": "originaljson",
        "dataType": "json",
        "valueType": "original",
        "x-i18n-regionID": "AE",
        "data": data_text,
    }
    response = session.get(
        f"{ACS_BASE}/h5/{api.lower()}/1.0/",
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    ret = payload.get("ret") or []
    if ret and any("TOKEN" in str(item).upper() or "ILLEGAL_ACCESS" in str(item).upper() for item in ret):
        # Alibaba H5 APIs may refresh _m_h5_tk on the first signed request.
        timestamp_ms = str(int(time.time() * 1000))
        token = token_from_session(session)
        params["t"] = timestamp_ms
        params["sign"] = mtop_sign(token, timestamp_ms, data_text)
        response = session.get(
            f"{ACS_BASE}/h5/{api.lower()}/1.0/",
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        ret = payload.get("ret") or []
    if not ret or not any("SUCCESS" in str(item).upper() for item in ret):
        raise RuntimeError(f"速卖通 MTop 失败 {api}: ret={ret} response={payload}")
    data_payload = payload.get("data") or {}
    if isinstance(data_payload, dict):
        code = str(data_payload.get("code") or data_payload.get("errorCode") or "200")
        if code not in {"0", "200"}:
            raise RuntimeError(f"速卖通接口失败 {api}: code={code} response={payload}")
    return payload


def export_settled(session: requests.Session, period: PeriodRange, channel_id: str, seller_id: str, timeout: int) -> dict[str, Any]:
    payload = {
        "channelId": channel_id,
        "sellerId": seller_id,
        "startTime": period.start.strftime("%Y-%m-%d"),
        "endTime": period.end.strftime("%Y-%m-%d"),
        "exportType": "SETTLED",
        "currency": "CNY",
        "sorted": True,
        "pageSize": 10,
        "current": 1,
        "language": "zh_CN",
        "oneStopVersion": "NEW",
    }
    return mtop_get(session, "mtop.ae.merchant.fund.exportOrderFundDetail", payload, channel_id, timeout)


def export_receipts(session: requests.Session, period: PeriodRange, channel_id: str, timeout: int) -> dict[str, Any]:
    payload = {
        "channelId": channel_id,
        "startTime": period.start.strftime("%Y/%m/%d"),
        "endTime": period.end.strftime("%Y/%m/%d"),
        "exportType": "CHOICE_SELF_RECEIPTS",
        "bizScene": "CHOICE_SELF",
    }
    return mtop_get(session, "mtop.ae.merchant.fund.exportFundDetail", payload, channel_id, timeout)


def records_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    inner = data.get("data") if isinstance(data, dict) else {}
    if isinstance(inner, dict):
        records = inner.get("fileRecords") or []
    else:
        records = []
    return [row for row in records if isinstance(row, dict)]


def query_settled_records(session: requests.Session, channel_id: str, seller_id: str, timeout: int) -> dict[str, Any]:
    payload = {
        "channelId": channel_id,
        "exportType": "SETTLED",
        "pageSize": 10,
        "current": 1,
        "sellerId": seller_id,
        "language": "zh_CN",
    }
    return mtop_get(session, "mtop.ae.merchant.fund.queryFileExportRecord", payload, channel_id, timeout)


def query_receipts_records(session: requests.Session, channel_id: str, timeout: int) -> dict[str, Any]:
    payload = {
        "channelId": channel_id,
        "exportType": "CHOICE_SELF_RECEIPTS",
        "pageSize": 10,
        "current": 1,
        "bizScene": "CHOICE_SELF",
    }
    return mtop_get(session, "mtop.ae.merchant.fund.queryDownloadFileDetail", payload, channel_id, timeout)


def parse_export_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def find_record(records: list[dict[str, Any]], filename_part: str, created_after: datetime | None) -> dict[str, Any] | None:
    for row in sorted(records, key=lambda item: parse_export_time(item.get("exportTime")) or datetime.min, reverse=True):
        name = str(row.get("fileName") or "")
        if filename_part not in name:
            continue
        exported_at = parse_export_time(row.get("exportTime"))
        if created_after and exported_at and exported_at < created_after:
            continue
        if row.get("downUrl"):
            return row
    return None


def wait_record(
    session: requests.Session,
    period: PeriodRange,
    channel_id: str,
    seller_id: str,
    kind: str,
    created_after: datetime,
    timeout: int,
    attempts: int,
    interval_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if kind == "settled":
        expected = f"settled_{period.start:%Y-%m-%d}-{period.end:%Y-%m-%d}.xlsx"
        query = lambda: query_settled_records(session, channel_id, seller_id, timeout)
    else:
        expected = f"one_stop_revenue_and_expenditure_{period.start:%Y%m%d}-{period.end:%Y%m%d}.xlsx"
        query = lambda: query_receipts_records(session, channel_id, timeout)
    last_payload: dict[str, Any] = {}
    last_records: list[dict[str, Any]] = []
    for attempt in range(1, max(1, attempts) + 1):
        last_payload = query()
        last_records = records_from_response(last_payload)
        row = find_record(last_records, expected, created_after)
        if row:
            return row, last_payload
        if attempt < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(f"速卖通下载记录未生成: kind={kind}, expected={expected}, sample={last_records[:5]}")


def download_file(
    session: requests.Session,
    url: str,
    output_path: Path,
    timeout: int,
    attempts: int = 8,
    interval_seconds: int = 5,
) -> requests.Response:
    headers = {
        "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*",
        "Referer": "https://csp.aliexpress.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
    }
    last_response: requests.Response | None = None
    for attempt in range(1, max(1, attempts) + 1):
        response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        last_response = response
        if response.status_code == 404 and attempt < attempts:
            # fbprivacy links can appear in export records before the object is readable.
            time.sleep(interval_seconds)
            continue
        response.raise_for_status()
        if response.content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            if attempt < attempts:
                time.sleep(interval_seconds)
                continue
            raise RuntimeError(f"下载得到 HTML，不是 Excel: {url}")
        output_path.write_bytes(response.content)
        return response
    status = last_response.status_code if last_response is not None else "no response"
    body = ""
    if last_response is not None:
        body = last_response.text[:200].replace("\r", " ").replace("\n", " ")
    raise RuntimeError(f"速卖通文件下载失败: status={status}, url={url}, body={body}")


def export_aliexpress_finance(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    platform = "aliexpress"
    capture_path = ""
    outputs: list[str] = []
    debug: dict[str, Any] = {"period": period.to_dict()}
    try:
        auth_result = auth_login(account_name, auth_path, fallback_timeout_seconds=login_timeout)
        if not auth_result.success:
            raise RuntimeError(f"紫鸟鉴权失败: {auth_result.message}")
        if not auth_result.cookie:
            raise RuntimeError("紫鸟鉴权成功，但未返回 cookie。")
        session = build_session(auth_result.cookie, auth_result.user_agent)
        channel_id = str(task.get("channel_id") or "")
        seller_id = str(task.get("seller_id") or "")
        if not channel_id or not seller_id:
            raise RuntimeError("速卖通任务缺少 channel_id 或 seller_id。")

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []
        for kind, title, export_func in (
            ("settled", "已结算金额", lambda: export_settled(session, period, channel_id, seller_id, request_timeout)),
            ("receipts", "其他收支", lambda: export_receipts(session, period, channel_id, request_timeout)),
        ):
            started_at = datetime.now() - timedelta(minutes=3)
            export_response = export_func()
            row, query_response = wait_record(
                session,
                period,
                channel_id,
                seller_id,
                kind,
                started_at,
                request_timeout,
                int(task.get("download_attempts") or 18),
                int(task.get("download_interval_seconds") or 5),
            )
            output_path = download_dir / f"{download_stem(account_name, period, module_code(task, '速卖通资金'), title)}.xlsx"
            response = download_file(
                session,
                str(row.get("downUrl")),
                output_path,
                request_timeout,
                attempts=int(task.get("file_download_attempts") or 8),
                interval_seconds=int(task.get("file_download_interval_seconds") or 5),
            )
            outputs.append(str(output_path))
            results.append(
                {
                    "kind": kind,
                    "title": title,
                    "export_response": export_response,
                    "selected_row": row,
                    "query_response": query_response,
                    "output_path": str(output_path),
                    "status_code": response.status_code,
                    "content_length": len(response.content),
                    "content_type": response.headers.get("Content-Type", ""),
                }
            )

        file_stem = download_stem(account_name, period, module_code(task, "速卖通资金"))
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
                "channel_id": channel_id,
                "seller_id": seller_id,
                **debug,
                "results": results,
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "aliexpress_finance"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"速卖通资金导出完成，文件数 {len(outputs)}",
            output_path="; ".join(outputs),
            capture_path=capture_path,
            data={"period": period.to_dict(), "outputs": outputs},
        )
    except Exception as exc:
        return TaskResult(
            task_id=str(task.get("id") or "aliexpress_finance"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )
