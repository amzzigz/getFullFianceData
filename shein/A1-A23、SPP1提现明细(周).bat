@echo off
cd /d "%~dp0.."
py -3 main.py --env prod --task shein_funds --period weekly
pause
