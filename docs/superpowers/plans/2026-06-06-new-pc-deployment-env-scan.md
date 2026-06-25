# New PC Deployment And Environment Scan Plan

## Goal

Make a new Windows computer deployment independently verifiable before finance
export jobs are handed to business users.

## Implementation

1. Add focused tests for strict-mode exit behavior, JSON validation, writable
   directories, and enabled-task account pools.
2. Expand `env_scan.py` to inspect commands, dependency versions, configuration,
   account pools, writable paths, disk space, Git state, ZiNiao runtime, and task
   definitions. Every warning/error should include a suggested action.
3. Rewrite `DEPLOY.md` as an end-to-end deployment and operations runbook.
4. Make `install.bat` validate tasks and stop when the strict environment scan
   does not pass.

## Verification

- `py -3 -m pytest tests\test_env_scan.py -q`
- `py -3 -m py_compile env_scan.py`
- `py -3 scripts\validate_tasks.py`
- `py -3 env_scan.py --env prod`
- `py -3 -m pytest -q`
