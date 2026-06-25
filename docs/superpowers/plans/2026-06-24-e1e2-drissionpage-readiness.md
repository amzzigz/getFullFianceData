# E1E2 DrissionPage Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace E1E2 timing guesses with DrissionPage URL/document waits and seller API packet readiness.

**Architecture:** `open_bills_page()` owns listener lifecycle and navigation. It returns optional seller information parsed from the captured packet; the exporter falls back to `get_seller_info()` only when no usable packet is captured.

**Tech Stack:** Python, DrissionPage 4.1.1.4, pytest.

---

### Task 1: Define listener-driven readiness

**Files:**
- Modify: `tests/test_tiktok_email_income.py`
- Modify: `src/finance_crawler/platforms/tiktok_email_income.py`

- [ ] Write a failing test proving the listener starts before `page.get()`, URL/document waits run, the seller packet is parsed, and the listener stops.
- [ ] Run `py -3 -m pytest tests\test_tiktok_email_income.py::test_open_bills_page_uses_seller_packet_as_readiness_signal -q` and confirm failure.
- [ ] Implement packet parsing and listener lifecycle in `open_bills_page()`.
- [ ] Run the test again and confirm it passes.

### Task 2: Preserve compatibility fallback

**Files:**
- Modify: `tests/test_tiktok_email_income.py`
- Modify: `src/finance_crawler/platforms/tiktok_email_income.py`

- [ ] Write a failing test proving listener timeout returns no seller info and stops cleanly.
- [ ] Update the exporter to use captured seller info or call `get_seller_info()` as fallback.
- [ ] Run `py -3 -m pytest tests\test_tiktok_email_income.py -q`.

### Task 3: Verify and document

**Files:**
- Modify: `project-md/context.md`
- Modify: `project-md/tasks.md`
- Modify: `project-md/decisions.md`
- Modify: `project-md/changelog.md`

- [ ] Run `py -3 scripts\validate_tasks.py --env prod`.
- [ ] Run `py -3 -m pytest -q`.
- [ ] Run one local `tiktok_email_income` probe.
- [ ] Record that deployment-machine validation remains required.

