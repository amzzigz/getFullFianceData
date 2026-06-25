from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_TEMU_JOB_LOCK = threading.Lock()

from finance_crawler.auth import (
    AuthResult,
    configure_ziniu_auth_concurrency,
    configure_ziniu_client_environment,
    shein_shared_cookie_login,
)
from finance_crawler.config import load_app_config
from finance_crawler.models import TaskResult
from finance_crawler.periods import PeriodRange, resolve_previous_period
from finance_crawler.platforms.aliexpress_finance import export_aliexpress_finance
from finance_crawler.platforms.balance_records import export_balance_records
from finance_crawler.platforms.merchant_billing import export_merchant_billing
from finance_crawler.platforms.platform_fees import export_platform_fees
from finance_crawler.platforms.platform_income import export_platform_income
from finance_crawler.platforms.pop_sales_data import export_pop_sales_data
from finance_crawler.platforms.sales_ledger import export_sales_ledger
from finance_crawler.platforms.shein_funds import export_shein_funds
from finance_crawler.platforms.shenhe_report_bill import export_shenhe_report_bill
from finance_crawler.platforms.temu_fund_details import export_temu_fund_details
from finance_crawler.platforms.tiktok_fee_center import export_tiktok_fee_center, export_tiktok_fee_center_with_ctx
from finance_crawler.platforms.tiktok_email_income import export_tiktok_email_income
from finance_crawler.platforms.tiktok_fund_account import export_tiktok_fund_account, export_tiktok_fund_account_with_ctx
from finance_crawler.platforms.tiktok_sales_data import export_tiktok_sales_data, export_tiktok_sales_data_with_ctx
from finance_crawler.platforms.tiktok_withdrawals import (
    close_tiktok_browser,
    export_tiktok_withdrawals,
    export_tiktok_withdrawals_with_ctx,
    start_tiktok_browser,
)


class TeeStream:
    def __init__(self, primary, log_file):
        self.primary = primary
        self.log_file = log_file
        self.encoding = getattr(primary, "encoding", "utf-8")

    def write(self, text: str) -> int:
        self.primary.write(text)
        self.log_file.write(text)
        return len(text)

    def flush(self) -> None:
        self.primary.flush()
        self.log_file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.primary, "isatty", lambda: False)())


@contextlib.contextmanager
def run_log_capture(config):
    if not config.save_run_log():
        yield None
        return
    run_dir = config.log_root() / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with log_path.open("w", encoding="utf-8", buffering=1) as log_file:
        sys.stdout = TeeStream(original_stdout, log_file)
        sys.stderr = TeeStream(original_stderr, log_file)
        try:
            yield log_path
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="财务数据采集入口。")
    parser.add_argument("--env", choices=["local", "prod"], default="local", help="配置环境。")
    parser.add_argument("--config-dir", default=str(PROJECT_DIR / "config"), help="配置目录。")
    parser.add_argument("--task", action="append", default=[], help="指定任务 id，可重复传入。")
    parser.add_argument("--platform", action="append", default=[], help="指定平台，如 shein、temu、tiktok。可重复传入。")
    parser.add_argument("--account", action="append", default=[], help="指定账号名或账号前缀，如 A1。可重复传入。")
    parser.add_argument("--shop", action="append", default=[], help="指定店铺名/店铺ID/店铺账号缩写，如 fuzz 或 B2。可重复传入。")
    parser.add_argument("--period", choices=["monthly", "weekly"], default="", help="导出周期，不传时使用任务默认周期。")
    parser.add_argument("--today", default="", help="测试用运行日期，格式 YYYY-MM-DD。不传时使用当天。")
    parser.add_argument("--diagnose", action="store_true", help="诊断模式：强制保存 capture，并记录浏览器关键请求。")
    parser.add_argument("--dry-run", action="store_true", help="只展开任务，不执行采集。")
    return parser.parse_args()


def parse_today(raw_value: str):
    if not raw_value:
        return None
    return datetime.strptime(raw_value, "%Y-%m-%d")


def expand_tasks(tasks: list[dict], period_arg: str, today_arg: str) -> list[dict]:
    today = parse_today(today_arg)
    expanded = []
    for task in tasks:
        period_type = period_arg or str(task.get("default_period") or "monthly")
        frequency = task.get("frequency") or []
        if frequency and period_type not in frequency:
            raise RuntimeError(f"任务 {task.get('id')} 不支持周期 {period_type}，支持: {', '.join(frequency)}")
        timezone = str(task.get("timezone") or "Asia/Shanghai")
        period = resolve_previous_period(period_type, today=today, timezone=timezone)
        expanded.append({**task, "resolved_period": period.to_dict()})
    return expanded


def apply_runtime_flags(tasks: list[dict], runtime: dict[str, Any], diagnose: bool = False) -> list[dict]:
    save_capture_files = bool(runtime.get("save_capture_files", True))
    return [
        {
            **task,
            "save_capture_files": True if diagnose else bool(task.get("save_capture_files", save_capture_files)),
            "diagnostic_mode": bool(task.get("diagnostic_mode") or diagnose),
        }
        for task in tasks
    ]


def apply_shop_selectors(tasks: list[dict], selectors: list[str]) -> list[dict]:
    values: list[str] = []
    for selector in selectors or []:
        for chunk in str(selector or "").split(","):
            value = chunk.strip()
            if value and value not in values:
                values.append(value)
    if not values:
        return tasks
    return [{**task, "shop_selectors": values} for task in tasks]


def filter_tasks_by_platforms(tasks: list[dict], platform_selectors: list[str]) -> list[dict]:
    platforms: set[str] = set()
    for selector in platform_selectors or []:
        for chunk in str(selector or "").split(","):
            value = chunk.strip().lower()
            if value:
                platforms.add(value)
    if not platforms:
        return tasks
    return [task for task in tasks if str(task.get("platform") or "").lower() in platforms]


def account_display_name(account: Any) -> str:
    if isinstance(account, dict):
        return str(
            account.get("label")
            or account.get("name")
            or account.get("browserName")
            or account.get("store_username")
            or ""
        )
    return str(account or "")


def account_match_text(account: Any) -> str:
    if not isinstance(account, dict):
        return str(account or "")
    values = [
        account_display_name(account),
        account.get("name"),
        account.get("browserName"),
        account.get("store_username"),
        account.get("platform_id"),
        account.get("siteId"),
        account.get("site_id"),
    ]
    return " ".join(str(value) for value in values if value not in (None, ""))


def resolve_accounts(all_accounts: list[Any], selectors: list[str], strict: bool = True) -> list[Any]:
    if not selectors:
        return list(all_accounts)
    resolved: list[Any] = []
    for selector in selectors:
        raw = str(selector or "").strip()
        if not raw:
            continue
        for chunk in raw.split(","):
            value = chunk.strip()
            if not value:
                continue
            matches = [account for account in all_accounts if account_display_name(account) == value]
            if not matches:
                upper_value = value.upper()
                if re.fullmatch(r"[A-Z]+\d+", upper_value):
                    matches = [
                        account for account in all_accounts
                        if account_prefix_code(account_display_name(account)) == upper_value
                    ]
                else:
                    matches = [
                        account for account in all_accounts
                        if account_match_text(account).upper().startswith(upper_value)
                        or upper_value in account_match_text(account).upper()
                    ]
            if not matches:
                if strict:
                    raise RuntimeError(f"账号未在配置中找到: {value}")
                continue
            for account in matches:
                if account not in resolved:
                    resolved.append(account)
    return resolved


def account_prefix_code(account_name: str) -> str:
    text = str(account_name or "").strip().upper()
    match = re.search(r"(?:^|[^A-Z0-9])([A-Z]+\d+[A-Z]*)(?:[^A-Z0-9]|$)", text)
    if match:
        return match.group(1)
    match = re.match(r"[A-Z]+\d+[A-Z]*", text)
    return match.group(0) if match else text


def write_run_summary(output_root: Path, payload: dict[str, Any]) -> Path:
    run_dir = output_root / "runs" / datetime.now().strftime("%Y-%m-%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"run_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def period_label(period: PeriodRange) -> str:
    name = "月" if period.period_type == "monthly" else "周"
    return f"{name} {period.start:%Y-%m-%d %H:%M:%S} 至 {period.end:%Y-%m-%d %H:%M:%S}"


def result_output_paths(result: TaskResult) -> list[str]:
    outputs = (result.data or {}).get("outputs")
    if isinstance(outputs, list):
        return [str(item).strip() for item in outputs if str(item or "").strip()]
    return [part.strip() for part in str(result.output_path or "").split(";") if part.strip()]


def result_output_count(results: list[TaskResult]) -> int:
    return sum(len(result_output_paths(result)) for result in results)


NO_DATA_MESSAGE_PARTS = (
    "暂无数据可导出",
    "无记录",
    "MILS-导出文件失败",
)


def is_no_data_message(message: str) -> bool:
    text = str(message or "")
    return any(part in text for part in NO_DATA_MESSAGE_PARTS)


def normalize_result_status(result: TaskResult) -> TaskResult:
    if result.status == "no_data":
        result.success = True
        return result
    if is_no_data_message(result.message) and not result_output_paths(result):
        result.success = True
        result.status = "no_data"
        result.data = {**(result.data or {}), "no_data": True}
    return result


def result_log_status(result: TaskResult) -> str:
    if result.status == "no_data":
        return "无数据"
    return "完成" if result.success else "失败"


def result_status_counts(results: list[TaskResult]) -> dict[str, int]:
    return {
        "success": sum(1 for item in results if item.success and item.status != "no_data"),
        "no_data": sum(1 for item in results if item.status == "no_data"),
        "failed": sum(1 for item in results if not item.success),
    }


def result_detail_lines(results: list[TaskResult]) -> str:
    lines: list[str] = []
    no_data = [item for item in results if item.status == "no_data"]
    failed = [item for item in results if not item.success]
    if no_data:
        lines.append("无数据明细:")
        for item in no_data:
            lines.append(f"  - {item.account_name} | {item.task_id} | {item.message}")
    if failed:
        lines.append("失败明细:")
        for item in failed:
            lines.append(f"  - {item.account_name} | {item.task_id} | {item.message}")
    return "\n".join(lines)


def print_result_outputs(result: TaskResult) -> None:
    paths = result_output_paths(result)
    if not paths:
        return
    if len(paths) == 1:
        print(f"  文件: {paths[0]}")
        return
    print(f"  文件数: {len(paths)}")
    for path in paths:
        print(f"    - {path}")


def task_display_name(task: dict[str, Any]) -> str:
    return str(task.get("task_name") or task.get("id") or "")


def build_jobs(config, tasks: list[dict], account_selectors: list[str]) -> list[tuple[dict[str, Any], str]]:
    jobs: list[tuple[dict[str, Any], str]] = []
    strict_accounts = len(tasks) == 1
    for task in tasks:
        platform = str(task.get("platform") or "")
        account_source = str(task.get("account_source") or platform)
        accounts = resolve_accounts(config.accounts.get(account_source) or [], account_selectors, strict=strict_accounts)
        for account_name in accounts:
            jobs.append((task, account_name))
    return jobs


BATCH_BY_ACCOUNT_PLATFORMS = {"shein", "pop", "tiktok"}


def should_batch_by_account(tasks: list[dict]) -> bool:
    if len(tasks) <= 1:
        return False
    return all(str(task.get("platform") or "") in BATCH_BY_ACCOUNT_PLATFORMS for task in tasks)


SHARED_SHEIN_AUTH_RUNNERS = {
    "mils.sales_ledger",
    "gsfs.merchant_billing",
    "gsfs.platform_fees",
    "gsfs.platform_income",
    "gsfs.pop_sales_data",
    "mws.balance_records",
    "shein.funds",
}


def is_shared_shein_auth_task(task: dict[str, Any]) -> bool:
    return (
        str(task.get("runner") or "") in SHARED_SHEIN_AUTH_RUNNERS
        and "sso.geiwohuo.com" in str(task.get("target_page") or "").lower()
    )


def prepare_shared_shein_auth_for_batch(
    account_name: str,
    account_tasks: list[dict[str, Any]],
    auth_path: Path,
    login_timeout: int,
) -> AuthResult | None:
    target_urls: list[str] = []
    for task in account_tasks:
        if not is_shared_shein_auth_task(task):
            continue
        target_page = str(task.get("target_page") or "").strip()
        if target_page and target_page not in target_urls:
            target_urls.append(target_page)
    if not target_urls:
        return None
    return shein_shared_cookie_login(
        account_name,
        auth_path,
        target_urls,
        timeout_seconds=max(60, login_timeout),
    )


def should_batch_tiktok_with_shared_browser(tasks: list[dict]) -> bool:
    if len(tasks) <= 1:
        return False
    return all(str(task.get("platform") or "") == "tiktok" for task in tasks)


def should_run_jobs_serially(tasks: list[dict]) -> bool:
    if any(
        str(task.get("runner") or "") == "tiktok_email.income"
        or str(task.get("platform") or "").upper() == "E1E2"
        for task in tasks
    ):
        return True
    return bool(tasks) and all(is_temu_task(task) for task in tasks)


def is_temu_task(task: dict[str, Any]) -> bool:
    return (
        str(task.get("runner") or "") == "temu.fund_details"
        or str(task.get("id") or "") == "temu_fund_details"
    )


def job_worker_count(config, tasks: list[dict], job_count: int) -> int:
    workers = max(1, min(config.max_workers(), 3, job_count or 1))
    if should_run_jobs_serially(tasks):
        return 1
    return workers


def build_account_batches(
    config,
    tasks: list[dict],
    account_selectors: list[str],
) -> list[tuple[str, list[dict[str, Any]]]]:
    batches: dict[str, list[dict[str, Any]]] = {}
    strict_accounts = len(tasks) == 1
    for task in tasks:
        platform = str(task.get("platform") or "")
        account_source = str(task.get("account_source") or platform)
        accounts = resolve_accounts(config.accounts.get(account_source) or [], account_selectors, strict=strict_accounts)
        for account_name in accounts:
            batches.setdefault(account_display_name(account_name), []).append(task)
    return list(batches.items())


def print_run_plan(
    config,
    tasks: list[dict],
    jobs: list[tuple[dict[str, Any], str]],
    dry_run: bool,
    diagnose: bool = False,
) -> None:
    print("=" * 72)
    print(
        f"财务采集启动 | 环境={config.env} | 任务数={len(tasks)} | 账号任务={len(jobs)} | "
        f"并发={job_worker_count(config, tasks, len(jobs))} | "
        f"紫鸟鉴权并发={config.ziniu_auth_concurrency()} | "
        f"账号内并发={config.account_module_concurrency()}"
    )
    print(f"输出目录: {config.output_root()}")
    if dry_run:
        print("模式: dry-run，只展开任务，不执行采集")
    if diagnose:
        print("模式: diagnose，保存接口诊断 capture")
    print("-" * 72)
    for task in tasks:
        period = task_period(task)
        accounts = [account for current_task, account in jobs if current_task is task]
        print(f"任务: {task_display_name(task)} ({task.get('id')})")
        print(f"平台: {task.get('platform')} | 周期: {period_label(period)} | 账号数: {len(accounts)}")
        if accounts:
            print(f"账号: {', '.join(account_display_name(account) for account in accounts)}")
        print("-" * 72)


def print_batch_run_plan(
    config,
    tasks: list[dict],
    batches: list[tuple[str, list[dict[str, Any]]]],
    dry_run: bool,
    diagnose: bool = False,
) -> None:
    total_jobs = sum(len(account_tasks) for _, account_tasks in batches)
    print("=" * 72)
    print(
        f"财务采集启动 | 环境={config.env} | 账号批处理 | "
        f"任务数={len(tasks)} | 账号数={len(batches)} | 模块执行={total_jobs} | "
        f"并发={max(1, min(config.max_workers(), 3, len(batches) or 1))} | "
        f"紫鸟鉴权并发={config.ziniu_auth_concurrency()} | "
        f"账号内并发={config.account_module_concurrency()}"
    )
    print(f"输出目录: {config.output_root()}")
    if dry_run:
        print("模式: dry-run，只展开任务，不执行采集")
    if diagnose:
        print("模式: diagnose，保存接口诊断 capture")
    print("-" * 72)
    for account_name, account_tasks in batches:
        print(f"账号: {account_name} | 模块数: {len(account_tasks)}")
        for task in account_tasks:
            period = task_period(task)
            print(f"  - {task_display_name(task)} ({task.get('id')}) | {period_label(period)}")
        print("-" * 72)


def task_period(task: dict[str, Any]) -> PeriodRange:
    payload = task.get("resolved_period") or {}
    timezone = str(task.get("timezone") or "Asia/Shanghai")
    period_type = str(payload.get("period_type") or task.get("default_period") or "monthly")
    if payload.get("start") and payload.get("end"):
        tz = ZoneInfo(timezone)
        start = datetime.strptime(str(payload["start"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        end = datetime.strptime(str(payload["end"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        return PeriodRange(period_type=period_type, start=start, end=end)
    return resolve_previous_period(period_type, timezone=timezone)


def run_one_task_account(
    config,
    task: dict[str, Any],
    account_name: str,
    request_timeout: int,
    login_timeout: int,
) -> TaskResult:
    platform = str(task.get("platform") or "")
    period = task_period(task)
    if task.get("runner") == "shein.funds":
        return export_shein_funds(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "mws.balance_records":
        return export_balance_records(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "gsfs.merchant_billing":
        return export_merchant_billing(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "mils.sales_ledger":
        return export_sales_ledger(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "gsfs.pop_sales_data":
        return export_pop_sales_data(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "gsfs.platform_income":
        return export_platform_income(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "gsfs.platform_fees":
        return export_platform_fees(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "temu.fund_details":
        return export_temu_fund_details(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "shenhe.report_bill":
        return export_shenhe_report_bill(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "aliexpress.finance":
        return export_aliexpress_finance(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "tiktok.withdrawals":
        return export_tiktok_withdrawals(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "tiktok.sales_data":
        return export_tiktok_sales_data(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "tiktok.fee_center":
        return export_tiktok_fee_center(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "tiktok.fund_account":
        return export_tiktok_fund_account(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    if task.get("runner") == "tiktok_email.income":
        return export_tiktok_email_income(
            task=task,
            account_name=account_name,
            period=period,
            auth_path=config.desktop_auth_path(),
            output_root=config.output_root(),
            request_timeout=request_timeout,
            login_timeout=login_timeout,
        )
    return TaskResult(
        task_id=str(task.get("id") or ""),
        platform=platform,
        account_name=account_name,
        success=False,
        message=f"未实现 runner: {task.get('runner')}",
    )


def run_one_task_account_with_retry(
    config,
    task: dict[str, Any],
    account_name: str,
    request_timeout: int,
    login_timeout: int,
    max_attempts: int,
) -> TaskResult:
    attempts = max(1, max_attempts)
    result: TaskResult | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = run_one_task_account(config, task, account_name, request_timeout, login_timeout)
        except Exception as exc:
            result = TaskResult(
                task_id=str(task.get("id") or ""),
                platform=str(task.get("platform") or ""),
                account_name=account_name,
                success=False,
                message=f"未捕获异常: {exc}",
            )
        result = normalize_result_status(result)
        result.data = {**(result.data or {}), "attempt": attempt, "max_attempts": attempts}
        if result.success or attempt >= attempts:
            return result
        print(f"[重试] {task_display_name(task)} | {account_name} | 第 {attempt} 次失败，准备重试: {result.message}")
    return result or TaskResult(
        task_id=str(task.get("id") or ""),
        platform=str(task.get("platform") or ""),
        account_name=account_name,
        success=False,
        message="任务未执行。",
    )


def run_account_task_batch_with_retry(
    config,
    account_name: str,
    account_tasks: list[dict[str, Any]],
    request_timeout: int,
    login_timeout: int,
    max_attempts: int,
) -> list[TaskResult]:
    results: list[TaskResult] = []
    module_workers = max(1, min(config.account_module_concurrency(), len(account_tasks) or 1))
    print(f"[账号开始] {account_name} | 模块数 {len(account_tasks)} | 账号内并发={module_workers}")
    shared_auth = None
    shared_auth_failed = False
    if any(is_shared_shein_auth_task(task) for task in account_tasks):
        for attempt in range(1, max(1, max_attempts) + 1):
            shared_auth = prepare_shared_shein_auth_for_batch(
                account_name,
                account_tasks,
                config.desktop_auth_path(),
                login_timeout,
            )
            if shared_auth is None or shared_auth.success or attempt >= max_attempts:
                break
            print(f"[重试] 账号共享鉴权 | {account_name} | 第 {attempt} 次失败，准备重试: {shared_auth.message}")
        if shared_auth and not shared_auth.success:
            shared_auth_failed = True
            print(f"[降级] 账号共享鉴权 | {account_name} | {shared_auth.message}；改用模块级串行鉴权")
            shared_auth = None
    if shared_auth_failed:
        module_workers = 1
    prepared_tasks = [
        {**task, "_auth_result": shared_auth}
        if shared_auth and is_shared_shein_auth_task(task)
        else task
        for task in account_tasks
    ]

    def run_prepared_task(task: dict[str, Any]) -> TaskResult:
        return run_one_task_account_with_retry(
            config,
            task,
            account_name,
            request_timeout,
            login_timeout,
            max_attempts,
        )

    with ThreadPoolExecutor(max_workers=module_workers) as executor:
        future_map = {}
        for task in prepared_tasks:
            period = task_period(task)
            print(f"[开始] {task_display_name(task)} | {account_name} | {period_label(period)}")
            future_map[executor.submit(run_prepared_task, task)] = task
        ordered_results: dict[str, TaskResult] = {}
        for future in as_completed(future_map):
            task = future_map[future]
            try:
                ordered_results[str(task.get("id") or "")] = future.result()
            except Exception as exc:
                ordered_results[str(task.get("id") or "")] = TaskResult(
                    task_id=str(task.get("id") or ""),
                    platform=str(task.get("platform") or ""),
                    account_name=account_name,
                    success=False,
                    message=f"账号内模块未捕获异常: {exc}",
                )

    for task in prepared_tasks:
        result = ordered_results.get(str(task.get("id") or ""))
        if result is None:
            result = TaskResult(
                task_id=str(task.get("id") or ""),
                platform=str(task.get("platform") or ""),
                account_name=account_name,
                success=False,
                message="账号内模块未返回结果。",
            )
        results.append(result)
        status = result_log_status(result)
        print(f"[{status}] {task_display_name(task)} | {account_name} | {result.message}")
        print_result_outputs(result)
        if result.capture_path:
            print(f"  记录: {result.capture_path}")
    counts = result_status_counts(results)
    print(
        f"[账号结束] {account_name} | 执行成功={counts['success']} | "
        f"无数据={counts['no_data']} | 执行失败={counts['failed']} | 输出文件={result_output_count(results)}"
    )
    return results


def run_tasks(config, tasks: list[dict], account_selectors: list[str]) -> list[TaskResult]:
    results: list[TaskResult] = []
    request_timeout = int(config.runtime.get("request_timeout_seconds") or 60)
    login_timeout = int(config.runtime.get("login_timeout_seconds") or 30)
    max_attempts = max(1, int(config.runtime.get("retry_count") or 1))
    jobs = build_jobs(config, tasks, account_selectors)
    max_workers = job_worker_count(config, tasks, len(jobs))

    def run_job(task: dict[str, Any], account_name: str) -> TaskResult:
        if is_temu_task(task):
            with _TEMU_JOB_LOCK:
                return run_one_task_account_with_retry(
                    config,
                    task,
                    account_name,
                    request_timeout,
                    login_timeout,
                    max_attempts,
                )
        return run_one_task_account_with_retry(
            config,
            task,
            account_name,
            request_timeout,
            login_timeout,
            max_attempts,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for task, account_name in jobs:
            period = task_period(task)
            print(f"[开始] {task_display_name(task)} | {account_name} | {period_label(period)}")
            future = executor.submit(run_job, task, account_name)
            future_map[future] = (task, account_name)
        for future in as_completed(future_map):
            task, account_name = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = TaskResult(
                    task_id=str(task.get("id") or ""),
                    platform=str(task.get("platform") or ""),
                    account_name=account_name,
                    success=False,
                    message=f"未捕获异常: {exc}",
                )
            result = normalize_result_status(result)
            results.append(result)
            status = result_log_status(result)
            print(f"[{status}] {task_display_name(task)} | {account_name} | {result.message}")
            print_result_outputs(result)
            if result.capture_path:
                print(f"  记录: {result.capture_path}")
    return results


def run_account_batches(config, tasks: list[dict], account_selectors: list[str]) -> list[TaskResult]:
    results: list[TaskResult] = []
    request_timeout = int(config.runtime.get("request_timeout_seconds") or 60)
    login_timeout = int(config.runtime.get("login_timeout_seconds") or 30)
    max_attempts = max(1, int(config.runtime.get("retry_count") or 1))
    batches = build_account_batches(config, tasks, account_selectors)
    max_workers = max(1, min(config.max_workers(), 3, len(batches) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                run_account_task_batch_with_retry,
                config,
                account_name,
                account_tasks,
                request_timeout,
                login_timeout,
                max_attempts,
            ): (account_name, account_tasks)
            for account_name, account_tasks in batches
        }
        for future in as_completed(future_map):
            account_name, _ = future_map[future]
            try:
                results.extend(future.result())
            except Exception as exc:
                results.append(
                    TaskResult(
                        task_id="account_batch",
                        platform=str((account_tasks[0] if account_tasks else {}).get("platform") or ""),
                        account_name=account_name,
                        success=False,
                        message=f"账号批处理未捕获异常: {exc}",
                    )
                )
    return results


def run_one_tiktok_task_with_ctx(
    task: dict[str, Any],
    account_name: str,
    period: PeriodRange,
    ctx,
    output_root: Path,
    request_timeout: int,
) -> TaskResult:
    runner = str(task.get("runner") or "")
    if runner == "tiktok.withdrawals":
        return export_tiktok_withdrawals_with_ctx(task, account_name, period, ctx, output_root, request_timeout)
    if runner == "tiktok.sales_data":
        return export_tiktok_sales_data_with_ctx(task, account_name, period, ctx, output_root, request_timeout)
    if runner == "tiktok.fee_center":
        return export_tiktok_fee_center_with_ctx(task, account_name, period, ctx, output_root, request_timeout)
    if runner == "tiktok.fund_account":
        return export_tiktok_fund_account_with_ctx(task, account_name, period, ctx, output_root, request_timeout)
    return TaskResult(
        task_id=str(task.get("id") or ""),
        platform=str(task.get("platform") or "tiktok"),
        account_name=account_name,
        success=False,
        message=f"未实现 TK 共享浏览器 runner: {runner}",
    )


def run_tiktok_account_task_batch_with_retry(
    config,
    account_name: str,
    account_tasks: list[dict[str, Any]],
    request_timeout: int,
    login_timeout: int,
    max_attempts: int,
) -> list[TaskResult]:
    results: list[TaskResult] = []
    ctx = None
    print(f"[账号开始] {account_name} | TK共享浏览器 | 模块数 {len(account_tasks)}")
    attempts = max(1, max_attempts)
    start_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ctx = start_tiktok_browser(account_name, config.desktop_auth_path(), login_timeout)
            break
        except Exception as exc:
            start_error = exc
            if attempt < attempts:
                print(
                    f"[重试] TK共享浏览器 | {account_name} | "
                    f"第 {attempt} 次失败，准备重试: {exc}"
                )

    if ctx is None:
        message = f"TK账号共享浏览器启动失败: {start_error}"
        results.extend(
            TaskResult(
                task_id=str(task.get("id") or ""),
                platform=str(task.get("platform") or "tiktok"),
                account_name=account_name,
                success=False,
                message=message,
                data={
                    "attempt": attempts,
                    "max_attempts": attempts,
                    "tiktok_shared_startup_failure": True,
                },
            )
            for task in account_tasks
        )
    else:
        try:
            for task in account_tasks:
                period = task_period(task)
                print(f"[开始] {task_display_name(task)} | {account_name} | {period_label(period)}")
                result: TaskResult | None = None
                for attempt in range(1, attempts + 1):
                    try:
                        result = run_one_tiktok_task_with_ctx(
                            task,
                            account_name,
                            period,
                            ctx,
                            config.output_root(),
                            request_timeout,
                        )
                    except Exception as exc:
                        result = TaskResult(
                            task_id=str(task.get("id") or ""),
                            platform=str(task.get("platform") or "tiktok"),
                            account_name=account_name,
                            success=False,
                            message=f"未捕获异常: {exc}",
                        )
                    result = normalize_result_status(result)
                    result.data = {**(result.data or {}), "attempt": attempt, "max_attempts": attempts}
                    if result.success or attempt >= attempts:
                        break
                    print(
                        f"[重试] {task_display_name(task)} | {account_name} | "
                        f"第 {attempt} 次失败，准备重试: {result.message}"
                    )
                result = result or TaskResult(
                    task_id=str(task.get("id") or ""),
                    platform="tiktok",
                    account_name=account_name,
                    success=False,
                    message="任务未执行。",
                )
                result = normalize_result_status(result)
                results.append(result)
                status = result_log_status(result)
                print(f"[{status}] {task_display_name(task)} | {account_name} | {result.message}")
                print_result_outputs(result)
                if result.capture_path:
                    print(f"  记录: {result.capture_path}")
        except Exception as exc:
            results.append(
                TaskResult(
                    task_id="tiktok_account_batch",
                    platform="tiktok",
                    account_name=account_name,
                    success=False,
                    message=f"TK账号共享浏览器批处理失败: {exc}",
                )
            )
        finally:
            close_tiktok_browser(ctx)
    counts = result_status_counts(results)
    print(
        f"[账号结束] {account_name} | 执行成功={counts['success']} | "
        f"无数据={counts['no_data']} | 执行失败={counts['failed']} | 输出文件={result_output_count(results)}"
    )
    return results


def run_tiktok_account_batches(config, tasks: list[dict], account_selectors: list[str]) -> list[TaskResult]:
    results: list[TaskResult] = []
    request_timeout = int(config.runtime.get("request_timeout_seconds") or 60)
    login_timeout = int(config.runtime.get("login_timeout_seconds") or 30)
    max_attempts = max(1, int(config.runtime.get("retry_count") or 1))
    batches = build_account_batches(config, tasks, account_selectors)
    max_workers = max(1, min(config.max_workers(), 3, len(batches) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                run_tiktok_account_task_batch_with_retry,
                config,
                account_name,
                account_tasks,
                request_timeout,
                login_timeout,
                max_attempts,
            ): (account_name, account_tasks)
            for account_name, account_tasks in batches
        }
        for future in as_completed(future_map):
            account_name, _ = future_map[future]
            try:
                results.extend(future.result())
            except Exception as exc:
                results.append(
                    TaskResult(
                        task_id="tiktok_account_batch",
                        platform="tiktok",
                        account_name=account_name,
                        success=False,
                        message=f"TK账号批处理未捕获异常: {exc}",
                    )
                )
    return results


def rerun_failed_results(config, tasks: list[dict], results: list[TaskResult]) -> list[TaskResult]:
    rerun_count = config.final_failed_rerun_count()
    if rerun_count <= 0:
        return results
    task_by_id = {str(task.get("id") or ""): task for task in tasks}
    request_timeout = int(config.runtime.get("request_timeout_seconds") or 60)
    login_timeout = int(config.runtime.get("login_timeout_seconds") or 30)
    max_attempts = max(1, int(config.runtime.get("retry_count") or 1))
    current_results = list(results)
    for round_index in range(1, rerun_count + 1):
        failed_indexes = [
            index
            for index, item in enumerate(current_results)
            if not item.success and item.status != "no_data" and item.task_id in task_by_id
        ]
        if not failed_indexes:
            break
        print(f"[失败补跑] 第 {round_index} 轮 | 失败项={len(failed_indexes)} | 串行执行")
        shared_startup_groups: dict[str, list[int]] = {}
        for index in failed_indexes:
            failed = current_results[index]
            if (failed.data or {}).get("tiktok_shared_startup_failure"):
                shared_startup_groups.setdefault(failed.account_name, []).append(index)

        handled_indexes: set[int] = set()
        for account_name, indexes in shared_startup_groups.items():
            account_tasks = [task_by_id[current_results[index].task_id] for index in indexes]
            print(
                f"[补跑开始] TK共享浏览器 | {account_name} | "
                f"模块数={len(account_tasks)} | 原因: {current_results[indexes[0]].message}"
            )
            rerun_results = run_tiktok_account_task_batch_with_retry(
                config,
                account_name,
                account_tasks,
                request_timeout,
                login_timeout,
                max_attempts,
            )
            rerun_by_task_id = {result.task_id: result for result in rerun_results}
            for index in indexes:
                failed = current_results[index]
                task = task_by_id[failed.task_id]
                rerun_result = rerun_by_task_id.get(failed.task_id)
                if rerun_result is None:
                    rerun_result = TaskResult(
                        task_id=failed.task_id,
                        platform=failed.platform,
                        account_name=failed.account_name,
                        success=False,
                        message="TK共享浏览器补跑未返回该模块结果。",
                    )
                rerun_result = normalize_result_status(rerun_result)
                rerun_result.data = {
                    **(rerun_result.data or {}),
                    "final_failed_rerun": True,
                    "final_failed_rerun_round": round_index,
                    "previous_message": failed.message,
                }
                current_results[index] = rerun_result
                handled_indexes.add(index)
                status = result_log_status(rerun_result)
                print(f"[补跑{status}] {task_display_name(task)} | {account_name} | {rerun_result.message}")
                print_result_outputs(rerun_result)
                if rerun_result.capture_path:
                    print(f"  记录: {rerun_result.capture_path}")

        for index in failed_indexes:
            if index in handled_indexes:
                continue
            failed = current_results[index]
            task = task_by_id.get(failed.task_id)
            if not task:
                continue
            print(f"[补跑开始] {task_display_name(task)} | {failed.account_name} | 原因: {failed.message}")
            rerun_result = run_one_task_account_with_retry(
                config,
                task,
                failed.account_name,
                request_timeout,
                login_timeout,
                max_attempts,
            )
            rerun_result = normalize_result_status(rerun_result)
            rerun_result.data = {
                **(rerun_result.data or {}),
                "final_failed_rerun": True,
                "final_failed_rerun_round": round_index,
                "previous_message": failed.message,
            }
            current_results[index] = rerun_result
            status = result_log_status(rerun_result)
            print(f"[补跑{status}] {task_display_name(task)} | {failed.account_name} | {rerun_result.message}")
            print_result_outputs(rerun_result)
            if rerun_result.capture_path:
                print(f"  记录: {rerun_result.capture_path}")
    return current_results


def main() -> int:
    args = parse_args()
    config = load_app_config(args.env, args.config_dir)
    configure_ziniu_client_environment(
        config.ziniu_install_dir(),
        config.ziniu_host(),
        config.ziniu_port(),
    )
    with run_log_capture(config) as run_log_path:
        return run_main(args, config, run_log_path)


def run_main(args, config, run_log_path: Path | None = None) -> int:
    configure_ziniu_auth_concurrency(config.ziniu_auth_concurrency())
    if run_log_path:
        print(f"运行日志: {run_log_path}")
    selected = set(args.task)
    enabled_tasks = [
        task for task in config.tasks
        if task.get("enabled") and (not selected or task.get("id") in selected)
    ]
    enabled_tasks = filter_tasks_by_platforms(enabled_tasks, args.platform)
    tasks = apply_shop_selectors(
        apply_runtime_flags(expand_tasks(enabled_tasks, args.period, args.today), config.runtime, diagnose=args.diagnose),
        args.shop,
    )
    batch_tiktok_shared = should_batch_tiktok_with_shared_browser(tasks)
    batch_by_account = should_batch_by_account(tasks)
    jobs = build_jobs(config, tasks, args.account)
    batches = build_account_batches(config, tasks, args.account) if batch_by_account else []
    payload = {
        "generated_at": datetime.now().isoformat(),
        "environment": args.env,
        "config_dir": str(config.config_dir),
        "output_root": str(config.output_root()),
        "run_log_path": str(run_log_path or ""),
        "max_workers": config.max_workers(),
        "ziniu_auth_concurrency": config.ziniu_auth_concurrency(),
        "account_module_concurrency": config.account_module_concurrency(),
        "final_failed_rerun_count": config.final_failed_rerun_count(),
        "task_count": len(tasks),
        "job_count": len(jobs),
        "batch_by_account": batch_by_account,
        "batch_tiktok_shared_browser": batch_tiktok_shared,
        "diagnose": bool(args.diagnose),
        "tasks": tasks,
        "account_counts": {key: len(value or []) for key, value in config.accounts.items()},
        "dry_run": args.dry_run,
    }
    if not tasks:
        print("没有启用的任务。先在 config/tasks.json 打开任务开关。")
        return 0
    if batch_by_account:
        print_batch_run_plan(config, tasks, batches, args.dry_run, diagnose=args.diagnose)
    else:
        print_run_plan(config, tasks, jobs, args.dry_run, diagnose=args.diagnose)
    if args.dry_run:
        return 0

    if batch_tiktok_shared:
        results = run_tiktok_account_batches(config, tasks, args.account)
    elif batch_by_account:
        results = run_account_batches(config, tasks, args.account)
    else:
        results = run_tasks(config, tasks, args.account)
    results = rerun_failed_results(config, tasks, results)
    result_payload = {
        **payload,
        "success_count": result_status_counts(results)["success"],
        "no_data_count": result_status_counts(results)["no_data"],
        "failed_count": result_status_counts(results)["failed"],
        "output_file_count": result_output_count(results),
        "results": [item.to_dict() for item in results],
    }
    summary_path = None
    if bool(config.runtime.get("save_run_summary", True)):
        summary_path = write_run_summary(config.output_root(), result_payload)
    print("=" * 72)
    print(
        f"采集结束 | 账号={len({item.account_name for item in results})} | 模块={len(tasks)} | "
        f"执行成功={result_payload['success_count']} | 无数据={result_payload['no_data_count']} | "
        f"执行失败={result_payload['failed_count']} | "
        f"输出文件={result_payload['output_file_count']}"
    )
    detail_lines = result_detail_lines(results)
    if detail_lines:
        print(detail_lines)
    if summary_path:
        print(f"运行汇总: {summary_path}")
    return 1 if any(not item.success for item in results) else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
