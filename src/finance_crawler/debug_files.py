from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_crawler.periods import PeriodRange


def capture_enabled(task: dict[str, Any], failed: bool = False) -> bool:
    if failed:
        return True
    return bool(task.get("save_capture_files", True))


def write_capture_file(
    task: dict[str, Any],
    output_root: Path,
    platform: str,
    period: PeriodRange,
    file_stem: str,
    payload: dict[str, Any],
    failed: bool = False,
) -> str:
    if not capture_enabled(task, failed=failed):
        return ""
    period_label = f"{period.start:%Y%m%d}_{period.end:%Y%m%d}"
    capture_dir = output_root / "captures" / platform / period.period_type / period_label
    capture_dir.mkdir(parents=True, exist_ok=True)
    suffix = "failed_" if failed else ""
    capture_file = capture_dir / f"{file_stem}_{suffix}{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    capture_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(capture_file)
