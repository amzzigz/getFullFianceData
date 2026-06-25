from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re


PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_DIR / "config"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_account_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    values: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in values:
            values.append(value)
    return values


def split_shein_accounts(lines: list[str]) -> dict[str, list[str]]:
    main: list[str] = []
    a1b_a4b: list[str] = []
    pop: list[str] = []
    f1_f20: list[str] = []
    for value in lines:
        upper = value.upper()
        if "POP" in upper:
            pop.append(value)
        elif re.match(r"^A\d+B\b", upper):
            a1b_a4b.append(value)
        elif re.match(r"^F\d+\b", upper):
            f1_f20.append(value)
        elif re.match(r"^SPP\d+\b", upper) or re.match(r"^A\d+\b", upper):
            main.append(value)
    result: dict[str, list[str]] = {}
    if main:
        result["shein"] = main
        result["shein_main_12"] = main
    if a1b_a4b:
        result["shein_a1b_a4b"] = a1b_a4b
    if pop:
        result["pop"] = pop
    if f1_f20:
        result["shein_f1_f20"] = f1_f20
    return result


def resolve_path(raw_value: str | Path | None, base_dir: Path = PROJECT_DIR) -> Path:
    value = str(raw_value or "").strip()
    if not value:
        return Path("")
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


@dataclass
class AppConfig:
    env: str
    config_dir: Path
    raw: dict[str, Any] = field(default_factory=dict)
    accounts: dict[str, list[str]] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    secrets: dict[str, Any] = field(default_factory=dict)

    @property
    def paths(self) -> dict[str, Any]:
        return self.raw.get("paths") or {}

    @property
    def software(self) -> dict[str, Any]:
        return self.raw.get("software") or {}

    @property
    def runtime(self) -> dict[str, Any]:
        return self.raw.get("runtime") or {}

    def path(self, key: str, default: str = "") -> Path:
        return resolve_path(self.paths.get(key) or default)

    def output_root(self) -> Path:
        return self.path("output_root", "output")

    def log_root(self) -> Path:
        return self.path("log_root", "logs")

    def desktop_auth_path(self) -> Path:
        return self.path("desktop_auth_path")

    def chrome_path(self) -> Path:
        return resolve_path(self.software.get("chrome_path"))

    def ziniu_install_dir(self) -> Path:
        return resolve_path(self.software.get("ziniu_install_dir"))

    def ziniu_host(self) -> str:
        return str(self.software.get("ziniu_webdriver_host") or "127.0.0.1")

    def ziniu_port(self) -> int:
        return int(self.software.get("ziniu_webdriver_port") or 16851)

    def max_workers(self) -> int:
        return int(self.runtime.get("max_workers") or 1)

    def ziniu_auth_concurrency(self) -> int:
        return max(1, int(self.runtime.get("ziniu_auth_concurrency") or 1))

    def account_module_concurrency(self) -> int:
        return max(1, int(self.runtime.get("account_module_concurrency") or 1))

    def final_failed_rerun_count(self) -> int:
        return max(0, int(self.runtime.get("final_failed_rerun_count") or 0))

    def save_run_log(self) -> bool:
        return bool(self.runtime.get("save_run_log", True))


def load_app_config(env: str = "local", config_dir: str | Path | None = None) -> AppConfig:
    selected_env = (env or "local").strip()
    root = Path(config_dir).expanduser() if config_dir else DEFAULT_CONFIG_DIR
    raw = load_json(root / f"{selected_env}.json", {})
    accounts = load_json(root / f"accounts.{selected_env}.json", {})
    shein_accounts = load_account_lines(resolve_path((raw.get("paths") or {}).get("account_mapping_file") or "tools/shein账号池.txt"))
    if shein_accounts:
        accounts.update(split_shein_accounts(shein_accounts))
    shein_a1y_accounts = load_account_lines(resolve_path((raw.get("paths") or {}).get("shein_a1y_account_file") or "tools/A1Y-A4Y.txt"))
    if shein_a1y_accounts:
        accounts["shein_a1y_a4y"] = shein_a1y_accounts
    aliexpress_accounts = load_account_lines(resolve_path((raw.get("paths") or {}).get("aliexpress_account_file") or "tools/速卖通.txt"))
    if aliexpress_accounts:
        accounts["aliexpress"] = aliexpress_accounts
    tiktok_accounts = load_account_lines(resolve_path((raw.get("paths") or {}).get("tiktok_account_file") or "tools/tk账号池.txt"))
    if tiktok_accounts:
        accounts["tiktok"] = tiktok_accounts
    tiktok_email_account_file = (
        (raw.get("paths") or {}).get("tiktok_email_account_file")
        or (raw.get("paths") or {}).get("tiktok_pop_account_file")
        or "tools/E1-E2.txt"
    )
    tiktok_email_accounts = load_account_lines(resolve_path(tiktok_email_account_file))
    if tiktok_email_accounts:
        accounts["tiktok_email"] = tiktok_email_accounts
    tasks_payload = load_json(root / "tasks.json", {"tasks": []})
    secrets = load_json(root / f"secrets.{selected_env}.json", {})
    return AppConfig(
        env=selected_env,
        config_dir=root,
        raw=raw,
        accounts=accounts,
        tasks=list(tasks_payload.get("tasks") or []),
        secrets=secrets,
    )
