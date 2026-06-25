# TEMU Browser Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent concurrent TEMU account browser sessions from disconnecting each other and make failed browser cleanup observable and retryable.

**Architecture:** Keep the existing TEMU export behavior, but hold the shared ZiNiao auth slot for the complete account lifecycle from `startBrowser` through export and `stopBrowser`. Reuse the existing Shenhe wrapper pattern so standalone browser startup remains protected without nesting the non-reentrant slot. Add a small stop helper that validates `statusCode`, retries once with a short delay, and avoids logging browser credentials.

**Tech Stack:** Python 3.11, pytest, DrissionPage, ZiNiao local HTTP API.

---

### Task 1: Add regression coverage

**Files:**
- Modify: `tests/test_temu_login.py`

- [x] Add a test that asserts `export_temu_fund_details()` keeps `ziniu_auth_slot()` held during startup, export work, and browser close.
- [x] Run the new test and verify it fails because the current export function does not hold the slot.
- [x] Add a test that asserts a failed `stopBrowser` response waits and retries once.
- [x] Run the new test and verify it fails because the TEMU stop helper does not exist.

### Task 2: Serialize the complete TEMU browser lifecycle

**Files:**
- Modify: `src/finance_crawler/platforms/temu_fund_details.py`
- Test: `tests/test_temu_login.py`

- [x] Add `auth_slot_held` support to `start_temu_browser()` using the established Shenhe recursion pattern.
- [x] Wrap `export_temu_fund_details()` in `ziniu_auth_slot()` and move the existing body to `_export_temu_fund_details_unlocked()`.
- [x] Call `start_temu_browser(..., auth_slot_held=True)` from the unlocked exporter so the slot remains held through `finally`.
- [x] Run the lifecycle regression test and verify it passes.

### Task 3: Harden TEMU browser cleanup

**Files:**
- Modify: `src/finance_crawler/platforms/temu_fund_details.py`
- Test: `tests/test_temu_login.py`

- [x] Add `stop_temu_browser_session()` that requires `statusCode=0`, retries at most twice, and sleeps between attempts.
- [x] Use the helper in both startup-failure cleanup and normal close.
- [x] Run the cleanup regression test and verify it passes.

### Task 4: Record and verify

**Files:**
- Modify: `project-md/context.md`
- Modify: `project-md/tasks.md`
- Modify: `project-md/decisions.md`
- Modify: `project-md/changelog.md`

- [x] Record that `run_20260624_223608.log` disproved login-only serialization for TEMU.
- [x] Mark the prior “release after userInfo” decision as superseded by end-to-end account serialization.
- [x] Run `py -3.11 -m pytest tests\test_temu_login.py tests\test_main_platform_batch.py -q`.
- [x] Run the complete test suite.
- [x] Commit and push the isolated branch to `origin/main`.
