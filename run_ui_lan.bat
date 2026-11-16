@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

set "PYTHON_BIN=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_BIN=.venv\Scripts\python.exe"

"%PYTHON_BIN%" src\ui_server.py --lan %*
if %errorlevel% neq 0 pause
