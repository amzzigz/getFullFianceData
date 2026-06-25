# TEMU Tab Reconnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover transient TEMU DrissionPage tab disconnections without restarting a healthy ZiNiao browser session.

**Architecture:** Keep recovery inside the TEMU browser startup loop. Reconnect the current tab with DrissionPage's native API first, then select an existing TEMU tab if the original target was destroyed; browser-level connection failures continue through the existing cleanup and retry path.

**Tech Stack:** Python 3.11, DrissionPage 4.1.1.4, pytest.

---

### Task 1: Replace the simulated Chromium rebind

**Files:**
- Modify: `src/finance_crawler/platforms/temu_fund_details.py`
- Test: `tests/test_temu_login.py`

- [x] Add a failing test requiring `ChromiumOptions.existing_only()`.
- [x] Add a failing test requiring `tab.reconnect(wait=1)` without reconstructing `Chromium`.
- [x] Add a failing test for selecting a replacement TEMU tab after `TargetNotFoundError`.
- [x] Implement the minimal TEMU-only recovery logic.
- [x] Run `py -3.11 -m pytest tests\test_temu_login.py -q`.

### Task 2: Regression verification

**Files:**
- Modify: `project-md/context.md`
- Modify: `project-md/decisions.md`
- Modify: `project-md/tasks.md`
- Modify: `project-md/changelog.md`

- [x] Record the official DrissionPage recovery semantics.
- [x] Run TEMU and scheduler regression tests.
- [x] Run the complete pytest suite.
- [x] Review the final diff and leave the changes unpushed.
