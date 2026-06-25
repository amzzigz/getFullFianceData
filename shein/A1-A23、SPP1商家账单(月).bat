@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_merchant_billing --period monthly
pause
