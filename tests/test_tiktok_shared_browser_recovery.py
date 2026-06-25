import sys
import types
from pathlib import Path

import pytest

import main
from finance_crawler.models import TaskResult
from finance_crawler.platforms import tiktok_withdrawals


class BatchConfig:
    runtime = {
        "request_timeout_seconds": 60,
        "login_timeout_seconds": 30,
        "retry_count": 1,
    }

    def desktop_auth_path(self):
        return Path("tools/ziniu_auth_login_extracted.py")

    def output_root(self):
        return Path("output")

    def final_failed_rerun_count(self):
        return 1


def make_tasks():
    return [
        {
            "id": "tiktok_withdrawals",
            "platform": "tiktok",
            "runner": "tiktok.withdrawals",
            "task_name": "TK 提现明细",
        },
        {
            "id": "tiktok_sales_data",
            "platform": "tiktok",
            "runner": "tiktok.sales_data",
            "task_name": "TK 销售数据",
        },
    ]


def test_start_tiktok_browser_stops_browser_when_page_disconnects_before_context_return(monkeypatch):
    requests = []
    stop_attempts = 0

    class FakeHelper:
        def ensure_client_online(self):
            return True, ""

        def get_shop_info(self, account_name):
            return {"browserOauth": f"oauth-{account_name}"}, ""

        def build_start_browser_payload(self, info):
            return {"action": "startBrowser"}

        def send_http(self, payload):
            nonlocal stop_attempts
            requests.append(payload)
            if payload["action"] == "startBrowser":
                return {
                    "statusCode": "0",
                    "debuggingPort": 9222,
                    "browserOauth": "oauth-C1",
                }
            stop_attempts += 1
            if stop_attempts == 1:
                return {"statusCode": "1", "message": "busy"}
            return {"statusCode": "0"}

    class DisconnectedPage:
        @property
        def url(self):
            raise RuntimeError("与页面的连接已断开")

        def quit(self):
            pass

    class FakeBrowser:
        def new_tab(self, target_url):
            return DisconnectedPage()

    class FakeOptions:
        def set_local_port(self, port):
            return self

    fake_drission = types.SimpleNamespace(
        Chromium=lambda options: FakeBrowser(),
        ChromiumOptions=FakeOptions,
    )
    monkeypatch.setitem(sys.modules, "DrissionPage", fake_drission)
    monkeypatch.setattr(tiktok_withdrawals, "load_ziniu_helper", lambda auth_path: FakeHelper())

    with pytest.raises(RuntimeError, match="连接已断开"):
        tiktok_withdrawals.start_tiktok_browser(
            "C1",
            Path("tools/ziniu_auth_login_extracted.py"),
            30,
        )

    stop_requests = [item for item in requests if item.get("action") == "stopBrowser"]
    assert len(stop_requests) == 2
    assert all(item["browserOauth"] == "oauth-C1" for item in stop_requests)


def test_stop_tiktok_browser_session_does_not_log_browser_oauth(capsys):
    calls = []

    class FakeHelper:
        def send_http(self, payload):
            calls.append(payload)
            return {"statusCode": "1", "message": "busy"}

    stopped = tiktok_withdrawals.stop_tiktok_browser_session(
        FakeHelper(),
        "secret-oauth-token",
    )

    output = capsys.readouterr().out
    assert stopped is False
    assert len(calls) == 2
    assert "stopBrowser 失败" in output
    assert "secret-oauth-token" not in output


def test_tiktok_account_batch_retries_shared_browser_startup(monkeypatch):
    tasks = make_tasks()
    starts = []
    executed = []
    ctx = object()

    def fake_start(account_name, auth_path, login_timeout):
        starts.append(account_name)
        if len(starts) == 1:
            raise RuntimeError("与页面的连接已断开")
        return ctx

    def fake_run(task, account_name, period, received_ctx, output_root, request_timeout):
        executed.append((task["id"], received_ctx))
        return TaskResult(task["id"], "tiktok", account_name, True, "ok")

    monkeypatch.setattr(main, "start_tiktok_browser", fake_start)
    monkeypatch.setattr(main, "run_one_tiktok_task_with_ctx", fake_run)
    monkeypatch.setattr(main, "close_tiktok_browser", lambda received_ctx: None)

    results = main.run_tiktok_account_task_batch_with_retry(
        BatchConfig(),
        "C1",
        tasks,
        request_timeout=60,
        login_timeout=30,
        max_attempts=2,
    )

    assert starts == ["C1", "C1"]
    assert executed == [
        ("tiktok_withdrawals", ctx),
        ("tiktok_sales_data", ctx),
    ]
    assert all(result.success for result in results)


def test_tiktok_account_batch_startup_failure_returns_real_task_ids(monkeypatch):
    tasks = make_tasks()
    monkeypatch.setattr(
        main,
        "start_tiktok_browser",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("与页面的连接已断开")),
    )
    monkeypatch.setattr(main, "close_tiktok_browser", lambda received_ctx: None)

    results = main.run_tiktok_account_task_batch_with_retry(
        BatchConfig(),
        "C1",
        tasks,
        request_timeout=60,
        login_timeout=30,
        max_attempts=2,
    )

    assert [result.task_id for result in results] == [
        "tiktok_withdrawals",
        "tiktok_sales_data",
    ]
    assert all("TK账号共享浏览器启动失败" in result.message for result in results)
    assert all(result.data["tiktok_shared_startup_failure"] is True for result in results)


def test_tiktok_shared_startup_failures_enter_final_failed_rerun(monkeypatch):
    tasks = make_tasks()
    monkeypatch.setattr(
        main,
        "start_tiktok_browser",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("与页面的连接已断开")),
    )
    monkeypatch.setattr(main, "close_tiktok_browser", lambda received_ctx: None)
    failed = main.run_tiktok_account_task_batch_with_retry(
        BatchConfig(),
        "C1",
        tasks,
        request_timeout=60,
        login_timeout=30,
        max_attempts=1,
    )
    batch_calls = []

    def fake_batch(config, account_name, account_tasks, request_timeout, login_timeout, max_attempts):
        batch_calls.append((account_name, [task["id"] for task in account_tasks]))
        return [
            TaskResult(task["id"], "tiktok", account_name, True, "补跑成功")
            for task in account_tasks
        ]

    monkeypatch.setattr(main, "run_tiktok_account_task_batch_with_retry", fake_batch)

    results = main.rerun_failed_results(BatchConfig(), tasks, failed)

    assert batch_calls == [
        ("C1", ["tiktok_withdrawals", "tiktok_sales_data"]),
    ]
    assert all(result.success for result in results)
