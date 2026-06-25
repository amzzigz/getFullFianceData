@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_a1y_a4y_report_bill --period monthly
pause
