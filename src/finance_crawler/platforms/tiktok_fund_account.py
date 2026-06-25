from __future__ import annotations

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
from finance_crawler.platforms.tiktok_common import tiktok_download_poll_options
from finance_crawler.platforms.tiktok_withdrawals import (
    CREATE_FILE_TASK_URL,
    EXCHANGE_SESSION_URL,
    PIPO_BASE,
    QUERY_LIST_URL,
    SELLER_WALLET_URL,
    TiktokBrowserContext,
    browser_download_file,
    close_tiktok_browser,
    is_login_expired,
    is_pipo_parameter_error,
    open_cashier_page,
    open_seller_wallet_page,
    parse_cashier_params,
    period_utc_seconds,
    pipo_post_form,
    refresh_cashier_page,
    resolve_cashier_url,
    start_tiktok_browser,
    wait_file_task,
)


def build_fund_account_query_payload(wuid: str, period: PeriodRange) -> dict[str, Any]:
    start_seconds, end_seconds = period_utc_seconds(period)
    return {
        "wuid": wuid,
        "page_size": 10,
        "in_out_type": None,
        "wallet_type": "SELLER",
        "bill_type": None,
        "page_num": 1,
        "bill_currency_code": None,
        "biz_reference_id": None,
        "start_time_stamp": start_seconds,
        "end_time_stamp": end_seconds,
    }


def build_fund_account_payload(wuid: str, period: PeriodRange) -> dict[str, Any]:
    start_seconds, end_seconds = period_utc_seconds(period)
    return {
        "language": "zh",
        "task_type": "TRANSACTION_HISTORY",
        "wuid": wuid,
        "start_time_stamp": start_seconds,
        "end_time_stamp": end_seconds,
        "bill_type": None,
        "in_out_type": None,
        "biz_reference_id": "",
    }


def ensure_pipo_success(payload: dict[str, Any], url: str) -> dict[str, Any]:
    inner = payload.get("_inner_response") or payload
    if str(inner.get("result_code") or "").lower() == "success" or str(inner.get("error_code") or "") == "0":
        return inner
    raise RuntimeError(f"Pipo 业务失败 {url}: {payload}")


def probe_fund_account_list(
    page: Any,
    merchant_id: str,
    wuid: str,
    period: PeriodRange,
    timeout: int,
) -> dict[str, Any]:
    response = pipo_post_form(page, QUERY_LIST_URL, merchant_id, build_fund_account_query_payload(wuid, period), timeout)
    return ensure_pipo_success(response, QUERY_LIST_URL)


def refresh_session_if_possible(
    ctx: TiktokBrowserContext,
    merchant_id: str,
    wuid: str,
    detect_seconds: int,
    timeout: int,
    debug: dict[str, Any],
) -> tuple[str, str]:
    exchange_payload = pipo_post_form(ctx.page, EXCHANGE_SESSION_URL, merchant_id, {"set_cookie": True}, timeout)
    debug["exchange_response"] = exchange_payload
    try:
        ensure_pipo_success(exchange_payload, EXCHANGE_SESSION_URL)
        return merchant_id, wuid
    except RuntimeError as exc:
        if not is_login_expired(exchange_payload):
            raise
        print("[TK] Pipo session 过期，刷新钱包页后再试一次", flush=True)
        cashier_url, params = refresh_cashier_page(ctx, detect_seconds)
        debug.update({"refreshed_cashier_url": cashier_url, "refresh_reason": str(exc)})
        merchant_id = str(params["merchant_id"])
        wuid = str(params["wuid"])
        debug.update({"merchant_id": merchant_id, "wuid": wuid})
        exchange_payload = pipo_post_form(ctx.page, EXCHANGE_SESSION_URL, merchant_id, {"set_cookie": True}, timeout)
        debug["exchange_response_after_refresh"] = exchange_payload
        try:
            ensure_pipo_success(exchange_payload, EXCHANGE_SESSION_URL)
        except RuntimeError:
            if not is_login_expired(exchange_payload):
                raise
            print("[TK] 刷新 session 仍过期，继续尝试创建导出任务", flush=True)
        return merchant_id, wuid


def create_fund_file_task(
    ctx: TiktokBrowserContext,
    merchant_id: str,
    wuid: str,
    period: PeriodRange,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    create_payload = build_fund_account_payload(wuid, period)
    create_response = pipo_post_form(ctx.page, CREATE_FILE_TASK_URL, merchant_id, create_payload, timeout)
    create_inner = ensure_pipo_success(create_response, CREATE_FILE_TASK_URL)
    return create_payload, create_inner


def export_tiktok_fund_account_with_ctx(
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
        print(f"[TK] {account_name} 打开钱包页", flush=True)
        open_seller_wallet_page(ctx)
        time.sleep(3)
        detect_seconds = int(task.get("cashier_detect_seconds") or 45)
        print(f"[TK] {account_name} 查找 Pipo 钱包链接", flush=True)
        cashier_url = resolve_cashier_url(ctx, detect_seconds, request_timeout)
        params = parse_cashier_params(cashier_url)
        print(f"[TK] {account_name} 进入 Pipo 钱包页", flush=True)
        open_cashier_page(ctx, cashier_url)
        debug["diagnostic_recorder_installed"] = install_browser_request_recorder(ctx.page)

        merchant_id = str(params["merchant_id"])
        wuid = str(params["wuid"])
        start_seconds, end_seconds = period_utc_seconds(period)
        debug.update(
            {
                "cashier_url": cashier_url,
                "merchant_id": merchant_id,
                "wuid": wuid,
                "start_time_stamp": start_seconds,
                "end_time_stamp": end_seconds,
            }
        )

        print(f"[TK] {account_name} 校验资金账户列表", flush=True)
        try:
            debug["list_response"] = probe_fund_account_list(ctx.page, merchant_id, wuid, period, request_timeout)
        except RuntimeError as exc:
            if not is_pipo_parameter_error(str(exc)):
                raise
            debug["list_probe_skipped"] = {"reason": str(exc)}
            print(f"[TK] {account_name} 资金账户列表校验参数不兼容，跳过校验继续导出", flush=True)

        print(f"[TK] {account_name} 创建资金账户导出任务", flush=True)
        try:
            create_payload, create_inner = create_fund_file_task(ctx, merchant_id, wuid, period, request_timeout)
        except RuntimeError as exc:
            if not (is_pipo_parameter_error(str(exc)) or "LOGIN_STATUS_EXPIRED" in str(exc) or "Login status expired" in str(exc)):
                raise
            print(f"[TK] {account_name} 创建导出任务失败，刷新钱包入口后重试一次", flush=True)
            open_seller_wallet_page(ctx)
            time.sleep(3)
            cashier_url = resolve_cashier_url(ctx, detect_seconds, request_timeout)
            params = parse_cashier_params(cashier_url)
            open_cashier_page(ctx, cashier_url)
            merchant_id = str(params["merchant_id"])
            wuid = str(params["wuid"])
            debug.update({"merchant_id": merchant_id, "wuid": wuid, "retry_cashier_url": cashier_url})
            create_payload, create_inner = create_fund_file_task(ctx, merchant_id, wuid, period, request_timeout)
        task_id = str(create_inner.get("task_id") or "")
        if not task_id:
            raise RuntimeError(f"TK 资金账户导出未返回 task_id: {create_inner}")

        print(f"[TK] {account_name} 等待导出文件生成 task_id={task_id}", flush=True)
        attempts, interval = tiktok_download_poll_options(task)
        download_url, query_inner = wait_file_task(
            ctx.page,
            merchant_id,
            task_id,
            attempts,
            interval,
            request_timeout,
            account_name,
            "资金账户文件",
        )
        full_download_url = urljoin(PIPO_BASE, download_url)

        period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
        download_dir = output_root / "downloads" / platform / period.period_type / period_label / export_folder_name(task)
        download_dir.mkdir(parents=True, exist_ok=True)
        file_stem = download_stem(account_name, period, module_code(task, "TK资金账户"))
        output_path = download_dir / f"{file_stem}.csv"
        print(f"[TK] {account_name} 下载资金账户 CSV", flush=True)
        download_bytes = browser_download_file(ctx.page, full_download_url, output_path, request_timeout)

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
                "create_payload": create_payload,
                "create_response": create_inner,
                "query_response": query_inner,
                "download_url": full_download_url,
                "output_path": str(output_path),
                "download_bytes": download_bytes,
                **({"browser_diagnostics": collect_browser_diagnostics(ctx.page)} if diagnostic_enabled(task) else {}),
            },
        )
        return TaskResult(
            task_id=str(task.get("id") or "tiktok_fund_account"),
            platform=platform,
            account_name=account_name,
            success=True,
            message="TK 资金账户导出完成，文件数 1",
            output_path=str(output_path),
            capture_path=capture_path,
            data={"period": period.to_dict(), "output": str(output_path), "download_bytes": download_bytes},
        )
    except Exception as exc:
        try:
            file_stem = download_stem(account_name, period, module_code(task, "TK资金账户"))
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
            task_id=str(task.get("id") or "tiktok_fund_account"),
            platform=platform,
            account_name=account_name,
            success=False,
            message=str(exc),
            capture_path=capture_path,
        )


def export_tiktok_fund_account(
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
        ctx = start_tiktok_browser(account_name, auth_path, login_timeout)
        return export_tiktok_fund_account_with_ctx(
            task=task,
            account_name=account_name,
            period=period,
            ctx=ctx,
            output_root=output_root,
            request_timeout=request_timeout,
        )
    finally:
        close_tiktok_browser(ctx)
