from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_crawler.control_panel import run_control_panel


def main() -> int:
    parser = argparse.ArgumentParser(description="启动财务采集本地网页控制面板。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project-root", default=str(ROOT), help="财务项目根目录，默认使用当前代码所在目录。")
    args = parser.parse_args()
    run_control_panel(host=args.host, port=args.port, project_root=Path(args.project_root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
