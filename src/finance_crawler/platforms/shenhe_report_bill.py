from __future__ import annotations

import json
import time
import uuid
from base64 import b64decode
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from finance_crawler.auth import (
    get_initial_browser_tab,
    is_browser_tab_connection_error,
    load_ziniu_helper,
    recover_browser_tab,
    ziniu_auth_slot,
)
from finance_crawler.debug_files import write_capture_file
from finance_crawler.filenames import download_stem, module_code
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.shein_funds import export_folder_name, safe_name


BASE_URL = "https://www.shenhe888.com"
TARGET_URL = f"{BASE_URL}/scp-front.html#/finance-management/report-bill-management"
USER_INFO_URL = f"{BASE_URL}/portal/front/supplier/portal/get-user-info"
LIST_URL = f"{BASE_URL}/scp/front/report-bill/list"
EXPORT_CHECK_BILL_URL = f"{BASE_URL}/scp/front/report-bill/export-check-bill-detail"
EXPORT_PLUS_MINUS_URL = f"{BASE_URL}/scp/front/report-bill/export-plus-minus-bill"


@dataclass
class ShenhePageRef:
    browser: Any
    page: Any
    reconnect_attempts: int = 0


def js_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def ensure_success(data: dict[str, Any], url: str) -> dict[str, Any]:
    if str(data.get("code")) in {"0", "200"}:
        return data
    raise RuntimeError(f"申合接口失败 {url}: code={data.get('code')} msg={data.get('msg')}")


def start_logged_in_page(
    account_name: str,
    auth_path: Path,
    timeout_seconds: int,
    auth_slot_held: bool = False,
) -> tuple[Any, Any, Any, str]:
    if not auth_slot_held:
        with ziniu_auth_slot():
            return start_logged_in_page(account_name, auth_path, timeout_seconds, auth_slot_held=True)

    helper = load_ziniu_helper(auth_path)
    ok, err = helper.ensure_client_online()
    if not ok:
        raise RuntimeError(f"紫鸟客户端不可用: {err}")
    info, info_err = helper.get_shop_info(account_name)
    if not info:
        raise RuntimeError(f"紫鸟账号未找到: {info_err}")

    payload = helper.build_start_browser_payload(info)
    response = None
    for attempt in range(1, 4):
        response = helper.send_http(payload)
        if response and str(response.get("statusCode")) == "0":
            break
        helper.ensure_client_online()
        time.sleep(2 * attempt)
    if not response or str(response.get("statusCode")) != "0":
        raise RuntimeError(f"startBrowser failed: {response}")

    browser_oauth = str(response.get("browserOauth") or info.get("browserOauth") or "")
    debug_port = int(response.get("debuggingPort") or 0)
    browser = page = None
    try:
        from DrissionPage import Chromium, ChromiumOptions

        browser = Chromium(ChromiumOptions().set_local_port(debug_port).existing_only())
        page = get_initial_browser_tab(browser, debug_port)

        end_at = time.time() + max(10, timeout_seconds)
        last_url = ""
        reconnect_attempts = 0
        target_requested = False
        while time.time() < end_at:
            try:
                last_url = str(getattr(page, "url", "") or "")
                lower_url = last_url.lower()
                if "shenhe888.com" not in lower_url and not target_requested:
                    page.get(TARGET_URL)
                    target_requested = True
                    last_url = str(getattr(page, "url", "") or "")
                    lower_url = last_url.lower()
                if "shenhe888.com" in lower_url and "login" not in lower_url:
                    return helper, browser, page, browser_oauth
            except Exception as exc:
                if is_browser_tab_connection_error(exc) and reconnect_attempts < 3:
                    reconnect_attempts += 1
                    try:
                        page = recover_browser_tab(browser, page, exc, "shenhe888.com")
                        target_requested = False
                        continue
                    except Exception as reconnect_exc:
                        if reconnect_attempts < 3:
                            continue
                        raise reconnect_exc
                raise
            time.sleep(2)
        raise RuntimeError(f"申合平台未检测到已登录状态，请先手动登录后再运行。当前URL={last_url}")
    except Exception:
        close_browser(helper, browser, page, browser_oauth)
        raise


def close_browser(helper: Any, browser: Any, page: Any, browser_oauth: str) -> None:
    try:
        if browser_oauth:
            helper.send_http({"action": "stopBrowser", "requestId": str(uuid.uuid4()), "browserOauth": browser_oauth})
    except Exception:
        pass
    try:
        if page:
            page.quit()
    except Exception:
        pass


def browser_fetch(page: Any, url: str, method: str, payload: dict[str, Any] | None, timeout: int) -> dict[str, Any]:
    script = f"""
        return (async () => {{
            const portalToken = localStorage.getItem('portal_token') || '';
            const options = {{
                method: {js_json(method.upper())},
                credentials: 'include',
                headers: {{
                    'accept': 'application/json',
                    'content-type': 'application/json;charset=UTF-8',
                    'system': 'scp',
                    'system-language': 'CN'
                }}
            }};
            if (portalToken) {{
                options.headers['portal_token'] = portalToken;
            }}
            const payload = {js_json(payload or {})};
            if (options.method !== 'GET') {{
                options.body = JSON.stringify(payload);
            }}
            const r = await fetch({js_json(url)}, options);
            const text = await r.text();
            let data = null;
            try {{ data = text ? JSON.parse(text) : {{}}; }} catch (e) {{ data = {{rawText: text}}; }}
            return {{ok: r.ok, status: r.status, url: r.url, data}};
        }})();
    """
    result = None
    last_error = ""
    for attempt in range(1, 4):
        try:
            active_page = page.page if isinstance(page, ShenhePageRef) else page
            result = active_page.run_js(script)
            break
        except Exception as exc:
            last_error = str(exc)
            if isinstance(page, ShenhePageRef) and is_browser_tab_connection_error(exc):
                page.reconnect_attempts += 1
                if page.reconnect_attempts <= 3:
                    try:
                        page.page = recover_browser_tab(page.browser, page.page, exc, "shenhe888.com")
                        continue
                    except Exception:
                        if page.reconnect_attempts < 3:
                            continue
                        raise
            if attempt >= 3:
                raise
            time.sleep(2)
    if not isinstance(result, dict):
        raise RuntimeError(f"浏览器 fetch 返回异常: {result!r}, last_error={last_error}")
    if not result.get("ok"):
        raise RuntimeError(f"浏览器 fetch 失败 {url}: status={result.get('status')} data={result.get('data')}")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"接口响应不是 JSON 对象 {url}: {data!r}")
    return data


def browser_post_json(page: Any, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    return ensure_success(browser_fetch(page, url, "POST", payload, timeout), url)


def browser_get_json(page: Any, url: str, timeout: int) -> dict[str, Any]:
    return browser_fetch(page, url, "GET", None, timeout)


def list_report_bills(page: Any, period: PeriodRange, timeout: int, page_size: int = 50) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    page_num = 1
    last_data: dict[str, Any] = {}
    while True:
        payload = {
            "perPage": page_size,
            "page": page_num,
            "reportBillStatus": 20,
            "createTime": {
                "left": period.start.strftime("%Y-%m-%d %H:%M:%S"),
                "right": period.end.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        payloads.append(payload)
        data = browser_post_json(page, LIST_URL, payload, timeout)
        last_data = data
        list_payload = ((data.get("info") or {}).get("list") or {})
        rows = list_payload.get("data") or []
        if not isinstance(rows, list):
            raise RuntimeError(f"报账单列表结构异常: {data}")
        records.extend([row for row in rows if isinstance(row, dict)])
        meta = list_payload.get("meta") or {}
        total = int(meta.get("count") or len(records))
        if len(records) >= total or not rows:
            break
        page_num += 1
    return records, last_data, payloads


def export_report_file(page: Any, url: str, report_no: str, timeout: int) -> tuple[str, dict[str, Any]]:
    data = browser_post_json(page, url, {"reportNo": report_no}, timeout)
    file_url = str(data.get("info") or "").strip()
    if not file_url:
        raise RuntimeError(f"导出接口未返回文件URL: reportNo={report_no}, response={data}")
    return file_url, data


def output_extension(file_url: str) -> str:
    suffix = Path(urlparse(file_url).path).suffix
    return suffix if suffix else ".xlsx"


def download_url(file_url: str, output_path: Path, timeout: int) -> requests.Response:
    response = requests.get(file_url, timeout=timeout)
    response.raise_for_status()
    content = response.content
    if content.lstrip().lower().startswith(b"<!doctype html") or content.lstrip().lower().startswith(b"<html"):
        raise RuntimeError(f"下载得到 HTML，不是 Excel: url={file_url}")
    output_path.write_bytes(content)
    return response


def export_shenhe_report_bill(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    with ziniu_auth_slot():
        return _export_shenhe_report_bill_unlocked(
            task,
            account_name,
            period,
            auth_path,
            output_root,
            request_timeout,
            login_timeout,
        )


def _export_shenhe_report_bill_unlocked(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    auth_path: Path,
    output_root: Path,
    request_timeout: int = 60,
    login_timeout: int = 30,
) -> TaskResult:
    platform = str(task.get("platform") or "shein")
    helper = browser = page = None
    browser_oauth = ""
    capture_path = ""
    debug: dict[str, Any] = {"period": period.to_dict()}
    outputs: list[str] = []
    report_results: list[dict[str, Any]] = []
    try:
        helper, browser, page, browser_oauth = start_logged_in_page(
            account_name,
            auth_path,
            login_timeout,
            auth_slot_held=True,
        )
        page_ref = ShenhePageRef(browser, page)
        records, list_response, list_payloads = list_report_bills(
            page_ref,
            period,
            request_timeout,
            int(task.get("page_size") or 50),
        )
        debug.update(
            {
                "target_page": TARGET_URL,
                "list_url": LIST_URL,
                "list_payloads": list_payloads,
                "list_count": len(records),
                "list_sample": records[:3],
            }
        )

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)

        for record in records:
            report_no = str(record.get("reportBillNo") or "").strip()
            if not report_no:
                continue
            per_report: dict[str, Any] = {"reportBillNo": report_no, "createTime": record.get("createTime")}
            for kind, export_url in (
                ("报账单", EXPORT_CHECK_BILL_URL),
                ("补扣款详情", EXPORT_PLUS_MINUS_URL),
            ):
                file_url, export_response = export_report_file(page_ref, export_url, report_no, request_timeout)
                kind_code = "明细" if kind == "报账单" else "补扣款"
                file_stem = download_stem(account_name, period, module_code(task, "rbill"), report_no, kind_code)
                output_path = download_dir / f"{file_stem}{output_extension(file_url)}"
                response = download_url(file_url, output_path, request_timeout)
                outputs.append(str(output_path))
                per_report[kind] = {
                    "export_url": export_url,
                    "export_response": export_response,
                    "file_url": file_url,
                    "output_path": str(output_path),
                    "status_code": response.status_code,
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_length": len(response.content),
                }
            report_results.append(per_report)

        file_stem = download_stem(account_name, period, module_code(task, "rbill"))
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
                **debug,
                "list_response": list_response,
                "report_results": report_results,
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "shenhe_report_bill"),
            platform=platform,
            account_name=account_name,
            success=True,
            message=f"申合报账单导出完成，报账单数 {len(report_results)}，文件数 {len(outputs)}",
            output_path="; ".join(outputs[:3]) + (" ..." if len(outputs) > 3 else ""),
            capture_path=capture_path,
            data={"period": period.to_dict(), "report_count": len(report_results), "outputs": outputs},
        )
    except Exception as exc:
        try:
            period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
            file_stem = download_stem(account_name, period, module_code(task, "rbill"))
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
                    "report_results": report_results,
                },
                failed=True,
            )
        except Exception:
            pass
        return TaskResult(
            task_id=str(task.get("id") or "shenhe_report_bill"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )
    finally:
        active_page = page_ref.page if "page_ref" in locals() else page
        close_browser(helper, browser, active_page, browser_oauth)
