@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task aliexpress_finance --period monthly
pause
