@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task tiktok_sales_data --period monthly
pause
