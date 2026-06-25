import main
import threading
import time
from finance_crawler.auth import AuthResult
from finance_crawler.models import TaskResult
from finance_crawler.platforms.tiktok_common import tiktok_download_poll_options
from finance_crawler.platforms.tiktok_withdrawals import is_tiktok_business_page_url


def test_filter_tasks_by_platforms_keeps_only_selected_platform():
    tasks = [
        {"id": "shein_funds", "platform": "shein"},
        {"id": "tiktok_sales_data", "platform": "tiktok"},
        {"id": "tiktok_fee_center", "platform": "tiktok"},
    ]

    filtered = main.filter_tasks_by_platforms(tasks, ["tiktok"])

    assert [task["id"] for task in filtered] == ["tiktok_sales_data", "tiktok_fee_center"]


def test_tiktok_multiple_tasks_use_account_batching():
    tasks = [
        {"id": "tiktok_sales_data", "platform": "tiktok"},
        {"id": "tiktok_fee_center", "platform": "tiktok"},
    ]

    assert main.should_batch_by_account(tasks)


def test_tiktok_multiple_tasks_use_shared_browser_batch():
    tasks = [
        {"id": "tiktok_withdrawals", "platform": "tiktok"},
        {"id": "tiktok_sales_data", "platform": "tiktok"},
    ]

    assert main.should_batch_tiktok_with_shared_browser(tasks)


def test_e1e2_platform_does_not_join_tiktok_shared_browser_batch():
    tasks = [
        {"id": "tiktok_withdrawals", "platform": "tiktok"},
        {"id": "tiktok_email_income", "platform": "E1E2"},
    ]

    assert not main.should_batch_tiktok_with_shared_browser(tasks)


def test_e1e2_jobs_run_serially_for_low_resource_ziniu(monkeypatch):
    tasks = [{"id": "tiktok_email_income", "platform": "E1E2", "account_source": "tiktok_email"}]
    config = type(
        "Config",
        (),
        {
            "runtime": {"request_timeout_seconds": 60, "login_timeout_seconds": 30, "retry_count": 1},
            "accounts": {"tiktok_email": ["TIKTOK-POP-E1", "TIKTOK-POP-E2-SL"]},
            "max_workers": lambda self: 3,
        },
    )()
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    def fake_run_one(config, task, account_name, request_timeout, login_timeout, max_attempts):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with active_lock:
            active -= 1
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "run_one_task_account_with_retry", fake_run_one)

    results = main.run_tasks(config, tasks, [])

    assert [result.account_name for result in results] == ["TIKTOK-POP-E1", "TIKTOK-POP-E2-SL"]
    assert max_active == 1


def test_e1e2_run_plan_shows_effective_serial_workers(capsys):
    tasks = [{"id": "tiktok_email_income", "platform": "E1E2", "account_source": "tiktok_email"}]
    jobs = [(tasks[0], "TIKTOK-POP-E1"), (tasks[0], "TIKTOK-POP-E2-SL")]
    config = type(
        "Config",
        (),
        {
            "env": "prod",
            "max_workers": lambda self: 3,
            "ziniu_auth_concurrency": lambda self: 1,
            "account_module_concurrency": lambda self: 2,
            "output_root": lambda self: "output",
        },
    )()

    main.print_run_plan(config, tasks, jobs, dry_run=True)

    out = capsys.readouterr().out
    assert "账号任务=2 | 并发=1 | 紫鸟鉴权并发=1" in out


def test_temu_jobs_run_serially_to_keep_account_retries_contiguous(monkeypatch):
    tasks = [{"id": "temu_fund_details", "platform": "temu", "account_source": "temu"}]
    config = type(
        "Config",
        (),
        {
            "runtime": {"request_timeout_seconds": 60, "login_timeout_seconds": 30, "retry_count": 1},
            "accounts": {"temu": ["B30", "B27"]},
            "max_workers": lambda self: 2,
        },
    )()
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    def fake_run_one(config, task, account_name, request_timeout, login_timeout, max_attempts):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with active_lock:
            active -= 1
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "run_one_task_account_with_retry", fake_run_one)

    results = main.run_tasks(config, tasks, [])

    assert [result.account_name for result in results] == ["B30", "B27"]
    assert max_active == 1


def test_temu_run_plan_shows_effective_serial_workers(capsys):
    tasks = [{"id": "temu_fund_details", "platform": "temu", "account_source": "temu"}]
    jobs = [(tasks[0], "B30"), (tasks[0], "B27")]
    config = type(
        "Config",
        (),
        {
            "env": "prod",
            "max_workers": lambda self: 2,
            "ziniu_auth_concurrency": lambda self: 1,
            "account_module_concurrency": lambda self: 1,
            "output_root": lambda self: "output",
        },
    )()

    main.print_run_plan(config, tasks, jobs, dry_run=True)

    out = capsys.readouterr().out
    assert "账号任务=2 | 并发=1 | 紫鸟鉴权并发=1" in out


def test_mixed_temu_and_aliexpress_jobs_keep_non_temu_concurrency(monkeypatch):
    tasks = [
        {"id": "temu_fund_details", "platform": "temu", "account_source": "temu"},
        {"id": "aliexpress_finance", "platform": "aliexpress", "account_source": "aliexpress"},
    ]
    config = type(
        "Config",
        (),
        {
            "runtime": {"request_timeout_seconds": 60, "login_timeout_seconds": 30, "retry_count": 1},
            "accounts": {"temu": ["B30"], "aliexpress": ["D1"]},
            "max_workers": lambda self: 2,
        },
    )()
    temu_active = threading.Event()
    aliexpress_overlapped = threading.Event()

    def fake_run_one(config, task, account_name, request_timeout, login_timeout, max_attempts):
        if task["platform"] == "temu":
            temu_active.set()
            time.sleep(0.08)
            temu_active.clear()
        else:
            if temu_active.wait(0.05) and temu_active.is_set():
                aliexpress_overlapped.set()
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "run_one_task_account_with_retry", fake_run_one)

    results = main.run_tasks(config, tasks, [])

    assert len(results) == 2
    assert aliexpress_overlapped.is_set()


def test_mixed_temu_tasks_do_not_force_global_worker_count_to_one():
    tasks = [
        {"id": "temu_fund_details", "platform": "temu", "runner": "temu.fund_details"},
        {"id": "aliexpress_finance", "platform": "aliexpress", "runner": "aliexpress.finance"},
    ]
    config = type("Config", (), {"max_workers": lambda self: 3})()

    assert main.job_worker_count(config, tasks, 3) == 3


def test_mixed_temu_jobs_are_serial_with_each_other(monkeypatch):
    tasks = [
        {"id": "temu_fund_details", "platform": "temu", "account_source": "temu"},
        {"id": "aliexpress_finance", "platform": "aliexpress", "account_source": "aliexpress"},
    ]
    config = type(
        "Config",
        (),
        {
            "runtime": {"request_timeout_seconds": 60, "login_timeout_seconds": 30, "retry_count": 1},
            "accounts": {"temu": ["B30", "B27"], "aliexpress": ["D1"]},
            "max_workers": lambda self: 3,
        },
    )()
    active_temu = 0
    max_active_temu = 0
    lock = threading.Lock()

    def fake_run_one(config, task, account_name, request_timeout, login_timeout, max_attempts):
        nonlocal active_temu, max_active_temu
        if task["platform"] == "temu":
            with lock:
                active_temu += 1
                max_active_temu = max(max_active_temu, active_temu)
            time.sleep(0.02)
            with lock:
                active_temu -= 1
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "run_one_task_account_with_retry", fake_run_one)

    main.run_tasks(config, tasks, [])

    assert max_active_temu == 1


def test_tiktok_business_page_url_accepts_us_seller_subdomain():
    assert is_tiktok_business_page_url("https://seller.us.tiktokshopglobalselling.com/homepage")
    assert is_tiktok_business_page_url("https://seller.tiktokshopglobalselling.com/homepage")
    assert not is_tiktok_business_page_url("https://seller.us.tiktokshopglobalselling.com/account/login")


def test_tiktok_download_poll_options_are_shorter_by_default():
    assert tiktok_download_poll_options({}) == (12, 4)


def test_tiktok_download_poll_options_allow_task_override():
    assert tiktok_download_poll_options({"download_attempts": 3, "download_interval_seconds": 2}) == (3, 2)


def test_result_output_paths_splits_multi_file_outputs():
    result = TaskResult(
        task_id="tiktok_fee_center",
        platform="tiktok",
        account_name="C1",
        success=True,
        message="done",
        output_path="a.xlsx; b.xlsx; c.xlsx",
    )

    assert main.result_output_paths(result) == ["a.xlsx", "b.xlsx", "c.xlsx"]
    assert main.result_output_count([result]) == 3


def test_result_output_paths_prefers_structured_outputs_over_truncated_display():
    result = TaskResult(
        task_id="temu_fund_details",
        platform="temu",
        account_name="B1",
        success=True,
        message="done",
        output_path="a.xlsx; b.xlsx; c.xlsx ...",
        data={"outputs": ["a.xlsx", "b.xlsx", "c.xlsx", "d.xlsx"]},
    )

    assert main.result_output_paths(result) == ["a.xlsx", "b.xlsx", "c.xlsx", "d.xlsx"]
    assert main.result_output_count([result]) == 4


def test_result_output_count_ignores_empty_outputs():
    results = [
        TaskResult("one", "tiktok", "C1", True, "done", output_path="a.csv"),
        TaskResult("two", "tiktok", "C1", True, "done"),
    ]

    assert main.result_output_count(results) == 1


def test_no_data_message_is_reported_as_no_data_status():
    result = TaskResult(
        "shein_platform_fees",
        "shein",
        "SPP1",
        False,
        "接口失败 https://example/export: code=gsfs98008 msg=暂无数据可导出",
    )

    normalized = main.normalize_result_status(result)

    assert normalized.success
    assert normalized.status == "no_data"
    assert main.result_log_status(normalized) == "无数据"


def test_successful_skip_message_is_reported_as_no_data_status():
    result = TaskResult(
        "shein_funds",
        "shein",
        "A21",
        True,
        "SHEIN 资金管理无记录，已跳过导出。",
    )

    normalized = main.normalize_result_status(result)

    assert normalized.success
    assert normalized.status == "no_data"


def test_result_status_counts_split_success_no_data_and_failed():
    results = [
        TaskResult("ok", "shein", "A1", True, "done"),
        TaskResult("empty", "shein", "A2", True, "empty", status="no_data"),
        TaskResult("failed", "shein", "A3", False, "boom"),
    ]

    assert main.result_status_counts(results) == {"success": 1, "no_data": 1, "failed": 1}


def test_result_detail_lines_show_no_data_and_failures_for_business_users():
    results = [
        TaskResult("empty", "shein", "A2", True, "无记录", status="no_data"),
        TaskResult("failed", "shein", "A3", False, "权限不足"),
    ]

    lines = main.result_detail_lines(results)

    assert "无数据明细:" in lines
    assert "  - A2 | empty | 无记录" in lines
    assert "失败明细:" in lines
    assert "  - A3 | failed | 权限不足" in lines


def test_resolve_accounts_matches_e1_inside_tiktok_pop_name():
    accounts = ["TIKTOK-POP-E1", "TIKTOK-POP-E2-SL", "C1主账号"]

    assert main.resolve_accounts(accounts, ["E1"]) == ["TIKTOK-POP-E1"]
    assert main.resolve_accounts(accounts, ["E2"]) == ["TIKTOK-POP-E2-SL"]


def test_resolve_accounts_keeps_stable_account_specs_and_matches_label():
    account = {
        "label": "B22/B12/B13-LY",
        "platform_id": 149,
        "siteId": 391,
        "store_username": "demo-store-user",
    }

    assert main.account_display_name(account) == "B22/B12/B13-LY"
    assert main.resolve_accounts([account], ["B22"]) == [account]
    assert main.resolve_accounts([account], ["demo-store-user"]) == [account]


def test_shein_account_batch_prepares_shared_auth_once_and_runs_modules_concurrently(monkeypatch):
    tasks = [
        {
            "id": "shein_sales_ledger",
            "platform": "shein",
            "runner": "mils.sales_ledger",
            "target_page": "https://sso.geiwohuo.com/#/mils/report",
        },
        {
            "id": "shein_merchant_billing",
            "platform": "shein",
            "runner": "gsfs.merchant_billing",
            "target_page": "https://sso.geiwohuo.com/#/gsfs/finance/reportOrder/dualMode",
        },
    ]
    config = type(
        "Config",
        (),
        {
            "runtime": {"account_module_concurrency": 2},
            "desktop_auth_path": lambda self: "tools/ziniu_auth_login_extracted.py",
            "output_root": lambda self: "output",
            "account_module_concurrency": lambda self: 2,
        },
    )()
    shared = AuthResult(True, "success", "A21", platform="shein", cookie="sso=1; session=2", user_agent="ua")
    prepared = []
    received = []

    def fake_prepare(account_name, account_tasks, auth_path, login_timeout):
        prepared.append((account_name, [task["id"] for task in account_tasks]))
        return shared

    def fake_run_one(config, task, account_name, request_timeout, login_timeout):
        received.append((task["id"], task.get("_auth_result")))
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "prepare_shared_shein_auth_for_batch", fake_prepare)
    monkeypatch.setattr(main, "run_one_task_account", fake_run_one)

    results = main.run_account_task_batch_with_retry(config, "A21", tasks, 60, 30, 1)

    assert [result.task_id for result in results] == ["shein_sales_ledger", "shein_merchant_billing"]
    assert prepared == [("A21", ["shein_sales_ledger", "shein_merchant_billing"])]
    assert received == [("shein_sales_ledger", shared), ("shein_merchant_billing", shared)]


def test_shein_account_batch_falls_back_to_module_auth_when_shared_auth_fails(monkeypatch):
    tasks = [
        {
            "id": "shein_sales_ledger",
            "platform": "shein",
            "runner": "mils.sales_ledger",
            "target_page": "https://sso.geiwohuo.com/#/mils/report",
        },
        {
            "id": "shein_merchant_billing",
            "platform": "shein",
            "runner": "gsfs.merchant_billing",
            "target_page": "https://sso.geiwohuo.com/#/gsfs/finance/reportOrder/dualMode",
        },
    ]
    config = type(
        "Config",
        (),
        {
            "runtime": {"account_module_concurrency": 2},
            "desktop_auth_path": lambda self: "tools/ziniu_auth_login_extracted.py",
            "output_root": lambda self: "output",
            "account_module_concurrency": lambda self: 2,
        },
    )()
    received = []

    monkeypatch.setattr(
        main,
        "prepare_shared_shein_auth_for_batch",
        lambda *_args, **_kwargs: AuthResult(False, "page disconnected", "A21", platform="shein"),
    )

    def fake_run_one(config, task, account_name, request_timeout, login_timeout):
        received.append((task["id"], task.get("_auth_result")))
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "run_one_task_account", fake_run_one)

    results = main.run_account_task_batch_with_retry(config, "A21", tasks, 60, 30, 1)

    assert [result.task_id for result in results] == ["shein_sales_ledger", "shein_merchant_billing"]
    assert received == [("shein_sales_ledger", None), ("shein_merchant_billing", None)]


def test_shein_account_batch_forces_serial_modules_after_shared_auth_failure(monkeypatch):
    tasks = [
        {
            "id": "shein_sales_ledger",
            "platform": "shein",
            "runner": "mils.sales_ledger",
            "target_page": "https://sso.geiwohuo.com/#/mils/report",
        },
        {
            "id": "shein_merchant_billing",
            "platform": "shein",
            "runner": "gsfs.merchant_billing",
            "target_page": "https://sso.geiwohuo.com/#/gsfs/finance/reportOrder/dualMode",
        },
    ]
    config = type(
        "Config",
        (),
        {
            "runtime": {"account_module_concurrency": 2},
            "desktop_auth_path": lambda self: "tools/ziniu_auth_login_extracted.py",
            "output_root": lambda self: "output",
            "account_module_concurrency": lambda self: 2,
        },
    )()
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    monkeypatch.setattr(
        main,
        "prepare_shared_shein_auth_for_batch",
        lambda *_args, **_kwargs: AuthResult(False, "page disconnected", "A21", platform="shein"),
    )

    def fake_run_one(config, task, account_name, request_timeout, login_timeout):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with active_lock:
            active -= 1
        return TaskResult(task["id"], task["platform"], account_name, True, "ok")

    monkeypatch.setattr(main, "run_one_task_account", fake_run_one)

    results = main.run_account_task_batch_with_retry(config, "A21", tasks, 60, 30, 1)

    assert [result.task_id for result in results] == ["shein_sales_ledger", "shein_merchant_billing"]
    assert max_active == 1


def test_final_failed_rerun_replaces_failed_result(monkeypatch):
    tasks = [{"id": "shein_sales_ledger", "platform": "shein", "task_name": "SHEIN 销售台账"}]
    config = type(
        "Config",
        (),
        {
            "runtime": {
                "final_failed_rerun_count": 1,
                "request_timeout_seconds": 60,
                "login_timeout_seconds": 30,
                "retry_count": 1,
            },
            "final_failed_rerun_count": lambda self: 1,
        },
    )()
    failed = TaskResult("shein_sales_ledger", "shein", "A21", False, "紫鸟鉴权失败")
    calls = []

    def fake_run_one(config, task, account_name, request_timeout, login_timeout):
        calls.append((task["id"], account_name))
        return TaskResult(task["id"], task["platform"], account_name, True, "补跑成功")

    monkeypatch.setattr(main, "run_one_task_account", fake_run_one)

    results = main.rerun_failed_results(config, tasks, [failed])

    assert calls == [("shein_sales_ledger", "A21")]
    assert results[0].success
    assert results[0].message == "补跑成功"
    assert results[0].data["final_failed_rerun"] is True
    assert results[0].data["previous_message"] == "紫鸟鉴权失败"


def test_final_failed_rerun_skips_no_data_results(monkeypatch):
    tasks = [{"id": "shein_platform_fees", "platform": "shein", "task_name": "SHEIN 平台费用"}]
    config = type(
        "Config",
        (),
        {
            "runtime": {"final_failed_rerun_count": 1},
            "final_failed_rerun_count": lambda self: 1,
        },
    )()
    no_data = TaskResult(
        "shein_platform_fees",
        "shein",
        "SPP1",
        True,
        "暂无数据可导出",
        status="no_data",
    )

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("no_data result should not be rerun")

    monkeypatch.setattr(main, "run_one_task_account", forbidden_run)

    assert main.rerun_failed_results(config, tasks, [no_data]) == [no_data]
