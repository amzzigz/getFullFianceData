from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import env_scan


def test_required_modules_include_windows_timezone_data() -> None:
    assert env_scan.REQUIRED_MODULES["tzdata"] == "tzdata"


def test_add_check_stores_suggestion() -> None:
    checks: list[dict[str, object]] = []

    env_scan.add_check(checks, "demo", "WARN", "需要处理", suggestion="执行修复命令")

    assert checks == [
        {
            "name": "demo",
            "status": "WARN",
            "message": "需要处理",
            "suggestion": "执行修复命令",
        }
    ]


def test_result_exit_code_strict_treats_warning_as_failure() -> None:
    result = {"summary": {"error": 0, "warn": 1}}

    assert env_scan.result_exit_code(result, strict=False) == 0
    assert env_scan.result_exit_code(result, strict=True) == 1


def test_check_json_file_reports_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    checks: list[dict[str, object]] = []

    env_scan.check_json_file(checks, "broken_config", path, required=True)

    assert checks[0]["status"] == "ERROR"
    assert "JSON" in str(checks[0]["message"])
    assert checks[0]["suggestion"]


def test_check_writable_directory_creates_and_removes_probe(tmp_path: Path) -> None:
    target = tmp_path / "new-output"
    checks: list[dict[str, object]] = []

    env_scan.check_writable_directory(checks, "output_directory", target)

    assert checks[0]["status"] == "OK"
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_check_account_sources_reports_enabled_task_with_empty_accounts() -> None:
    config = SimpleNamespace(
        accounts={"tiktok": ["TK-1"], "tiktok_email": []},
        tasks=[
            {"id": "tk", "enabled": True, "account_source": "tiktok"},
            {"id": "e1e2", "enabled": True, "account_source": "tiktok_email"},
            {"id": "disabled", "enabled": False, "account_source": "missing"},
        ],
    )
    checks: list[dict[str, object]] = []

    env_scan.check_account_sources(checks, config)

    assert checks[0]["status"] == "ERROR"
    assert "tiktok_email" in str(checks[0]["message"])
    assert "missing" not in str(checks[0]["message"])


def test_build_summary_exposes_ready_and_suggestions() -> None:
    checks = [
        {"name": "ok", "status": "OK", "message": "ok"},
        {"name": "warn", "status": "WARN", "message": "warn", "suggestion": "fix it"},
    ]

    summary = env_scan.build_summary(checks)

    assert summary == {"ok": 1, "warn": 1, "error": 0, "ready": True}
    assert json.dumps(checks)
