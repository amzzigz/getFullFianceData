from __future__ import annotations

import re
import unicodedata
from typing import Any

from finance_crawler.periods import PeriodRange


MODULE_CODES = {
    "shein_funds": "提现明细",
    "balance_records": "资金流水",
    "merchant_billing": "商家账单",
    "sales_ledger": "销售数据",
    "platform_fees": "平台费用",
    "platform_income": "销售平台费用",
    "pop_sales_data": "销售平台费用",
    "temu_fund_details": "资金明细",
    "shenhe_report_bill": "报账单",
    "shenhe.report_bill": "报账单",
    "aliexpress_finance": "速卖通资金",
    "aliexpress.finance": "速卖通资金",
    "tiktok_withdrawals": "TK提现明细",
    "tiktok.withdrawals": "TK提现明细",
    "tiktok_sales_data": "TK销售数据",
    "tiktok.sales_data": "TK销售数据",
    "tiktok_fee_center": "TK费用中心",
    "tiktok.fee_center": "TK费用中心",
    "tiktok_fund_account": "TK资金账户",
    "tiktok.fund_account": "TK资金账户",
    "tiktok_email_income": "销售数据",
    "tiktok_email.income": "销售数据",
}


def ascii_slug(value: Any, fallback: str = "file") -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return text or fallback


def filename_part(value: Any, fallback: str = "文件") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text, flags=re.UNICODE).strip("_")
    text = re.sub(r"_+", "_", text)
    return text or fallback


def account_code(account_name: str) -> str:
    text = str(account_name or "").upper()
    match = re.search(r"\bSPP\d+\b|[A-Z]+\d+[A-Z]*", text)
    return match.group(0) if match else ascii_slug(account_name, "acct")


def period_code(period: PeriodRange) -> str:
    return f"{period.start:%Y%m%d}-{period.end:%Y%m%d}"


def module_code(task: dict[str, Any], fallback: str) -> str:
    task_id = str(task.get("id") or "")
    runner = str(task.get("runner") or "")
    for key, code in MODULE_CODES.items():
        if key in task_id or key in runner:
            return code
    return filename_part(fallback, "导出")


def download_stem(
    account_name: str,
    period: PeriodRange,
    module: str,
    *parts: Any,
) -> str:
    tokens = [account_code(account_name), period_code(period), filename_part(module, "导出")]
    tokens.extend(filename_part(part, "") for part in parts if str(part or "").strip())
    return "_".join(token for token in tokens if token)
