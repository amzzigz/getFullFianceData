@echo off
cd /d "%~dp0..\.."
py -3 main.py --env prod --task tiktok_email_income --period monthly
pause
