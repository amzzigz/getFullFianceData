from __future__ import annotations

from typing import Any


DEFAULT_TIKTOK_DOWNLOAD_ATTEMPTS = 12
DEFAULT_TIKTOK_DOWNLOAD_INTERVAL_SECONDS = 4


def tiktok_download_poll_options(task: dict[str, Any]) -> tuple[int, int]:
    attempts = int(task.get("download_attempts") or DEFAULT_TIKTOK_DOWNLOAD_ATTEMPTS)
    interval = int(task.get("download_interval_seconds") or DEFAULT_TIKTOK_DOWNLOAD_INTERVAL_SECONDS)
    return max(1, attempts), max(1, interval)


def log_tiktok_poll(account_name: str, target: str, attempt: int, attempts: int, state: str) -> None:
    print(f"[TK] {account_name} 等待{target} {attempt}/{attempts}: {state}", flush=True)
