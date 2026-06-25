@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --period monthly ^
  --task shein_a1y_a4y_report_bill ^
  --task shein_sales_ledger ^
  --task shein_f1_f20_merchant_billing ^
  --task shein_f1_f20_sales_data ^
  --task shein_f1_f20_funds ^
  --task shein_f1_f20_platform_fees ^
  --task shein_merchant_billing ^
  --task shein_funds ^
  --task shein_balance_records ^
  --task shein_platform_fees ^
  --task shein_a1b_a4b_sales_data ^
  --task pop_sales_data ^
  --task pop_merchant_billing ^
  --task pop_balance_records ^
  --task pop_funds
pause
