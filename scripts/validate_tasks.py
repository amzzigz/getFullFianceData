from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
TASKS_PATH = PROJECT_DIR / "config" / "tasks.json"

KNOWN_RUNNERS = {
    "mils.sales_ledger",
    "mws.balance_records",
    "gsfs.merchant_billing",
    "gsfs.pop_sales_data",
    "gsfs.platform_income",
    "gsfs.platform_fees",
    "shein.funds",
    "temu.fund_details",
    "shenhe.report_bill",
    "aliexpress.finance",
    "tiktok.withdrawals",
    "tiktok.sales_data",
    "tiktok.fee_center",
    "tiktok.fund_account",
    "tiktok_email.income",
}

REQUIRED_FIELDS = {
    "id",
    "enabled",
    "platform",
    "account_source",
    "task_name",
    "frequency",
    "default_period",
    "runner",
    "timezone",
}

VALID_PERIODS = {"monthly", "weekly"}


def main() -> int:
    if not TASKS_PATH.exists():
        print(f"ERROR: not found: {TASKS_PATH}")
        return 1

    payload = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") or []
    errors: list[str] = []
    ids: list[str] = []

    for index, task in enumerate(tasks):
        label = task.get("id") or f"<index:{index}>"
        missing = sorted(REQUIRED_FIELDS - set(task.keys()))
        if missing:
            errors.append(f"{label}: missing fields: {', '.join(missing)}")

        task_id = str(task.get("id") or "").strip()
        if task_id:
            ids.append(task_id)
        else:
            errors.append(f"{label}: empty id")

        runner = str(task.get("runner") or "")
        if runner not in KNOWN_RUNNERS:
            errors.append(f"{label}: unknown runner: {runner}")

        frequency = set(task.get("frequency") or [])
        invalid_periods = sorted(frequency - VALID_PERIODS)
        if invalid_periods:
            errors.append(f"{label}: invalid frequency: {', '.join(invalid_periods)}")

        default_period = task.get("default_period")
        if default_period not in VALID_PERIODS:
            errors.append(f"{label}: invalid default_period: {default_period}")
        elif frequency and default_period not in frequency:
            errors.append(f"{label}: default_period not in frequency: {default_period}")

        if task.get("output_type") == "download" and not task.get("export_folder"):
            errors.append(f"{label}: download task should define export_folder")

    duplicates = [task_id for task_id, count in Counter(ids).items() if count > 1]
    for task_id in duplicates:
        errors.append(f"duplicate task id: {task_id}")

    print(f"tasks: {len(tasks)}")
    print("runners:")
    for runner, count in sorted(Counter(t.get("runner") for t in tasks).items()):
        print(f"  {runner}: {count}")

    if errors:
        print("\nERRORS:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("OK: task config contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
