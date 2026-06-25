# SHEIN Tab Reconnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DrissionPage native tab recovery to normal SHEIN/POP/A1B shared login and A1Y-A4Y Shenhe report-bill browser operations.

**Architecture:** Each SHEIN browser-owning module keeps its current lifecycle and lock scope. A narrow host-aware recovery helper reconnects the current tab first, then selects an existing expected-host tab, with at most three recovery attempts before preserving the existing cleanup and outer retry behavior.

**Tech Stack:** Python 3.11, DrissionPage 4.1.1.4, pytest.

---

### Task 1: Normal SHEIN shared-login recovery

**Files:**
- Modify: `src/finance_crawler/auth.py`
- Test: `tests/test_shein_auth_serialization.py`

- [ ] Add a failing test proving the ZiNiao debugging-port attach uses `ChromiumOptions.existing_only()`.
- [ ] Add a failing test proving a disconnected SHEIN tab calls `reconnect(wait=1)` and continues cookie warm-up.
- [ ] Add a failing test proving a destroyed target is replaced by an existing `geiwohuo.com` tab.
- [ ] Run `py -3 -m pytest tests\test_shein_auth_serialization.py -q` and verify the new tests fail for missing behavior.
- [ ] Implement `is_browser_tab_connection_error()` and `recover_browser_tab()` in `auth.py` with host filtering and no cross-platform side effects.
- [ ] Attach with `existing_only()` and recover at most three times inside `_shein_shared_cookie_login_unlocked()`.
- [ ] Re-run the focused tests and verify they pass.

### Task 2: A1Y-A4Y Shenhe recovery

**Files:**
- Modify: `src/finance_crawler/platforms/shenhe_report_bill.py`
- Test: `tests/test_shenhe_report_bill.py`

- [ ] Add a failing test proving Shenhe startup uses `existing_only()` and reconnects the initial tab.
- [ ] Add a failing test proving browser fetch replaces a destroyed page with an existing `shenhe888.com` tab and reuses it.
- [ ] Add a failing test proving recovery exhaustion still triggers existing browser cleanup.
- [ ] Run `py -3 -m pytest tests\test_shenhe_report_bill.py -q` and verify the new tests fail for missing behavior.
- [ ] Add a small mutable Shenhe page holder and host-aware recovery calls while preserving the end-to-end auth slot.
- [ ] Re-run the focused tests and verify they pass.

### Task 3: Documentation and regression verification

**Files:**
- Modify: `project-md/context.md`
- Modify: `project-md/decisions.md`
- Modify: `project-md/tasks.md`
- Modify: `project-md/changelog.md`

- [ ] Record that native reconnect is SHEIN-only and does not replace existing account batching or cleanup behavior.
- [ ] Run focused SHEIN tests.
- [ ] Run `py -3 scripts\validate_tasks.py --env prod`.
- [ ] Run `py -3 -m pytest -q`.
- [ ] Review `git diff --check` and the final scoped diff.
- [ ] Sync the approved files to `E:\自动化\财务` without reverting unrelated changes.
- [ ] Publish a clean public snapshot to `amzzigz/getFullFianceData` for new-environment testing after sensitive scanning.
