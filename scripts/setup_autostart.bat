@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0\.."

echo ==============================================================
echo  PC起動時のスマホ用サーバー自動起動を登録します
echo ==============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\register_ui_autostart_windows.ps1"
echo.
echo 登録を解除したい場合は、次を実行してください:
echo   powershell -ExecutionPolicy Bypass -File .\scripts\register_ui_autostart_windows.ps1 -Unregister
pause
