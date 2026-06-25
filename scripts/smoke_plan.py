from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run cheap harness checks for the finance crawler.")
    parser.add_argument("--env", default="local", choices=["local", "prod"])
    parser.add_argument("--today", default="2026-05-12")
    args = parser.parse_args()

    commands = [
        [sys.executable, "scripts/validate_tasks.py"],
        [sys.executable, "main.py", "--env", args.env, "--today", args.today, "--dry-run"],
    ]

    for command in commands:
        print("$", " ".join(command))
        result = subprocess.run(command, cwd=PROJECT_DIR, text=True)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
