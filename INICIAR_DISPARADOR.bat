@echo off
chcp 65001 >nul
cd /d "%~dp0"
start http://localhost:3050
python -u painel.py
pause
