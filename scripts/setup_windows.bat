@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0\.."

echo ==============================================================
echo  ハリーのnote実験記録ラボ : かんたん初期セットアップ
echo ==============================================================
echo.

python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python 3.10 以上が見つかりません。
    echo.
    echo 以下の手順で Python をインストールしてから、もう一度このファイルを
    echo ダブルクリックしてください:
    echo   1. https://www.python.org/downloads/ を開く
    echo   2. 黄色の「Download Python」ボタンからインストーラをダウンロード
    echo   3. 起動後の最初の画面で、一番下の
    echo      『Add python.exe to PATH』に必ずチェックを入れる！
    echo   4. 「Install Now」をクリックして完了後に再度実行
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 専用環境 (.venv^) を作成中...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] 専用環境の作成に失敗しました。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 既存の .venv を使用します。
)

echo [2/3] 必要なライブラリをインストール中（数十秒かかります）...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] ライブラリのインストールに失敗しました。ネット接続を確認してください。
    pause
    exit /b 1
)

echo [3/3] 観測用ブラウザ (Chromium^) を導入中（1〜2分かかります）...
".venv\Scripts\python.exe" -m playwright install chromium
if %errorlevel% neq 0 (
    echo [ERROR] ブラウザの導入に失敗しました。
    pause
    exit /b 1
)

echo.
echo ==============================================================
echo  [OK] セットアップがすべて完了しました！
echo.
echo  次はフォルダ内の『run_ui_lan.bat』をダブルクリックしてください。
echo  ブラウザが開くので、noteに1回ログインすれば準備完了です！
echo ==============================================================
pause
