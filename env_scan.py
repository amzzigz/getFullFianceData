from __future__ import annotations

import argparse
import ast
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from finance_crawler.config import AppConfig, load_app_config


AUTH_SCRIPT_CANDIDATES = [
    PROJECT_DIR / "tools" / "ziniu_auth_login_extracted.py",
    Path.home() / "Desktop" / "ziniu_auth_login_extracted.py",
    Path.home() / "Desktop" / "自动化" / "ziniu_auth_login_extracted.py",
    Path.home() / "Desktop" / "python backup" / "ziniu_auth_login_extracted.py",
]

CHROME_CANDIDATES = [
    Path(os.environ.get("CHROME_PATH", "")),
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
]

ZINIAO_INSTALL_CANDIDATES = [
    Path(os.environ.get("ZINIAO_INSTALL_DIR", "")),
    Path(r"D:\紫鸟\ziniao"),
    Path(r"D:\ziniao"),
    Path(r"C:\紫鸟\ziniao"),
    Path(r"C:\ziniao"),
    Path(r"C:\Program Files\ziniao"),
    Path(r"C:\Program Files\ZiNiao"),
    Path(r"C:\Program Files (x86)\ziniao"),
    Path(r"C:\Program Files (x86)\ZiNiao"),
]

REQUIRED_MODULES = {
    "requests": "requests",
    "DrissionPage": "DrissionPage",
    "psutil": "psutil",
    "openpyxl": "openpyxl",
    "tzdata": "tzdata",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="扫描财务采集项目运行环境。")
    parser.add_argument("--env", choices=["local", "prod"], default="local", help="配置环境。")
    parser.add_argument("--config-dir", default=str(PROJECT_DIR / "config"), help="配置目录。")
    parser.add_argument("--json-output", default="", help="扫描结果 JSON 输出路径。")
    parser.add_argument("--strict", action="store_true", help="将 WARN 也视为扫描失败，用于新电脑交付验收。")
    return parser.parse_args()


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    status: str,
    message: str,
    suggestion: str = "",
    **extra: Any,
) -> None:
    item = {"name": name, "status": status, "message": message}
    if suggestion:
        item["suggestion"] = suggestion
    item.update(extra)
    checks.append(item)


def path_exists(path: Path) -> bool:
    text = str(path)
    return bool(text and text != "." and path.exists())


def first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path_exists(path)), None)


def socket_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def normalize_path(path: Path | None) -> str:
    return str(path) if path and str(path) else ""


def check_json_file(checks: list[dict[str, Any]], name: str, path: Path, required: bool) -> None:
    if not path.exists():
        add_check(
            checks,
            name,
            "ERROR" if required else "WARN",
            f"未找到: {path}",
            suggestion=f"从对应的 .example.json 模板复制并填写: {path.name}",
        )
        return
    try:
        json.loads(path.read_text(encoding="utf-8"))
        add_check(checks, name, "OK", f"JSON 有效: {path}")
    except Exception as exc:
        add_check(
            checks,
            name,
            "ERROR",
            f"JSON 解析失败: {path} ({exc})",
            suggestion="用编辑器修复 JSON 格式后重新扫描。",
        )


def check_commands(checks: list[dict[str, Any]]) -> None:
    for command, required in (("git", True), ("py", True)):
        selected = shutil.which(command)
        add_check(
            checks,
            f"command_{command}",
            "OK" if selected else ("ERROR" if required else "WARN"),
            f"找到命令: {selected}" if selected else f"未找到命令: {command}",
            suggestion=f"安装 {command} 并重新打开命令行。" if not selected else "",
        )


def check_modules(checks: list[dict[str, Any]]) -> None:
    found: dict[str, str] = {}
    missing: list[str] = []
    for display, module_name in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(display)
            continue
        try:
            found[display] = importlib.metadata.version(display)
        except importlib.metadata.PackageNotFoundError:
            found[display] = "installed"
    add_check(
        checks,
        "python_modules",
        "ERROR" if missing else "OK",
        f"缺少依赖: {', '.join(missing)}" if missing else "核心 Python 依赖已安装",
        suggestion="运行 install.bat 或 py -3 -m pip install -r requirements.txt。" if missing else "",
        modules=found,
    )


def check_auth_script(checks: list[dict[str, Any]], configured_path: Path) -> None:
    candidates = [configured_path, *AUTH_SCRIPT_CANDIDATES] if str(configured_path) else AUTH_SCRIPT_CANDIDATES
    auth_path = first_existing(candidates)
    add_check(
        checks,
        "ziniu_auth_script_path",
        "OK" if auth_path else "ERROR",
        f"找到紫鸟鉴权脚本: {auth_path}" if auth_path else "未找到紫鸟鉴权脚本",
        suggestion="将可用的 ziniu_auth_login_extracted.py 放入 tools 目录或在配置中指定路径。" if not auth_path else "",
        candidates=[normalize_path(path) for path in candidates],
        selected=normalize_path(auth_path),
    )
    if not auth_path:
        return
    try:
        tree = ast.parse(auth_path.read_text(encoding="utf-8"), filename=str(auth_path))
        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        ok = "ZiniuAuthLogin" in classes and "auth_login" in functions
        add_check(
            checks,
            "ziniu_auth_script_symbols",
            "OK" if ok else "ERROR",
            "紫鸟脚本结构正常" if ok else "紫鸟脚本缺少 ZiniuAuthLogin/auth_login",
            suggestion="从当前可运行电脑同步最新紫鸟鉴权脚本。" if not ok else "",
        )
    except Exception as exc:
        add_check(
            checks,
            "ziniu_auth_script_parse",
            "ERROR",
            f"紫鸟脚本解析失败: {exc}",
            suggestion="修复脚本语法或从当前可运行电脑重新同步。",
        )


def check_account_sources(checks: list[dict[str, Any]], config: AppConfig) -> None:
    enabled_sources = {
        str(task.get("account_source") or "").strip()
        for task in config.tasks
        if task.get("enabled") and task.get("account_source")
    }
    missing = sorted(source for source in enabled_sources if not config.accounts.get(source))
    counts = {source: len(config.accounts.get(source) or []) for source in sorted(enabled_sources)}
    add_check(
        checks,
        "enabled_task_accounts",
        "ERROR" if missing else "OK",
        f"启用任务缺少账号池: {', '.join(missing)}" if missing else "所有启用任务均有账号",
        suggestion="填写 config/accounts.<env>.json 或 tools 目录中的对应账号池文件。" if missing else "",
        account_counts=counts,
    )


def check_writable_directory(checks: list[dict[str, Any]], name: str, path: Path) -> None:
    probe = path / ".env_scan_write_test"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        free_gb = shutil.disk_usage(path).free / (1024**3)
        status = "WARN" if free_gb < 5 else "OK"
        add_check(
            checks,
            name,
            status,
            f"可写，剩余空间 {free_gb:.1f} GB: {path}",
            suggestion="清理磁盘，建议至少保留 5 GB 可用空间。" if status == "WARN" else "",
            free_gb=round(free_gb, 2),
        )
    except Exception as exc:
        add_check(
            checks,
            name,
            "ERROR",
            f"目录不可写: {path} ({exc})",
            suggestion="修复目录权限或在配置中改为可写目录。",
        )


def run_project_check(checks: list[dict[str, Any]], name: str, command: list[str]) -> None:
    try:
        result = subprocess.run(command, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=60)
        detail = (result.stdout or result.stderr).strip().splitlines()
        message = detail[-1] if detail else "命令执行完成"
        add_check(
            checks,
            name,
            "OK" if result.returncode == 0 else "ERROR",
            message,
            suggestion=f"手动运行并修复输出: {' '.join(command)}" if result.returncode else "",
            returncode=result.returncode,
        )
    except Exception as exc:
        add_check(
            checks,
            name,
            "ERROR",
            f"检查命令执行失败: {exc}",
            suggestion=f"手动运行: {' '.join(command)}",
        )


def check_git(checks: list[dict[str, Any]]) -> None:
    if not shutil.which("git"):
        return
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=10, check=True
            ).stdout.strip()
        )
        add_check(
            checks,
            "git_repository",
            "WARN" if dirty else "OK",
            f"分支={branch or '(detached)'}，origin={remote or '(未配置)'}，工作区={'有改动' if dirty else '干净'}",
            suggestion="确认本地改动已提交或已备份，再用于正式部署。" if dirty else "",
        )
    except Exception as exc:
        add_check(
            checks,
            "git_repository",
            "WARN",
            f"Git 仓库状态读取失败: {exc}",
            suggestion="确认当前目录由 Git 克隆且已配置 origin。",
        )


def check_ziniu_process(checks: list[dict[str, Any]]) -> None:
    try:
        import psutil

        names = sorted(
            {
                process.info["name"]
                for process in psutil.process_iter(["name"])
                if "ziniao" in str(process.info.get("name") or "").lower()
                or "紫鸟" in str(process.info.get("name") or "").lower()
            }
        )
        add_check(
            checks,
            "ziniu_process",
            "OK" if names else "WARN",
            f"紫鸟进程运行中: {', '.join(names)}" if names else "未检测到紫鸟进程",
            suggestion="启动紫鸟并登录账号后重新扫描。" if not names else "",
        )
    except Exception as exc:
        add_check(checks, "ziniu_process", "WARN", f"紫鸟进程检查失败: {exc}")


def build_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "ok": sum(1 for item in checks if item["status"] == "OK"),
        "warn": sum(1 for item in checks if item["status"] == "WARN"),
        "error": sum(1 for item in checks if item["status"] == "ERROR"),
    }
    summary["ready"] = summary["error"] == 0
    return summary


def build_result(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    config_dir = Path(args.config_dir)
    env_path = config_dir / f"{args.env}.json"
    accounts_path = config_dir / f"accounts.{args.env}.json"
    tasks_path = config_dir / "tasks.json"
    secrets_path = config_dir / f"secrets.{args.env}.json"

    check_commands(checks)
    check_json_file(checks, "environment_config", env_path, required=True)
    check_json_file(checks, "accounts_config", accounts_path, required=False)
    check_json_file(checks, "tasks_config", tasks_path, required=True)
    check_json_file(checks, "secrets_config", secrets_path, required=False)

    add_check(
        checks,
        "python",
        "OK" if sys.version_info >= (3, 11) else "ERROR",
        f"{platform.python_version()} ({sys.executable})",
        suggestion="安装 Python 3.11 或 3.12 64 位版。" if sys.version_info < (3, 11) else "",
    )
    check_modules(checks)
    check_git(checks)
    run_project_check(checks, "task_contract", [sys.executable, "scripts/validate_tasks.py"])

    try:
        config = load_app_config(args.env, config_dir)
    except Exception as exc:
        add_check(
            checks,
            "config_load",
            "ERROR",
            f"项目配置加载失败: {exc}",
            suggestion="先修复上方配置 JSON 错误，再重新扫描。",
        )
        return make_result(args.env, checks)

    check_account_sources(checks, config)
    check_writable_directory(checks, "output_directory", config.output_root())
    check_writable_directory(checks, "log_directory", config.log_root())
    check_writable_directory(checks, "download_directory", config.path("download_root", "output/downloads"))

    chrome_candidates = [config.chrome_path(), *CHROME_CANDIDATES]
    chrome_path = first_existing(chrome_candidates)
    add_check(
        checks,
        "chrome_path",
        "OK" if chrome_path else "WARN",
        f"找到 Chrome: {chrome_path}" if chrome_path else "未找到 Chrome",
        suggestion="安装 Chrome，或在配置 software.chrome_path 中填写实际路径。" if not chrome_path else "",
        candidates=[normalize_path(path) for path in chrome_candidates if str(path)],
    )

    ziniu_candidates = [config.ziniu_install_dir(), *ZINIAO_INSTALL_CANDIDATES]
    ziniu_path = first_existing(ziniu_candidates)
    add_check(
        checks,
        "ziniu_install_path",
        "OK" if ziniu_path else "WARN",
        f"找到紫鸟安装目录: {ziniu_path}" if ziniu_path else "未找到紫鸟安装目录",
        suggestion="安装紫鸟，或在配置 software.ziniu_install_dir 中填写实际目录。" if not ziniu_path else "",
        candidates=[normalize_path(path) for path in ziniu_candidates if str(path)],
    )
    check_ziniu_process(checks)

    port_online = socket_open(config.ziniu_host(), config.ziniu_port())
    add_check(
        checks,
        "ziniu_webdriver_port",
        "OK" if port_online else "WARN",
        f"{config.ziniu_host()}:{config.ziniu_port()} 可连接"
        if port_online
        else f"{config.ziniu_host()}:{config.ziniu_port()} 未监听",
        suggestion="启动并登录紫鸟；若仍未监听，核对配置中的 webdriver 端口。" if not port_online else "",
    )
    check_auth_script(checks, config.desktop_auth_path())

    bat_count = len(list(PROJECT_DIR.glob("**/*.bat")))
    add_check(
        checks,
        "batch_entries",
        "OK" if bat_count else "ERROR",
        f"发现 {bat_count} 个 BAT 任务入口",
        suggestion="确认平台 BAT 文件已随 Git 仓库完整拉取。" if not bat_count else "",
    )
    return make_result(args.env, checks)


def make_result(environment: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment": environment,
        "project_dir": str(PROJECT_DIR),
        "checks": checks,
        "summary": build_summary(checks),
    }


def result_exit_code(result: dict[str, Any], strict: bool) -> int:
    summary = result["summary"]
    return 1 if summary["error"] or (strict and summary["warn"]) else 0


def main() -> int:
    args = parse_args()
    result = build_result(args)
    summary = result["summary"]
    print(
        f"环境扫描完成：OK={summary['ok']} WARN={summary['warn']} "
        f"ERROR={summary['error']} READY={'YES' if summary['ready'] else 'NO'}"
    )
    for item in result["checks"]:
        print(f"[{item['status']}] {item['name']}: {item['message']}")
        if item.get("suggestion"):
            print(f"  建议: {item['suggestion']}")
    output_path = (
        Path(args.json_output)
        if args.json_output
        else PROJECT_DIR / "output" / "env_scan" / f"env_scan_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"扫描结果已写入: {output_path}")
    if args.strict and summary["warn"]:
        print("严格模式：存在 WARN，验收未通过。")
    return result_exit_code(result, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
