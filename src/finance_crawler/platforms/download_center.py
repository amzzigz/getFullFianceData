from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import API_BASE_URL


DOWNLOAD_LIST_URL = f"{API_BASE_URL}/sso/common/fileExport/list"
GET_FILE_URL = f"{API_BASE_URL}/sso/common/fileExport/getFileUrl"
DOWNLOAD_PAGE_URL = f"{API_BASE_URL}/#/download-management/list?last_page=home_download"


def post_download_json(session: requests.Session, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) not in {"0", "200"}:
        raise RuntimeError(f"接口失败 {url}: code={data.get('code')} msg={data.get('msg')}")
    return data


def build_download_list_payload(period: PeriodRange, lookback_days: int = 30) -> dict[str, Any]:
    end_at = datetime.now(period.end.tzinfo).replace(hour=23, minute=59, second=59, microsecond=0)
    start_at = end_at - timedelta(days=max(1, lookback_days - 1))
    return {
        "page": 1,
        "perPage": 50,
        "createTimeStart": start_at.strftime("%Y-%m-%d %H:%M:%S"),
        "createTimeEnd": end_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def download_center_records(session: requests.Session, period: PeriodRange, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = build_download_list_payload(period)
    data = post_download_json(session, DOWNLOAD_LIST_URL, payload, timeout)
    records = data.get("info", {}).get("data") or []
    if not isinstance(records, list):
        raise RuntimeError(f"下载中心列表结构异常: {data}")
    return payload, [row for row in records if isinstance(row, dict)]


def file_id_from_row(row: dict[str, Any]) -> str:
    for key in ("id", "fileId", "exportId", "recordId"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    raise RuntimeError(f"下载记录缺少 id 字段: {row}")


def parse_create_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def find_download_record(
    records: list[dict[str, Any]],
    name_keywords: list[str],
    extension: str = "",
    created_after: datetime | None = None,
) -> dict[str, Any]:
    normalized_ext = extension.strip().lower()
    sorted_records = sorted(
        records,
        key=lambda row: parse_create_time(row.get("createTime")) or datetime.min,
        reverse=True,
    )
    for row in sorted_records:
        name = str(row.get("fileName") or row.get("name") or "")
        row_ext = str(row.get("fileExtension") or row.get("extension") or "").lower()
        if normalized_ext and row_ext != normalized_ext:
            continue
        row_created_at = parse_create_time(row.get("createTime"))
        if created_after and row_created_at and row_created_at < created_after:
            continue
        if all(keyword in name for keyword in name_keywords):
            return row
    raise RuntimeError(f"下载中心没有匹配文件: keywords={name_keywords}, extension={extension}")


def matching_download_records(
    records: list[dict[str, Any]],
    name_keywords: list[str],
    extension: str = "",
    created_after: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized_ext = extension.strip().lower()
    sorted_records = sorted(
        records,
        key=lambda row: parse_create_time(row.get("createTime")) or datetime.min,
        reverse=True,
    )
    matches: list[dict[str, Any]] = []
    for row in sorted_records:
        name = str(row.get("fileName") or row.get("name") or "")
        row_ext = str(row.get("fileExtension") or row.get("extension") or "").lower()
        if normalized_ext and row_ext != normalized_ext:
            continue
        row_created_at = parse_create_time(row.get("createTime"))
        if created_after and row_created_at and row_created_at < created_after:
            continue
        if all(keyword in name for keyword in name_keywords):
            matches.append(row)
    return matches


def wait_download_record(
    session: requests.Session,
    period: PeriodRange,
    timeout: int,
    name_keywords: list[str],
    extension: str = "",
    created_after: datetime | None = None,
    attempts: int = 12,
    interval_seconds: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    last_records: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        last_payload, last_records = download_center_records(session, period, timeout)
        try:
            row = find_download_record(last_records, name_keywords, extension, created_after=created_after)
            return row, last_records, last_payload
        except RuntimeError:
            if attempt >= attempts:
                break
            time.sleep(interval_seconds)
    raise RuntimeError(f"下载中心轮询后仍无匹配文件: keywords={name_keywords}, extension={extension}")


def try_get_file_url(session: requests.Session, file_id: str, timeout: int) -> tuple[str, dict[str, Any]]:
    response = session.get(GET_FILE_URL, params={"id": file_id}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) != "0":
        raise RuntimeError(f"获取下载地址失败: code={data.get('code')} msg={data.get('msg')}")
    info = data.get("info") or {}
    if isinstance(info, dict):
        for key in ("url", "fileUrl", "downloadUrl"):
            value = info.get(key)
            if isinstance(value, str) and value:
                return value, data
    return "", data


def get_file_url(session: requests.Session, file_id: str, timeout: int) -> str:
    last_data: dict[str, Any] = {}
    for attempt in range(1, 25):
        url, data = try_get_file_url(session, file_id, timeout)
        last_data = data
        if url:
            return url
        if attempt < 24:
            time.sleep(5)
    raise RuntimeError(f"未拿到文件下载链接: {last_data}")


def wait_download_file_url(
    session: requests.Session,
    period: PeriodRange,
    timeout: int,
    name_keywords: list[str],
    extension: str = "",
    created_after: datetime | None = None,
    allow_unanchored_fallback: bool = False,
    fallback_created_after: datetime | None = None,
    attempts: int = 36,
    interval_seconds: int = 5,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    last_records: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}
    last_file_response: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        last_payload, last_records = download_center_records(session, period, timeout)
        preferred = matching_download_records(last_records, name_keywords, extension, created_after=created_after)
        candidates = preferred
        if allow_unanchored_fallback:
            fallback = matching_download_records(last_records, name_keywords, extension, created_after=fallback_created_after)
            candidates = preferred + [row for row in fallback if row not in preferred]
        if not candidates:
            last_file_response = {
                "error": f"下载中心没有匹配文件: keywords={name_keywords}, extension={extension}"
            }
        for row in candidates[:8]:
            try:
                file_id = file_id_from_row(row)
                url, data = try_get_file_url(session, file_id, timeout)
                last_file_response = {"candidate": row, "response": data}
                if url:
                    return row, url, last_records, last_payload, data
            except RuntimeError as exc:
                last_file_response = {"error": str(exc), "candidate": row}
        if attempt < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(
        "下载中心轮询后仍未拿到文件链接: "
        f"keywords={name_keywords}, extension={extension}, "
        f"last_file_response={last_file_response}, "
        f"last_records_sample={last_records[:5]}"
    )


def download_file(session: requests.Session, url: str, path: Path, timeout: int) -> requests.Response:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    path.write_bytes(response.content)
    return response
