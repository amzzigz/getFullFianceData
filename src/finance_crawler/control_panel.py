from __future__ import annotations

import json
import os
import re
import base64
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from finance_crawler.config import load_app_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_finance_command(
    project_root: Path,
    env: str,
    task_ids: list[str],
    accounts: list[str],
    shops: list[str],
    period: str,
    diagnose: bool,
    python_executable: str | None = None,
) -> list[str]:
    command = [python_executable or sys.executable, "-u", str(project_root / "main.py"), "--env", env]
    for task_id in task_ids:
        if task_id:
            command.extend(["--task", task_id])
    for account in accounts:
        if account:
            command.extend(["--account", account])
    for shop in shops:
        if shop:
            command.extend(["--shop", shop])
    if period:
        command.extend(["--period", period])
    if diagnose:
        command.append("--diagnose")
    return command


def build_bat_command(bat_path: Path) -> list[str]:
    return ["cmd.exe", "/d", "/c", str(bat_path)]


def save_bat_file(project_root: Path, filename: str, content: str) -> Path:
    return save_bat_file_bytes(project_root, filename, content.encode("utf-8"))


def save_bat_file_bytes(project_root: Path, filename: str, content: bytes) -> Path:
    bat_root = project_root / "output" / "panel"
    bat_root.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem or "finance_job"
    safe_stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", stem).strip("._") or "finance_job"
    saved = bat_root / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}_{safe_stem}.bat"
    saved.write_bytes(content)
    return saved


def prepare_bat_for_run(project_root: Path, bat_path: Path) -> Path:
    panel_root = project_root / "output" / "panel"
    try:
        is_legacy = bat_path.parent.name == "bat_jobs" and bat_path.parent.parent.resolve() == panel_root.resolve()
    except OSError:
        is_legacy = False
    if not is_legacy:
        return bat_path
    target = panel_root / bat_path.name
    if not target.exists() or target.read_bytes() != bat_path.read_bytes():
        target.write_bytes(bat_path.read_bytes())
    return target


def _clean_message(text: str) -> str:
    if "TEMU 登录超时" in text:
        return "账号登录未完成，可能需要人工确认登录状态。"
    if "与页面的连接已断开" in text:
        return "浏览器连接中断，程序已尝试恢复或重试。"
    if "Read timed out" in text or "timed out" in text:
        return "本机服务响应超时，程序已记录该项。"
    if "导出任务未完成" in text or "任务未完成" in text:
        return "平台仍在生成文件，程序正在等待。"
    if "account not found" in text or "账号未找到" in text:
        return "账号环境未找到，请确认紫鸟账号池或成员权限。"
    return text.strip()


def business_log_line(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("[auth]") or line.startswith("版本:"):
        return None
    if line.startswith("[开始]"):
        return "开始：" + line.removeprefix("[开始]").strip()
    if line.startswith("[完成]"):
        return "完成：" + line.removeprefix("[完成]").strip()
    if line.startswith("[无数据]"):
        return "无数据：" + line.removeprefix("[无数据]").strip()
    if line.startswith("[失败]"):
        return "失败：" + _clean_message(line.removeprefix("[失败]").strip())
    if line.startswith("[重试]"):
        return "重试：" + _clean_message(line.removeprefix("[重试]").strip())
    if line.startswith("[补跑"):
        return _clean_message(line)
    if line.startswith("财务采集启动") or line.startswith("任务:") or line.startswith("平台:"):
        return line
    if line.startswith("账号:") or line.startswith("采集结束") or line.startswith("失败明细") or line.startswith("无数据明细"):
        return line
    if line.startswith("文件数:") or line.startswith("文件:") or line.startswith("- "):
        return line
    cleaned = _clean_message(line)
    return cleaned if cleaned != line and cleaned else None


def _parse_detail_line(line: str) -> dict[str, str] | None:
    match = re.match(r"\s*-\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*)\s*$", line)
    if not match:
        return None
    return {
        "account": match.group(1).strip(),
        "task": match.group(2).strip(),
        "message": _clean_message(match.group(3).strip()),
    }


def summarize_run_log(log_text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": "running",
        "success_count": 0,
        "no_data_count": 0,
        "failed_count": 0,
        "output_file_count": 0,
        "failed_items": [],
        "no_data_items": [],
    }
    detail_mode = ""
    for line in log_text.splitlines():
        if line.startswith("采集结束"):
            numbers = {
                key: int(value)
                for key, value in re.findall(r"(执行成功|无数据|执行失败|输出文件)=(\d+)", line)
            }
            summary["success_count"] = numbers.get("执行成功", 0)
            summary["no_data_count"] = numbers.get("无数据", 0)
            summary["failed_count"] = numbers.get("执行失败", 0)
            summary["output_file_count"] = numbers.get("输出文件", 0)
            summary["status"] = "failed" if summary["failed_count"] else "success"
            continue
        if line.startswith("失败明细"):
            detail_mode = "failed"
            continue
        if line.startswith("无数据明细"):
            detail_mode = "no_data"
            continue
        item = _parse_detail_line(line)
        if item and detail_mode == "failed":
            summary["failed_items"].append(item)
        elif item and detail_mode == "no_data":
            summary["no_data_items"].append(item)
    return summary


def business_log_lines(log_text: str) -> list[str]:
    lines = [line for line in (business_log_line(item) for item in log_text.splitlines()) if line]
    if lines:
        return lines
    return [line.strip() for line in log_text.splitlines() if line.strip()]


def finalize_run_summary(summary: dict[str, Any], return_code: int | None) -> dict[str, Any]:
    if summary.get("status") != "running":
        return summary
    if return_code:
        summary["status"] = "failed"
        summary["failed_count"] = summary.get("failed_count") or 1
        return summary
    summary["status"] = "success"
    summary["success_count"] = summary.get("success_count") or 1
    return summary


def account_display_name(account: Any) -> str:
    if isinstance(account, dict):
        return str(
            account.get("label")
            or account.get("name")
            or account.get("browserName")
            or account.get("store_username")
            or ""
        )
    return str(account or "")


def dedupe_account_names(accounts: dict[str, list[str]], sources: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for source in sources:
        for account in accounts.get(source, []):
            if account and account not in seen:
                seen.add(account)
                result.append(account)
    return result


def panel_options(env: str = "prod", project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    config = load_app_config(env, project_root / "config")
    tasks = []
    for task in config.tasks:
        if not task.get("enabled"):
            continue
        account_source = str(task.get("account_source") or task.get("platform") or "")
        tasks.append(
            {
                "id": task.get("id"),
                "name": task.get("task_name") or task.get("id"),
                "platform": task.get("platform"),
                "account_source": account_source,
                "default_period": task.get("default_period") or "monthly",
                "frequency": task.get("frequency") or [],
            }
        )
    accounts = {
        key: [account_display_name(item) for item in value]
        for key, value in sorted(config.accounts.items())
    }
    account_pools = [
        {"key": key, "count": len(values), "accounts": values}
        for key, values in accounts.items()
    ]
    return {"env": env, "tasks": tasks, "accounts": accounts, "account_pools": account_pools}


@dataclass
class PanelRun:
    id: str
    env: str
    command: list[str]
    log_path: str
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    return_code: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class PanelSchedule:
    id: str
    name: str
    enabled: bool
    schedule_type: str
    hour: int
    minute: int
    payload: dict[str, Any]
    weekdays: list[int] = field(default_factory=list)
    month_day: int | None = None
    last_run_key: str = ""


def schedule_due_key(schedule: PanelSchedule, now: datetime | None = None) -> str:
    current = now or datetime.now()
    if not schedule.enabled:
        return ""
    if schedule.hour != current.hour or schedule.minute != current.minute:
        return ""
    if schedule.schedule_type == "daily":
        return current.strftime("%Y-%m-%dT%H:%M")
    if schedule.schedule_type == "weekly":
        weekday = current.weekday()
        if weekday not in schedule.weekdays:
            return ""
        return f"{current:%G-W%V}-{weekday}T{current:%H:%M}"
    if schedule.schedule_type == "monthly":
        if schedule.month_day != current.day:
            return ""
        return current.strftime("%Y-%m-%dT%H:%M")
    return ""


class ScheduleStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def list(self) -> list[PanelSchedule]:
        with self.lock:
            return self._read()

    def save(self, schedules: list[PanelSchedule]) -> None:
        with self.lock:
            self._write(schedules)

    def add(self, payload: dict[str, Any]) -> PanelSchedule:
        schedule = PanelSchedule(
            id=uuid.uuid4().hex[:10],
            name=str(payload.get("name") or "财务采集计划"),
            enabled=bool(payload.get("enabled", True)),
            schedule_type=str(payload.get("schedule_type") or "daily"),
            hour=int(payload.get("hour") or 0),
            minute=int(payload.get("minute") or 0),
            weekdays=[int(item) for item in payload.get("weekdays") or []],
            month_day=int(payload["month_day"]) if payload.get("month_day") not in (None, "") else None,
            payload=dict(payload.get("payload") or {}),
        )
        with self.lock:
            schedules = self._read()
            schedules.append(schedule)
            self._write(schedules)
        return schedule

    def delete(self, schedule_id: str) -> bool:
        with self.lock:
            schedules = self._read()
            kept = [item for item in schedules if item.id != schedule_id]
            if len(kept) == len(schedules):
                return False
            self._write(kept)
            return True

    def mark_ran(self, schedule_id: str, run_key: str) -> None:
        with self.lock:
            schedules = self._read()
            for schedule in schedules:
                if schedule.id == schedule_id:
                    schedule.last_run_key = run_key
                    break
            self._write(schedules)

    def _read(self) -> list[PanelSchedule]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        return [PanelSchedule(**item) for item in raw if isinstance(item, dict)]

    def _write(self, schedules: list[PanelSchedule]) -> None:
        self.path.write_text(
            json.dumps([asdict(item) for item in schedules], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class PanelRunner:
    def __init__(self, project_root: Path = PROJECT_ROOT, run_root: Path | None = None) -> None:
        self.project_root = project_root
        self.run_root = run_root or project_root / "output" / "panel" / "runs"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.schedule_store = ScheduleStore(project_root / "output" / "panel" / "schedules.json")
        self.runs: dict[str, PanelRun] = {}
        self.lock = threading.Lock()
        self.scheduler_started = False

    def active_run(self) -> PanelRun | None:
        with self.lock:
            return next((run for run in self.runs.values() if run.status == "running"), None)

    def start_run(self, payload: dict[str, Any]) -> PanelRun:
        if self.active_run():
            raise RuntimeError("已有任务正在运行，请等待完成后再启动。")
        run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        log_path = self.run_root / f"{run_id}.log"
        if payload.get("mode") == "bat":
            bat_path = Path(str(payload.get("bat_path") or ""))
            if not bat_path.exists():
                raise RuntimeError("定时 bat 文件不存在，请重新拖入。")
            bat_path = prepare_bat_for_run(self.project_root, bat_path)
            command = build_bat_command(bat_path)
        else:
            command = build_finance_command(
                project_root=self.project_root,
                env=str(payload.get("env") or "prod"),
                task_ids=[str(item) for item in payload.get("task_ids") or []],
                accounts=[str(item) for item in payload.get("accounts") or []],
                shops=[str(item) for item in payload.get("shops") or []],
                period=str(payload.get("period") or ""),
                diagnose=bool(payload.get("diagnose")),
            )
        run = PanelRun(
            id=run_id,
            env=str(payload.get("env") or "prod"),
            command=command,
            log_path=str(log_path),
        )
        with self.lock:
            self.runs[run_id] = run
        thread = threading.Thread(target=self._run_process, args=(run,), daemon=True)
        thread.start()
        return run

    def _run_process(self, run: PanelRun) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            with Path(run.log_path).open("w", encoding="utf-8", errors="replace") as log:
                log.write("面板启动命令: " + " ".join(run.command) + "\n")
                log.flush()
                process = subprocess.Popen(
                    run.command,
                    cwd=str(self.project_root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                run.return_code = process.wait()
        except Exception as exc:
            with Path(run.log_path).open("a", encoding="utf-8", errors="replace") as log:
                log.write(f"面板运行失败: {exc}\n")
            run.return_code = -1
        finally:
            text = self.read_log(run.id)
            run.summary = finalize_run_summary(summarize_run_log(text), run.return_code)
            run.status = str(run.summary.get("status") or "failed")
            run.ended_at = time.time()

    def get_run(self, run_id: str) -> PanelRun | None:
        with self.lock:
            return self.runs.get(run_id)

    def list_runs(self) -> list[PanelRun]:
        with self.lock:
            return sorted(self.runs.values(), key=lambda item: item.started_at, reverse=True)

    def read_log(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if not run:
            return ""
        path = Path(run.log_path)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def save_bat_file(self, filename: str, content: str) -> Path:
        return save_bat_file(self.project_root, filename, content)

    def start_scheduler(self) -> None:
        if self.scheduler_started:
            return
        self.scheduler_started = True
        thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        thread.start()

    def _scheduler_loop(self) -> None:
        while True:
            self.run_due_schedules()
            time.sleep(20)

    def run_due_schedules(self, now: datetime | None = None) -> list[PanelRun]:
        started: list[PanelRun] = []
        if self.active_run():
            return started
        for schedule in self.schedule_store.list():
            run_key = schedule_due_key(schedule, now)
            if not run_key or run_key == schedule.last_run_key:
                continue
            self.schedule_store.mark_ran(schedule.id, run_key)
            started.append(self.start_run(schedule.payload))
            break
        return started


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>财务采集控制面板</title>
  <style>
    :root { color-scheme: light; --bg:#f5f7fb; --ink:#1f2937; --muted:#697386; --line:#d9e0ea; --panel:#ffffff; --accent:#2563eb; --ok:#0f8a4b; --bad:#c2410c; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:"Microsoft YaHei",Segoe UI,Arial,sans-serif; background:var(--bg); color:var(--ink); }
    header { height:64px; display:flex; align-items:center; justify-content:space-between; padding:0 24px; border-bottom:1px solid var(--line); background:#fff; }
    h1 { font-size:20px; margin:0; font-weight:650; }
    main { display:grid; grid-template-columns: 320px 1fr; min-height:calc(100vh - 64px); }
    aside { padding:18px; border-right:1px solid var(--line); background:#fff; overflow:auto; }
    section { padding:18px 22px; overflow:auto; }
    label { display:block; font-size:13px; color:var(--muted); margin:14px 0 6px; }
    select, input { width:100%; height:36px; border:1px solid var(--line); border-radius:6px; padding:0 10px; background:#fff; color:var(--ink); }
    .row { display:flex; gap:8px; align-items:center; }
    .row > * { flex:1; }
    button { height:36px; border:1px solid var(--accent); background:var(--accent); color:#fff; border-radius:6px; padding:0 14px; cursor:pointer; font-weight:600; }
    button.secondary { color:var(--accent); background:#fff; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:14px; }
    .panel h2 { font-size:15px; margin:0 0 10px; }
    .checks { max-height:220px; overflow:auto; border:1px solid var(--line); border-radius:6px; padding:8px; background:#fbfcff; }
    .checks label { display:flex; gap:8px; align-items:center; margin:6px 0; color:var(--ink); }
    .checks input { width:auto; height:auto; }
    .mini-actions { display:flex; gap:8px; margin:6px 0; }
    .mini-actions button { height:28px; font-size:12px; padding:0 8px; }
    .pool { font-size:12px; line-height:1.6; color:var(--muted); border:1px solid var(--line); border-radius:6px; padding:8px; background:#fbfcff; max-height:140px; overflow:auto; }
    .pool b { color:var(--ink); }
    .summary { display:grid; grid-template-columns:repeat(4,minmax(120px,1fr)); gap:10px; }
    .metric { border-bottom:2px solid var(--line); padding:10px 0; }
    .metric strong { display:block; font-size:24px; }
    .metric span { color:var(--muted); font-size:13px; }
    pre { white-space:pre-wrap; margin:0; font-family:Consolas,"Microsoft YaHei",monospace; line-height:1.55; font-size:13px; }
    .log { height:420px; overflow:auto; background:#0f172a; color:#e2e8f0; border-radius:8px; padding:14px; }
    .status-ok { color:var(--ok); }
    .status-bad { color:var(--bad); }
    .muted { color:var(--muted); }
    .runs button { margin-left:8px; }
    .tabs { display:flex; gap:8px; margin-bottom:14px; border-bottom:1px solid var(--line); }
    .tab-button { height:38px; border:0; border-bottom:2px solid transparent; border-radius:0; background:transparent; color:var(--muted); padding:0 12px; }
    .tab-button.active { color:var(--accent); border-bottom-color:var(--accent); }
    .tab-view[hidden] { display:none; }
    .schedule-grid { display:grid; grid-template-columns:minmax(320px,420px) 1fr; gap:14px; align-items:start; }
    .schedule-list div { padding:10px 0; border-bottom:1px solid var(--line); }
    .dropzone { border:1px dashed #9aa8bd; border-radius:8px; padding:18px; background:#fbfcff; color:var(--muted); text-align:center; }
    .dropzone.dragging { border-color:var(--accent); color:var(--accent); background:#eff6ff; }
    .file-picked { margin-top:8px; font-size:12px; color:var(--ink); word-break:break-all; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      aside { border-right:0; border-bottom:1px solid var(--line); }
      .summary { grid-template-columns:repeat(2,minmax(120px,1fr)); }
      .schedule-grid { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
<header>
  <h1>财务采集控制面板</h1>
  <div class="muted" id="activeText">未运行</div>
</header>
<main>
  <aside>
    <div class="panel">
      <h2>运行范围</h2>
      <label>环境</label>
      <select id="env"><option value="prod">prod</option><option value="local">local</option></select>
      <label>周期</label>
      <select id="period"><option value="monthly">月度</option><option value="weekly">周度</option></select>
      <label>平台</label>
      <select id="platform"></select>
      <label>模块</label>
      <div class="mini-actions">
        <button class="secondary" id="selectAllTasks" type="button">全选模块</button>
        <button class="secondary" id="clearAllTasks" type="button">取消全选</button>
      </div>
      <div class="checks" id="tasks"></div>
      <label>账号池信息</label>
      <div class="pool" id="accountPools"></div>
      <label>账号</label>
      <div class="mini-actions">
        <button class="secondary" id="selectAllAccounts" type="button">全选账号</button>
        <button class="secondary" id="clearAllAccounts" type="button">取消全选</button>
      </div>
      <div class="checks" id="accounts"></div>
      <label>店铺筛选（可选，逗号分隔）</label>
      <input id="shops" placeholder="例如 B2, FaceTrue">
      <label><input id="diagnose" type="checkbox" style="width:auto;height:auto"> 保存诊断信息</label>
      <div class="row" style="margin-top:14px">
        <button id="runBtn">开始运行</button>
        <button class="secondary" id="refreshBtn">刷新</button>
      </div>
    </div>
  </aside>
  <section>
    <div class="tabs" role="tablist" aria-label="主要模块">
      <button class="tab-button active" data-tab="logs" id="logsTabButton" type="button">运行日志</button>
      <button class="tab-button" data-tab="schedules" id="schedulesTabButton" type="button">定时计划</button>
    </div>
    <div class="tab-view" id="logsTab">
      <div class="panel">
        <h2>本次结果</h2>
        <div class="summary">
          <div class="metric"><strong id="successCount">0</strong><span>成功</span></div>
          <div class="metric"><strong id="noDataCount">0</strong><span>无数据</span></div>
          <div class="metric"><strong id="failedCount">0</strong><span>失败</span></div>
          <div class="metric"><strong id="fileCount">0</strong><span>输出文件</span></div>
        </div>
      </div>
      <div class="panel">
        <h2>业务日志</h2>
        <div class="log"><pre id="businessLog"></pre></div>
      </div>
      <div class="panel runs">
        <h2>运行记录</h2>
        <div id="runs"></div>
      </div>
    </div>
    <div class="tab-view" id="schedulesTab" hidden>
      <div class="schedule-grid">
        <div class="panel">
          <h2>定时执行</h2>
          <div class="dropzone" id="batDropZone">
            <input id="batFileInput" type="file" accept=".bat,.cmd" hidden>
            <div>拖入 bat 文件，或点击选择</div>
            <div class="file-picked" id="batPickedText">未选择 bat</div>
          </div>
          <label>计划名称</label>
          <input id="scheduleName" value="财务采集计划">
          <label>频率</label>
          <select id="scheduleType">
            <option value="daily">每日</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
          </select>
          <div class="row">
            <div><label>小时</label><input id="scheduleHour" type="number" min="0" max="23" value="1"></div>
            <div><label>分钟</label><input id="scheduleMinute" type="number" min="0" max="59" value="0"></div>
          </div>
          <label>每周星期</label>
          <select id="scheduleWeekday">
            <option value="0">周一</option><option value="1">周二</option><option value="2">周三</option>
            <option value="3">周四</option><option value="4">周五</option><option value="5">周六</option><option value="6">周日</option>
          </select>
          <label>每月日期</label>
          <input id="scheduleMonthDay" type="number" min="1" max="31" value="1">
          <button id="saveScheduleBtn" style="margin-top:14px;width:100%">保存当前选择为计划</button>
        </div>
        <div class="panel runs schedule-list">
          <h2>定时计划</h2>
          <div id="schedules"></div>
        </div>
      </div>
    </div>
  </section>
</main>
<script>
let options = null;
let currentRunId = null;
let selectedBatPath = "";
let selectedBatName = "";

async function api(path, init) {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
}

function showError(error) {
  let message = error && error.message ? error.message : String(error || '操作失败');
  try {
    const parsed = JSON.parse(message);
    message = parsed.error || message;
  } catch (_) {}
  if (message.includes('已有任务正在运行')) {
    message = '已有任务正在运行，请等待完成后再启动。';
  }
  alert(message);
}

function checkedValues(id) {
  return [...document.querySelectorAll(`#${id} input:checked`)].map(x => x.value);
}

function splitShops() {
  return document.getElementById('shops').value.split(',').map(x => x.trim()).filter(Boolean);
}

function renderOptions() {
  const platforms = [...new Set(options.tasks.map(t => t.platform))];
  const platform = document.getElementById('platform');
  platform.innerHTML = platforms.map(p => `<option value="${p}">${p}</option>`).join('');
  document.getElementById('accountPools').innerHTML = (options.account_pools || []).map(p =>
    `<div><b>${p.key}</b>：${p.count} 个账号</div>`
  ).join('') || '<span>未读取到账号池</span>';
  renderFiltered();
}

function renderFiltered() {
  const platform = document.getElementById('platform').value;
  const tasks = options.tasks.filter(t => t.platform === platform);
  document.getElementById('tasks').innerHTML = tasks.map(t =>
    `<label><input type="checkbox" value="${t.id}" data-source="${t.account_source}" checked>${t.name}</label>`
  ).join('');
  renderAccounts();
}

function renderAccounts() {
  const sources = [...new Set([...document.querySelectorAll('#tasks input:checked')].map(x => x.dataset.source))];
  const seen = new Set();
  const values = [];
  sources.forEach(src => {
    (options.accounts[src] || []).forEach(account => {
      if (account && !seen.has(account)) {
        seen.add(account);
        values.push(account);
      }
    });
  });
  if (!sources.length) {
    document.getElementById('accounts').innerHTML = '<span class="muted">请先选择模块</span>';
    return;
  }
  if (!values.length) {
    document.getElementById('accounts').innerHTML = `<div class="muted">${sources.join('、')}：未读取到账号</div>`;
    return;
  }
  const title = `<div class="muted">已合并 ${sources.length} 个账号源，去重后 ${values.length} 个账号</div>`;
  document.getElementById('accounts').innerHTML = title + values.map(a =>
    `<label><input type="checkbox" value="${a}" checked>${a}</label>`
  ).join('');
}

function setChecks(id, checked) {
  document.querySelectorAll(`#${id} input`).forEach(x => x.checked = checked);
  if (id === 'tasks') renderAccounts();
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-button').forEach(button => {
    button.classList.toggle('active', button.dataset.tab === tabName);
  });
  document.getElementById('logsTab').hidden = tabName !== 'logs';
  document.getElementById('schedulesTab').hidden = tabName !== 'schedules';
}

async function refreshOptions() {
  const env = document.getElementById('env').value;
  options = await api(`/api/options?env=${encodeURIComponent(env)}`);
  renderOptions();
}

async function startRun() {
  try {
    const payload = {
      env: document.getElementById('env').value,
      period: document.getElementById('period').value,
      task_ids: checkedValues('tasks'),
      accounts: checkedValues('accounts'),
      shops: splitShops(),
      diagnose: document.getElementById('diagnose').checked
    };
    const run = await api('/api/runs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    currentRunId = run.id;
    await refreshRuns();
  } catch (error) {
    showError(error);
  }
}

function currentPayload() {
  return {
    env: document.getElementById('env').value,
    period: document.getElementById('period').value,
    task_ids: checkedValues('tasks'),
    accounts: checkedValues('accounts'),
    shops: splitShops(),
    diagnose: document.getElementById('diagnose').checked
  };
}

async function saveSchedule() {
  if (!selectedBatPath) {
    alert('请先拖入或选择 bat 文件。');
    return;
  }
  const scheduleType = document.getElementById('scheduleType').value;
  const payload = {
    name: document.getElementById('scheduleName').value || '财务采集计划',
    enabled: true,
    schedule_type: scheduleType,
    hour: Number(document.getElementById('scheduleHour').value || 0),
    minute: Number(document.getElementById('scheduleMinute').value || 0),
    weekdays: scheduleType === 'weekly' ? [Number(document.getElementById('scheduleWeekday').value)] : [],
    month_day: scheduleType === 'monthly' ? Number(document.getElementById('scheduleMonthDay').value || 1) : null,
    payload: {mode: 'bat', bat_path: selectedBatPath, bat_name: selectedBatName}
  };
  try {
    await api('/api/schedules', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    await refreshSchedules();
  } catch (error) {
    showError(error);
  }
}

async function uploadBatFile(file) {
  if (!file) return;
  const name = file.name || 'finance_job.bat';
  if (!name.toLowerCase().endsWith('.bat') && !name.toLowerCase().endsWith('.cmd')) {
    alert('请选择 bat 或 cmd 文件。');
    return;
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  try {
    const saved = await api('/api/bat-files', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({filename:name, content_b64:btoa(binary)})
    });
    selectedBatPath = saved.path;
    selectedBatName = saved.name;
    document.getElementById('batPickedText').textContent = `${saved.name} 已保存`;
  } catch (error) {
    showError(error);
  }
}

async function refreshRuns() {
  const runs = await api('/api/runs');
  document.getElementById('runs').innerHTML = runs.map(r =>
    `<div>${r.id} - <b class="${r.status === 'failed' ? 'status-bad' : 'status-ok'}">${r.status}</b><button class="secondary" onclick="selectRun('${r.id}')">查看</button></div>`
  ).join('') || '<span class="muted">暂无记录</span>';
  if (!currentRunId && runs.length) currentRunId = runs[0].id;
  if (currentRunId) await selectRun(currentRunId);
}

async function refreshSchedules() {
  const schedules = await api('/api/schedules');
  document.getElementById('schedules').innerHTML = schedules.map(s => {
    const text = s.schedule_type === 'daily'
      ? `每日 ${String(s.hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')}`
      : s.schedule_type === 'weekly'
        ? `每周 星期${(s.weekdays || []).map(x => '一二三四五六日'[x]).join(',')} ${String(s.hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')}`
        : `每月 ${s.month_day} 日 ${String(s.hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')}`;
    const target = s.payload && s.payload.bat_name ? ` - ${s.payload.bat_name}` : '';
    return `<div>${s.name} - ${text}${target} <button class="secondary" onclick="deleteSchedule('${s.id}')">删除</button></div>`;
  }).join('') || '<span class="muted">暂无计划</span>';
}

async function deleteSchedule(id) {
  await api(`/api/schedules/${id}`, {method:'DELETE'});
  await refreshSchedules();
}

async function selectRun(id) {
  currentRunId = id;
  const run = await api(`/api/runs/${id}`);
  const log = await api(`/api/runs/${id}/business-log`);
  const s = run.summary || {};
  document.getElementById('successCount').textContent = s.success_count || 0;
  document.getElementById('noDataCount').textContent = s.no_data_count || 0;
  document.getElementById('failedCount').textContent = s.failed_count || 0;
  document.getElementById('fileCount').textContent = s.output_file_count || 0;
  document.getElementById('businessLog').textContent = log.lines.join('\n');
  document.getElementById('activeText').textContent = run.status === 'running' ? '正在运行' : '最近运行：' + run.status;
}

document.getElementById('env').addEventListener('change', refreshOptions);
document.getElementById('platform').addEventListener('change', renderFiltered);
document.getElementById('tasks').addEventListener('change', renderAccounts);
document.getElementById('refreshBtn').addEventListener('click', refreshRuns);
document.getElementById('runBtn').addEventListener('click', startRun);
document.getElementById('selectAllTasks').addEventListener('click', () => setChecks('tasks', true));
document.getElementById('clearAllTasks').addEventListener('click', () => setChecks('tasks', false));
document.getElementById('selectAllAccounts').addEventListener('click', () => setChecks('accounts', true));
document.getElementById('clearAllAccounts').addEventListener('click', () => setChecks('accounts', false));
document.getElementById('saveScheduleBtn').addEventListener('click', saveSchedule);
const batDropZone = document.getElementById('batDropZone');
const batFileInput = document.getElementById('batFileInput');
batDropZone.addEventListener('click', () => batFileInput.click());
batFileInput.addEventListener('change', () => uploadBatFile(batFileInput.files[0]));
batDropZone.addEventListener('dragover', event => {
  event.preventDefault();
  batDropZone.classList.add('dragging');
});
batDropZone.addEventListener('dragleave', () => batDropZone.classList.remove('dragging'));
batDropZone.addEventListener('drop', event => {
  event.preventDefault();
  batDropZone.classList.remove('dragging');
  uploadBatFile(event.dataTransfer.files[0]);
});
document.querySelectorAll('.tab-button').forEach(button => {
  button.addEventListener('click', () => switchTab(button.dataset.tab));
});
refreshOptions().then(refreshRuns).then(refreshSchedules);
setInterval(() => { refreshRuns(); refreshSchedules(); }, 3000);
</script>
</body>
</html>
"""


class ControlPanelHandler(BaseHTTPRequestHandler):
    runner: PanelRunner

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(INDEX_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/options":
            env = parse_qs(parsed.query).get("env", ["prod"])[0]
            self._send_json(panel_options(env, self.runner.project_root))
            return
        if parsed.path == "/api/runs":
            self._send_json([self._run_payload(run) for run in self.runner.list_runs()])
            return
        if parsed.path == "/api/schedules":
            self._send_json([asdict(item) for item in self.runner.schedule_store.list()])
            return
        match = re.match(r"^/api/runs/([^/]+)(?:/(business-log|log))?$", parsed.path)
        if match:
            run_id, kind = match.group(1), match.group(2)
            run = self.runner.get_run(run_id)
            if not run:
                self._send_json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                return
            if kind == "business-log":
                self._send_json({"lines": business_log_lines(self.runner.read_log(run_id))})
                return
            if kind == "log":
                self._send_text(self.runner.read_log(run_id), "text/plain; charset=utf-8")
                return
            self._send_json(self._run_payload(run))
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/api/bat-files":
            try:
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                filename = str(payload.get("filename") or "")
                if not filename.lower().endswith((".bat", ".cmd")):
                    raise RuntimeError("只支持 bat 或 cmd 文件。")
                if payload.get("content_b64"):
                    content = base64.b64decode(str(payload.get("content_b64")))
                    saved = save_bat_file_bytes(self.runner.project_root, filename, content)
                else:
                    saved = self.runner.save_bat_file(filename, str(payload.get("content") or ""))
                self._send_json({"name": saved.name, "path": str(saved)}, HTTPStatus.CREATED)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/schedules":
            try:
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                schedule = self.runner.schedule_store.add(payload)
                self._send_json(asdict(schedule), HTTPStatus.CREATED)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path != "/api/runs":
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            run = self.runner.start_run(payload)
            self._send_json(self._run_payload(run), HTTPStatus.CREATED)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        match = re.match(r"^/api/schedules/([^/]+)$", self.path)
        if not match:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        deleted = self.runner.schedule_store.delete(match.group(1))
        self._send_json({"deleted": deleted})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _run_payload(self, run: PanelRun) -> dict[str, Any]:
        return asdict(run)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_control_panel(host: str = "127.0.0.1", port: int = 8765, project_root: Path = PROJECT_ROOT) -> None:
    ControlPanelHandler.runner = PanelRunner(project_root=project_root)
    ControlPanelHandler.runner.start_scheduler()
    server = ThreadingHTTPServer((host, port), ControlPanelHandler)
    print(f"财务采集控制面板: http://{host}:{port}")
    server.serve_forever()
