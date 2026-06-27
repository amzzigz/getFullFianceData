# Panel Task Applicability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make control-panel module names business-readable and prevent account/period-incompatible modules from being submitted.

**Architecture:** Keep `config/tasks.json` and loaded account pools as the only source of truth. Add small Python helpers for task-name translation and submission validation, then mirror the same applicability rule in the existing embedded JavaScript for immediate UI feedback.

**Tech Stack:** Python 3.11, pytest, standard-library HTTP server, vanilla JavaScript.

---

### Task 1: Translate task IDs in panel output

**Files:**
- Modify: `src/finance_crawler/control_panel.py:121-201`
- Test: `tests/test_control_panel.py`

- [ ] **Step 1: Write failing translation tests**

```python
def test_summarize_run_log_uses_business_task_name():
    summary = summarize_run_log(
        "失败明细:\n  - A20 | shein_platform_fees | 页面超时",
        {"shein_platform_fees": "SHEIN 平台费用"},
    )
    assert summary["failed_items"][0]["task"] == "SHEIN 平台费用"


def test_business_log_lines_uses_business_task_name():
    lines = business_log_lines(
        "失败明细:\n  - A20 | shein_platform_fees | 页面超时",
        {"shein_platform_fees": "SHEIN 平台费用"},
    )
    assert "  - A20 | SHEIN 平台费用 | 页面超时" in lines
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: FAIL because `summarize_run_log()` and `business_log_lines()` do not accept a task-name mapping.

- [ ] **Step 3: Add optional task-name translation**

```python
def _task_display_name(task_id: str, task_names: dict[str, str] | None) -> str:
    return (task_names or {}).get(task_id, task_id)


def _parse_detail_line(
    line: str,
    task_names: dict[str, str] | None = None,
) -> dict[str, str] | None:
    # Preserve the current regex and message cleanup.
    return {
        "account": match.group(1).strip(),
        "task": _task_display_name(match.group(2).strip(), task_names),
        "message": _clean_message(match.group(3).strip()),
    }
```

Pass the optional mapping through `summarize_run_log()` and `business_log_lines()`. For business-log detail lines, parse and rebuild `  - 账号 | 中文模块 | 信息`; leave original disk logs untouched.

- [ ] **Step 4: Use environment task names for run summaries and business logs**

```python
def task_name_map(env: str, project_root: Path) -> dict[str, str]:
    return {
        str(task["id"]): str(task["name"])
        for task in panel_options(env, project_root)["tasks"]
    }
```

Add a `task_names` field to `PanelRun`. Populate it from the selected environment only for manual finance runs; bat runs keep an empty mapping. Use the saved mapping in `PanelRunner._run_process()` and `ControlPanelHandler`'s `/business-log` response so bat execution never depends on finance configuration.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: PASS.

### Task 2: Validate account, module, and period combinations

**Files:**
- Modify: `src/finance_crawler/control_panel.py:216-264,399-419`
- Test: `tests/test_control_panel.py`

- [ ] **Step 1: Write failing applicability tests**

```python
def test_validate_run_selection_rejects_wrong_account_source():
    options = {
        "tasks": [{"id": "f1", "name": "F1", "account_source": "shein_f1_f20", "frequency": ["monthly"]}],
        "accounts": {"shein": ["A20"], "shein_f1_f20": ["F1"]},
    }
    with pytest.raises(RuntimeError, match="不适用于所选账号"):
        validate_run_selection(["f1"], ["A20"], "monthly", options)


def test_validate_run_selection_rejects_wrong_period():
    options = {
        "tasks": [{"id": "bill", "name": "商家账单", "account_source": "shein", "frequency": ["monthly"]}],
        "accounts": {"shein": ["A20"]},
    }
    with pytest.raises(RuntimeError, match="不支持周度"):
        validate_run_selection(["bill"], ["A20"], "weekly", options)
```

Also cover duplicate account membership across `shein` and `shein_main_12`, plus empty task/account selections.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: FAIL because `validate_run_selection()` does not exist.

- [ ] **Step 3: Implement the minimal validator**

```python
PERIOD_NAMES = {"daily": "日度", "weekly": "周度", "monthly": "月度"}


def validate_run_selection(
    task_ids: list[str],
    selected_accounts: list[str],
    period: str,
    options: dict[str, Any],
) -> None:
    if not task_ids:
        raise RuntimeError("请至少选择一个模块。")
    if not selected_accounts:
        raise RuntimeError("请至少选择一个账号。")
    tasks = {str(task["id"]): task for task in options["tasks"]}
    memberships = {
        account: {source for source, names in options["accounts"].items() if account in names}
        for account in selected_accounts
    }
    for task_id in task_ids:
        task = tasks.get(task_id)
        if not task:
            raise RuntimeError(f"未找到模块：{task_id}")
        if period not in task["frequency"]:
            raise RuntimeError(f"{task['name']} 不支持{PERIOD_NAMES.get(period, period)}。")
        if not any(task["account_source"] in sources for sources in memberships.values()):
            raise RuntimeError(f"{task['name']} 不适用于所选账号。")
```

- [ ] **Step 4: Call validation before building the finance command**

In `PanelRunner.start_run()`, load `panel_options(env, self.project_root)`, validate manual-run selections, then call `build_finance_command()`. Bat schedules remain unchanged.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: PASS.

### Task 3: Disable unavailable modules in the page

**Files:**
- Modify: `src/finance_crawler/control_panel.py:749-797,813-839,942-951`
- Test: `tests/test_control_panel.py`

- [ ] **Step 1: Write failing page-contract tests**

```python
def test_panel_recalculates_modules_from_accounts_and_period():
    assert "function refreshTaskAvailability()" in INDEX_HTML
    assert "document.getElementById('accounts').addEventListener('change', refreshTaskAvailability)" in INDEX_HTML
    assert "document.getElementById('period').addEventListener('change', refreshTaskAvailability)" in INDEX_HTML
    assert "input.disabled" in INDEX_HTML


def test_select_all_tasks_skips_disabled_modules():
    assert "if (!x.disabled) x.checked = checked" in INDEX_HTML
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: FAIL because the page does not recalculate module availability.

- [ ] **Step 3: Decouple account rendering from checked modules**

Render accounts from all `account_source` values belonging to the selected platform's tasks. Keep the existing account-name deduplication and checked-by-default behavior.

- [ ] **Step 4: Add availability calculation**

```javascript
function refreshTaskAvailability() {
  const period = document.getElementById('period').value;
  const selected = new Set(checkedValues('accounts'));
  document.querySelectorAll('#tasks input').forEach(input => {
    const task = options.tasks.find(item => item.id === input.value);
    const sourceAccounts = options.accounts[task.account_source] || [];
    const accountMatches = sourceAccounts.some(account => selected.has(account));
    const periodMatches = (task.frequency || []).includes(period);
    input.disabled = !accountMatches || !periodMatches;
    if (input.disabled) input.checked = false;
  });
}
```

Call it after rendering accounts and when account or period selections change.

- [ ] **Step 5: Make select-all and start behavior safe**

Update `setChecks('tasks', true)` to skip disabled inputs. Before posting, show a business-readable error if no modules or no accounts are selected; backend validation remains authoritative.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: PASS.

### Task 4: Regression verification and project memory

**Files:**
- Modify: `project-md/context.md`
- Modify: `project-md/tasks.md`
- Modify: `project-md/changelog.md`

- [ ] **Step 1: Run control-panel tests**

Run: `py -3.11 -m pytest tests/test_control_panel.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run the full test suite**

Run: `py -3.11 -m pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Check the diff**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only the control panel, its tests, and project Markdown are modified.

- [ ] **Step 4: Record the behavior**

Document that panel display names come from `task_name`, account/module applicability comes from `account_source`, period applicability comes from `frequency`, and validation exists in both UI and backend.

- [ ] **Step 5: Commit implementation**

```bash
git add src/finance_crawler/control_panel.py tests/test_control_panel.py project-md/context.md project-md/tasks.md project-md/changelog.md docs/superpowers/plans/2026-06-27-panel-task-applicability.md
git commit -m "fix(panel): constrain account modules"
```
