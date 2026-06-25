@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_sales_ledger --period monthly
pause
