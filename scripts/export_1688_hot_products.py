from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import urlopen

from openpyxl import Workbook
from openpyxl.styles import Font
from playwright.sync_api import sync_playwright


os.environ.setdefault("NODE_NO_WARNINGS", "1")

DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_PROFILE = r"E:\自动化\browser_profiles\1688_main"
DEFAULT_HAR = r"C:\Users\ln\Desktop\1688货号.har"
MANAGE_MARKER = "/offer/manage_mini.vm"


def find_manage_request(har_path: Path) -> str:
    har = json.loads(har_path.read_text(encoding="utf-8"))
    for entry in har.get("log", {}).get("entries", []):
        url = str(entry.get("request", {}).get("url") or "")
        if MANAGE_MARKER in url and "show_type=valid" in url:
            return url
    raise RuntimeError("HAR 中没有找到热销中商品接口 manage_mini.vm?show_type=valid")


def with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["currentPage"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def parse_rows(data: dict[str, Any]) -> list[tuple[str, str, Any]]:
    return [
        (
            str(item.get("itemNumber") or ""),
            str(item.get("subject") or ""),
            item.get("qualityStar", ""),
        )
        for item in data.get("items", [])
    ]


def cdp_websocket(port: int) -> str:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
            return str(data["webSocketDebuggerUrl"])
    except Exception as exc:
        raise RuntimeError(f"Chrome 调试端口 {port} 不可用") from exc


def ensure_chrome(chrome: str, profile: str, port: int) -> None:
    try:
        cdp_websocket(port)
        return
    except RuntimeError:
        pass
    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    )
    for _ in range(30):
        time.sleep(0.5)
        try:
            cdp_websocket(port)
            return
        except RuntimeError:
            pass
    raise RuntimeError(f"已启动 Chrome，但调试端口 {port} 仍不可用")


def click_page(page: Any, page_no: int) -> dict[str, Any]:
    selector = f"li.ant-pagination-item-{page_no}"
    locator = page.locator(selector)
    if locator.count() == 0:
        input_box = page.locator(".ant-pagination-options-quick-jumper input")
        input_box.fill(str(page_no))
        with page.expect_response(lambda r: MANAGE_MARKER in r.url and f"currentPage={page_no}" in r.url, timeout=30000) as info:
            input_box.press("Enter")
        response = info.value
    else:
        with page.expect_response(lambda r: MANAGE_MARKER in r.url and f"currentPage={page_no}" in r.url, timeout=30000) as info:
            locator.click()
        response = info.value
    data = response.json()
    if data.get("msgType") != "success":
        raise RuntimeError(f"第 {page_no} 页返回失败: {str(data)[:500]}")
    return data


def collect_all(page: Any) -> list[tuple[str, str, Any]]:
    active = int(page.locator("li.ant-pagination-item-active").inner_text().strip())
    if active == 1:
        click_page(page, 2)
    first = click_page(page, 1)
    total = int(first.get("totalCount") or 0)
    page_size = int(first.get("pageSize") or 20)
    pages = max(1, math.ceil(total / page_size))
    rows = parse_rows(first)
    print(f"热销中商品总数: {total}; 共 {pages} 页")
    for page_no in range(2, pages + 1):
        data = click_page(page, page_no)
        rows.extend(parse_rows(data))
        print(f"已抓取 {page_no}/{pages} 页，累计 {len(rows)} 条")
        time.sleep(0.3)
    return rows


def write_excel(rows: list[tuple[str, str, Any]], output: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "热销中"
    ws.append(["货号", "商品名", "质量分"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 80
    ws.column_dimensions["C"].width = 12
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 1688 专用 Chrome，导出热销中商品的货号、商品名、质量分。")
    parser.add_argument("--har", default=DEFAULT_HAR)
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    request_url = find_manage_request(Path(args.har))
    output = Path(args.output) if args.output else Path.home() / "Desktop" / f"1688热销中商品_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    ensure_chrome(args.chrome, args.profile, args.port)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_websocket(args.port), timeout=15000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.goto("https://offer.1688.com/app/pages-group/manage-home/index.html", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        rows = collect_all(page)
        page.close()

    write_excel(rows, output)
    print(f"已导出 {len(rows)} 条 -> {output}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"错误: {exc}")
        raise SystemExit(1) from None
