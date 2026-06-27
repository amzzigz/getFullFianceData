from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from datetime import datetime

from finance_crawler.control_panel import (
    INDEX_HTML,
    PanelRunner,
    PanelSchedule,
    build_schedule_run_name,
    build_bat_command,
    build_finance_command,
    business_log_line,
    business_log_lines,
    dedupe_account_names,
    finalize_run_summary,
    prepare_bat_for_run,
    save_bat_file,
    save_bat_file_bytes,
    schedule_due_key,
    summarize_run_log,
)


def test_build_finance_command_repeats_selected_filters(tmp_path):
    command = build_finance_command(
        project_root=tmp_path,
        env="prod",
        task_ids=["temu_fund_details", "tiktok_sales_data"],
        accounts=["B2", "B27/B28/B29-主账号-CT"],
        shops=["B2"],
        period="weekly",
        diagnose=True,
        python_executable=sys.executable,
    )

    assert command[:3] == [sys.executable, "-u", str(tmp_path / "main.py")]
    assert command.count("--task") == 2
    assert command.count("--account") == 2
    assert command.count("--shop") == 1
    assert "--diagnose" in command
    assert command[command.index("--env") + 1] == "prod"
    assert command[command.index("--period") + 1] == "weekly"


def test_dedupe_account_names_keeps_first_order():
    accounts = {
        "shein_main_12": ["A1", "A2", "A3"],
        "shein": ["A1", "A2", "A3"],
        "shein_f1_f20": ["F1", "F20"],
    }

    assert dedupe_account_names(accounts, ["shein_main_12", "shein", "shein_f1_f20"]) == ["A1", "A2", "A3", "F1", "F20"]


def test_build_bat_command_uses_cmd_for_windows_batch(tmp_path):
    bat_path = tmp_path / "nightly.bat"

    assert build_bat_command(bat_path) == ["cmd.exe", "/d", "/c", str(bat_path)]


def test_save_bat_file_stores_copy_under_panel_directory(tmp_path):
    saved = save_bat_file(tmp_path, "夜间总调.bat", "@echo off\necho ok")

    assert saved.parent == tmp_path / "output" / "panel"
    assert saved.suffix == ".bat"
    assert saved.read_text(encoding="utf-8") == "@echo off\necho ok"


def test_save_bat_file_bytes_preserves_original_encoding(tmp_path):
    raw = b"@echo off\r\ncd /d E:\\\xd7\xd4\xb6\xaf\xbb\xaf\\\xb2\xc6\xce\xf1\r\n"

    saved = save_bat_file_bytes(tmp_path, "nightly.bat", raw)

    assert saved.read_bytes() == raw


def test_run_control_panel_cli_passes_project_root(monkeypatch, tmp_path):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_control_panel.py"
    spec = importlib.util.spec_from_file_location("run_control_panel_script", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []

    def fake_run_control_panel(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(module, "run_control_panel", fake_run_control_panel)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_control_panel.py",
            "--port",
            "8879",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert module.main() == 0
    assert calls == [{"host": "127.0.0.1", "port": 8879, "project_root": tmp_path.resolve()}]


def test_control_panel_uses_main_tabs_for_logs_and_schedules():
    aside_html = INDEX_HTML.split("</aside>", 1)[0]
    section_html = INDEX_HTML.split("<section>", 1)[1]

    assert 'data-tab="logs"' in INDEX_HTML
    assert 'data-tab="schedules"' in INDEX_HTML
    assert 'id="logsTab"' in INDEX_HTML
    assert 'id="schedulesTab"' in INDEX_HTML
    assert "定时执行" not in aside_html
    assert "定时计划" not in aside_html
    assert "定时执行" in section_html
    assert "定时计划" in section_html


def test_schedule_ui_uses_uploaded_bat_instead_of_left_filters():
    assert 'id="batDropZone"' in INDEX_HTML
    assert 'id="batFileInput"' in INDEX_HTML
    assert "/api/bat-files" in INDEX_HTML
    assert "content_b64" in INDEX_HTML
    assert "payload: currentPayload()" not in INDEX_HTML


def test_control_panel_shows_api_errors_to_user():
    assert "showError" in INDEX_HTML
    assert "已有任务正在运行" in INDEX_HTML


def test_business_log_line_hides_technical_noise():
    assert business_log_line("[auth] checking webdriver endpoint...") is None
    assert business_log_line("与页面的连接已断开。版本: 4.1.1.4") == "浏览器连接中断，程序已尝试恢复或重试。"
    assert business_log_line("TEMU 登录超时: https://agentseller.temu.com/") == "账号登录未完成，可能需要人工确认登录状态。"
    assert business_log_line("[完成] TEMU 资金明细 | B2 | TEMU 资金明细完成，店铺数 6，文件数 24") == (
        "完成：TEMU 资金明细 | B2 | TEMU 资金明细完成，店铺数 6，文件数 24"
    )


def test_business_log_lines_falls_back_to_plain_bat_output():
    lines = business_log_lines(
        "\n".join(
            [
                "面板启动命令: cmd.exe /c test.bat",
                "panel schedule test start",
                "ok",
                "panel schedule test done",
            ]
        )
    )

    assert "panel schedule test start" in lines
    assert "ok" in lines
    assert "panel schedule test done" in lines


def test_finalize_run_summary_marks_plain_successful_bat_as_one_success():
    summary = finalize_run_summary(summarize_run_log("plain bat output"), 0)

    assert summary["status"] == "success"
    assert summary["success_count"] == 1
    assert summary["failed_count"] == 0


def test_panel_runner_bat_supports_dp0_project_root_and_pause(tmp_path):
    runner = PanelRunner(project_root=tmp_path)
    bat_path = save_bat_file(
        tmp_path,
        "dp0_pause.bat",
        '@echo off\r\ncd /d "%~dp0..\\.."\r\necho cwd=%cd%\r\npause\r\n',
    )

    run = runner.start_run({"mode": "bat", "bat_path": str(bat_path)})
    deadline = datetime.now().timestamp() + 5
    while runner.get_run(run.id).status == "running" and datetime.now().timestamp() < deadline:
        time.sleep(0.05)

    done = runner.get_run(run.id)
    assert done.status == "success"
    assert done.summary["success_count"] == 1
    assert str(tmp_path) in runner.read_log(run.id)


def test_panel_runner_bat_supports_one_level_dp0_project_root(tmp_path):
    runner = PanelRunner(project_root=tmp_path)
    bat_path = save_bat_file(
        tmp_path,
        "temu_monthly.bat",
        '@echo off\r\ncd /d "%~dp0.."\r\necho cwd=%cd%\r\n',
    )

    run = runner.start_run({"mode": "bat", "bat_path": str(bat_path)})
    deadline = datetime.now().timestamp() + 5
    while runner.get_run(run.id).status == "running" and datetime.now().timestamp() < deadline:
        time.sleep(0.05)

    done = runner.get_run(run.id)
    assert done.status == "success"
    assert done.summary["success_count"] == 1
    assert f"cwd={tmp_path}" in runner.read_log(run.id)


def test_prepare_bat_for_run_relocates_legacy_bat_jobs_copy(tmp_path):
    legacy_root = tmp_path / "output" / "panel" / "bat_jobs"
    legacy_root.mkdir(parents=True)
    legacy = legacy_root / "legacy.bat"
    legacy.write_text("@echo off\r\necho legacy\r\n", encoding="utf-8")

    prepared = prepare_bat_for_run(tmp_path, legacy)

    assert prepared.parent == tmp_path / "output" / "panel"
    assert prepared.read_bytes() == legacy.read_bytes()


def test_summarize_run_log_for_business_status():
    summary = summarize_run_log(
        "\n".join(
            [
                "采集结束 | 账号=8 | 模块=1 | 执行成功=7 | 无数据=0 | 执行失败=1 | 输出文件=88",
                "失败明细:",
                "  - B27/B28/B29-主账号-CT | temu_fund_details | TEMU 登录超时: https://agentseller.temu.com/",
            ]
        )
    )

    assert summary["success_count"] == 7
    assert summary["failed_count"] == 1
    assert summary["output_file_count"] == 88
    assert summary["status"] == "failed"
    assert summary["failed_items"] == [
        {
            "account": "B27/B28/B29-主账号-CT",
            "task": "temu_fund_details",
            "message": "账号登录未完成，可能需要人工确认登录状态。",
        }
    ]


def test_schedule_due_key_supports_daily_weekly_monthly():
    now = datetime(2026, 6, 26, 23, 30)  # Friday

    daily = PanelSchedule(
        id="d1",
        name="每日",
        enabled=True,
        schedule_type="daily",
        hour=23,
        minute=30,
        payload={},
    )
    weekly = PanelSchedule(
        id="w1",
        name="每周",
        enabled=True,
        schedule_type="weekly",
        hour=23,
        minute=30,
        weekdays=[4],
        payload={},
    )
    monthly = PanelSchedule(
        id="m1",
        name="每月",
        enabled=True,
        schedule_type="monthly",
        hour=23,
        minute=30,
        month_day=26,
        payload={},
    )

    assert schedule_due_key(daily, now) == "2026-06-26T23:30"
    assert schedule_due_key(weekly, now) == "2026-W26-4T23:30"
    assert schedule_due_key(monthly, now) == "2026-06-26T23:30"


def test_schedule_due_key_ignores_wrong_day_or_disabled():
    now = datetime(2026, 6, 26, 23, 30)
    disabled = PanelSchedule(
        id="d1",
        name="每日",
        enabled=False,
        schedule_type="daily",
        hour=23,
        minute=30,
        payload={},
    )
    weekly = PanelSchedule(
        id="w1",
        name="每周",
        enabled=True,
        schedule_type="weekly",
        hour=23,
        minute=30,
        weekdays=[0],
        payload={},
    )

    assert schedule_due_key(disabled, now) == ""
    assert schedule_due_key(weekly, now) == ""


def test_schedule_run_name_uses_plan_name_and_trigger_time():
    schedule = PanelSchedule(
        id="m1",
        name="temu月度财务",
        enabled=True,
        schedule_type="monthly",
        hour=23,
        minute=0,
        month_day=2,
        payload={"mode": "bat", "bat_path": "nightly.bat"},
    )

    assert build_schedule_run_name(schedule, datetime(2026, 6, 2, 23, 0)) == "temu月度财务 - 2026-06-02 23:00"


def test_due_schedule_run_record_uses_schedule_name_and_trigger_time(tmp_path):
    runner = PanelRunner(project_root=tmp_path)
    bat_path = save_bat_file(tmp_path, "ok.bat", "@echo off\r\necho ok\r\n")
    runner.schedule_store.add(
        {
            "name": "temu月度财务",
            "enabled": True,
            "schedule_type": "monthly",
            "hour": 23,
            "minute": 0,
            "month_day": 2,
            "payload": {"mode": "bat", "bat_path": str(bat_path), "bat_name": bat_path.name},
        }
    )

    runs = runner.run_due_schedules(datetime(2026, 6, 2, 23, 0))

    assert len(runs) == 1
    assert runs[0].display_name == "temu月度财务 - 2026-06-02 23:00"


def test_panel_can_stop_current_run(tmp_path):
    runner = PanelRunner(project_root=tmp_path)
    bat_path = save_bat_file(tmp_path, "slow.bat", "@echo off\r\nping 127.0.0.1 -n 6 >nul\r\n")

    run = runner.start_run({"mode": "bat", "bat_path": str(bat_path), "run_name": "慢任务"})
    deadline = datetime.now().timestamp() + 3
    while not runner.stop_active_run() and datetime.now().timestamp() < deadline:
        time.sleep(0.05)
    deadline = datetime.now().timestamp() + 5
    while runner.get_run(run.id).status == "running" and datetime.now().timestamp() < deadline:
        time.sleep(0.05)

    stopped = runner.get_run(run.id)
    assert stopped.status == "stopped"
    assert "用户已中止当前任务" in runner.read_log(run.id)
