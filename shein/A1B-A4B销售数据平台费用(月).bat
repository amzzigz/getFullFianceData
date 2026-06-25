@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_a1b_a4b_sales_data --period monthly
pause
