@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_f1_f20_platform_fees --period monthly
pause
