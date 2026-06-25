@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_balance_records --period monthly --account A1B --account A2B --account A3B --account A4B
pause
