@echo off
cd /d "%~dp0..\.."
py -3 main.py --env prod --task pop_balance_records --period monthly
pause
