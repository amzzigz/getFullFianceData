from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

from openpyxl import Workbook
from openpyxl.styles import Font


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from finance_crawler.platforms.temu_fund_details import (
    browser_get_user_info,
    browser_post_json,
    close_temu_browser,
    ensure_seller_page,
    ensure_success,
    malls_from_user_info,
    set_seller_mall_context,
    shop_matches,
    start_temu_browser,
    temu_account_label,
)


AGENT_BASE = "https://agentseller.temu.com"
HOT_PRODUCTS_URL = f"{AGENT_BASE}/mms/venom/api/supplier/sales/management/listOverall"
HEADERS = ["账号", "店铺名", "商品名称", "品类", "skc", "skc货号", "申报价格"]


def find_hot_result(har: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    for entry in har.get("log", {}).get("entries", []):
        request = entry.get("request", {})
        if request.get("url") != HOT_PRODUCTS_URL:
            continue
        try:
            payload = json.loads(request.get("postData", {}).get("text") or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("hotTag") is not True:
            continue
        try:
            response = json.loads(entry.get("response", {}).get("content", {}).get("text") or "{}")
        except json.JSONDecodeError:
            continue
        candidate = response.get("result")
        if isinstance(candidate, dict):
            result = candidate
    if result is None:
        raise RuntimeError("HAR 中没有找到 hotTag=true 的 TEMU 热销款响应。")
    return result


def _price_value(skus: list[dict[str, Any]]) -> int | float | str:
    prices = sorted(
        {
            int(sku["supplierPrice"]) / 100
            for sku in skus
            if isinstance(sku, dict) and sku.get("supplierPrice") not in (None, "")
        }
    )
    normalized = [int(price) if float(price).is_integer() else price for price in prices]
    if len(normalized) == 1:
        return normalized[0]
    return "/".join(str(price) for price in normalized)


def parse_hot_rows(account: str, shop: str, result: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for item in result.get("subOrderList") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            (
                account,
                shop or str(item.get("supplierName") or ""),
                str(item.get("productName") or ""),
                str(item.get("category") or ""),
                str(item.get("productSkcId") or ""),
                str(item.get("skcExtCode") or ""),
                _price_value(item.get("skuQuantityDetailList") or []),
            )
        )
    return rows


def write_excel(rows: list[tuple[Any, ...]], output: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "热销款"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    widths = [28, 24, 60, 24, 18, 28, 16]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + index)].width = width
    ws.column_dimensions["G"].number_format = "0.00"
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)


def collect_hot_products(
    page: Any,
    mall_id: int | str,
    post_json: Callable[..., dict[str, Any]] = browser_post_json,
    page_size: int = 40,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_no = 1
    while True:
        payload = {"pageNo": page_no, "pageSize": page_size, "isLack": 0, "hotTag": True}
        data = ensure_success(post_json(page, HOT_PRODUCTS_URL, payload, mall_id), HOT_PRODUCTS_URL)
        result = data.get("result") or {}
        current = [item for item in result.get("subOrderList") or [] if isinstance(item, dict)]
        records.extend(current)
        total = int(result.get("total") or 0)
        pages = max(1, math.ceil(total / page_size))
        print(f"  第 {page_no}/{pages} 页，累计 {len(records)}/{total} 条")
        if page_no >= pages:
            return records
        page_no += 1


def open_global_sales_page(ctx: Any, mall_id: int | str, timeout_seconds: int = 60) -> None:
    ensure_seller_page(ctx)
    set_seller_mall_context(ctx.page, mall_id)
    target_url = f"{AGENT_BASE}/stock-entry"
    link_url = (
        "https://seller.kuajingmaihuo.com/link-agent-seller"
        f"?region=1&targetUrl={quote(target_url, safe='')}"
    )
    ctx.page.get(link_url)
    end_at = time.time() + timeout_seconds
    while time.time() < end_at:
        current_url = str(getattr(ctx.page, "url", "") or "")
        lower_url = current_url.lower()
        if urlparse(current_url).netloc == urlparse(AGENT_BASE).netloc:
            try:
                ensure_success(
                    browser_post_json(ctx.page, f"{AGENT_BASE}/api/seller/auth/userInfo", {}, mall_id),
                    f"{AGENT_BASE}/api/seller/auth/userInfo",
                )
                return
            except Exception:
                pass
        if "authentication" in lower_url or "login" in lower_url or "link-agent-seller" in lower_url:
            try:
                ctx.page = ctx.helper._handle_click_for_platform(
                    ctx.page,
                    "temu_business",
                    lower_url,
                    ctx.helper._log,
                    ctx.browser,
                )
            except Exception:
                pass
        time.sleep(2)
    raise RuntimeError(f"TEMU 全球站授权超时: mallId={mall_id} url={getattr(ctx.page, 'url', '')}")


def _load_prod_config(config_dir: Path) -> tuple[list[Any], Path, int]:
    accounts = json.loads((config_dir / "accounts.prod.json").read_text(encoding="utf-8")).get("temu") or []
    config = json.loads((config_dir / "prod.json").read_text(encoding="utf-8"))
    auth_path = PROJECT_DIR / str(config["paths"]["desktop_auth_path"])
    login_timeout = int(config.get("runtime", {}).get("login_timeout_seconds") or 30)
    return accounts, auth_path, login_timeout


def _select_accounts(accounts: list[Any], selectors: list[str]) -> list[Any]:
    if not selectors:
        return accounts
    values = [part.strip().lower() for selector in selectors for part in selector.split(",") if part.strip()]
    selected = [
        account for account in accounts
        if any(value in temu_account_label(account).lower() for value in values)
    ]
    if not selected:
        raise RuntimeError(f"TEMU 未匹配到账号: {selectors}")
    return selected


def run_live(config_dir: Path, account_selectors: list[str], shop_selectors: list[str], page_size: int) -> list[tuple[Any, ...]]:
    accounts, auth_path, login_timeout = _load_prod_config(config_dir)
    rows: list[tuple[Any, ...]] = []
    for account in _select_accounts(accounts, account_selectors):
        account_label = temu_account_label(account)
        ctx = None
        try:
            print(f"[账号] {account_label}")
            ctx = start_temu_browser(account, auth_path, login_timeout)
            malls = malls_from_user_info(browser_get_user_info(ctx.page))
            if shop_selectors:
                malls = [
                    mall for index, mall in enumerate(malls)
                    if shop_matches(account_label, mall, index, shop_selectors)
                ]
            if not malls:
                raise RuntimeError(f"TEMU 未匹配到店铺: {shop_selectors or '全部'}")
            for mall in malls:
                mall_id = mall.get("mallId")
                if mall_id in (None, ""):
                    continue
                shop_name = str(mall.get("mallName") or mall.get("name") or mall_id)
                print(f"[店铺] {shop_name} ({mall_id})")
                open_global_sales_page(ctx, mall_id)
                records = collect_hot_products(ctx.page, mall_id, page_size=page_size)
                rows.extend(parse_hot_rows(account_label, shop_name, {"subOrderList": records}))
                print(f"  热销款 {len(records)} 条")
        finally:
            close_temu_browser(ctx)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 TEMU 销售管理中的全部热销款数据。")
    parser.add_argument("--har", default="", help="离线解析 HAR，不启动紫鸟。")
    parser.add_argument("--config-dir", default=str(PROJECT_DIR / "config"))
    parser.add_argument("--account", action="append", default=[], help="账号标签筛选，可重复传入。")
    parser.add_argument("--shop", action="append", default=[], help="店铺名/店铺 ID/B 编号筛选，可重复传入。")
    parser.add_argument("--page-size", type=int, default=40)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    output = Path(args.output) if args.output else PROJECT_DIR / "output" / f"TEMU热销款数据_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    if args.har:
        har = json.loads(Path(args.har).read_text(encoding="utf-8"))
        result = find_hot_result(har)
        account = args.account[0] if args.account else ""
        records = result.get("subOrderList") or []
        shop = str(records[0].get("supplierName") or "") if records else ""
        rows = parse_hot_rows(account, shop, result)
    else:
        rows = run_live(Path(args.config_dir), args.account, args.shop, args.page_size)
    write_excel(rows, output)
    print(f"已导出 {len(rows)} 条 -> {output}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"错误: {exc}")
        raise SystemExit(1) from None
