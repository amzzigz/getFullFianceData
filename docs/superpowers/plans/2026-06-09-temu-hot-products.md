# TEMU Hot Products Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent TEMU hot-products exporter that supports offline HAR parsing and live ZiNiao-backed collection for all configured stores.

**Architecture:** Keep the feature isolated in `scripts/export_temu_hot_products.py`. Reuse existing TEMU browser/account/store helpers, add only the global sales-page authorization flow needed by this endpoint, and write one Excel workbook containing exactly the requested columns.

**Tech Stack:** Python 3.11+, DrissionPage, openpyxl, pytest

---

### Task 1: HAR parsing and row mapping

**Files:**
- Create: `tests/test_export_temu_hot_products.py`
- Create: `scripts/export_temu_hot_products.py`

- [ ] Write tests proving only `hotTag=true` responses are selected and one SKC maps to one seven-column row.
- [ ] Run `py -3 -m pytest tests/test_export_temu_hot_products.py -v` and confirm failure because the script does not exist.
- [ ] Implement HAR response selection, row mapping, price conversion, and Excel writing.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Live collection

**Files:**
- Modify: `scripts/export_temu_hot_products.py`
- Modify: `tests/test_export_temu_hot_products.py`

- [ ] Write tests for pagination and account selection.
- [ ] Run focused tests and confirm the new tests fail.
- [ ] Implement global sales-page authorization, per-store pagination, CLI account/shop filtering, and browser cleanup.
- [ ] Run focused tests and confirm they pass.

### Task 3: End-to-end verification

**Files:**
- Verify: `scripts/export_temu_hot_products.py`

- [ ] Run HAR mode against `C:\Users\ln\Desktop\temu-热销款数据.har`.
- [ ] Inspect the workbook headers and rows; confirm exactly seven columns and three `MinimalKnit` records.
- [ ] Run live mode for account `B23/B25/B26-主账号-YF` and shop `MinimalKnit`.
- [ ] Run the full pytest suite and report any unrelated failures separately.
