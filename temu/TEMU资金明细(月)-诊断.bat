@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task temu_fund_details --period monthly --diagnose
pause
