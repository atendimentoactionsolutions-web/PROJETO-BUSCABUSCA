@echo off
chcp 65001 >nul
cd /d "C:\Users\Mundo Apple\.gemini\antigravity\scratch\disparador-fornecedores"
start http://localhost:3050
"C:\Users\Mundo Apple\.gemini\antigravity\scratch\python-env\python.exe" -u painel.py
pause
