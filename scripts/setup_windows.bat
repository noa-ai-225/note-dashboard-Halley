@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0\.."

echo ==============================================================
echo  ハリーのnote実験記録ラボ : Windows 初期セットアップ
echo ==============================================================

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Python 仮想環境 (.venv^) を作成中...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] 仮想環境の作成に失敗しました。Python 3.10以上を確認してください。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 既存の .venv を使用します。
)

echo [2/3] 依存パッケージをインストール中...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] パッケージのインストールに失敗しました。
    pause
    exit /b 1
)

echo [3/3] Playwright 用 Chromium ブラウザを導入中...
".venv\Scripts\python.exe" -m playwright install chromium
if %errorlevel% neq 0 (
    echo [ERROR] Chromium のインストールに失敗しました。
    pause
    exit /b 1
)

echo.
echo ==============================================================
echo  [OK] セットアップが完了しました！
echo  次は run_ui_lan.bat または run_login.bat から
echo  初回ログイン認証を行ってください。
echo ==============================================================
pause
