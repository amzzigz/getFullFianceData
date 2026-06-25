@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_f1_f20_funds --period monthly
pause
