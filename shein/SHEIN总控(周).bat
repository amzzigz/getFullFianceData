@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --period weekly ^
  --task shein_sales_ledger ^
  --task shein_funds ^
  --task shein_platform_fees ^
  --task shein_a1b_a4b_sales_data ^
  --task pop_sales_data ^
  --task pop_funds
pause
