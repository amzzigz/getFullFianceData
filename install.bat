@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未找到 Python Launcher py。请先安装 Python 3.11+，并勾选 Add python.exe to PATH。
  pause
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 需要 Python 3.11 或更高版本。
  py -3 --version
  pause
  exit /b 1
)

echo [1/4] 升级 pip...
py -3 -m pip install --upgrade pip
if errorlevel 1 goto fail

echo [2/4] 安装项目依赖...
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo [3/4] 校验任务定义...
py -3 scripts\validate_tasks.py
if errorlevel 1 goto fail

echo [4/4] 执行严格环境扫描...
py -3 env_scan.py --env prod --strict
if errorlevel 1 goto fail

echo.
echo 初始化和严格环境验收完成。可以开始单账号实跑。
pause
exit /b 0

:fail
echo.
echo [ERROR] 初始化失败，请查看上方错误。
pause
exit /b 1
