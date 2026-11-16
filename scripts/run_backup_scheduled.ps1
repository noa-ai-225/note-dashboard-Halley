# ハリーのnote実験記録 定時実行スクリプト (Windowsタスクスケジューラ用)
$ErrorActionPreference = "Continue"

$BaseDir = Split-Path -Parent $PSScriptRoot
Set-Location $BaseDir

$LogDir = Join-Path $BaseDir "data\logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$LogFile = Join-Path $LogDir "scheduled_backup.log"

$VenvPython = Join-Path $BaseDir ".venv\Scripts\python.exe"
$PythonBin = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

$startTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "========================================================" -Encoding utf8
Add-Content -Path $LogFile -Value "[定時観測開始] $startTime" -Encoding utf8

$env:PYTHONIOENCODING = "utf-8"
& $PythonBin (Join-Path $BaseDir "src\main.py") --type scheduled 2>&1 | Out-String | Add-Content -Path $LogFile -Encoding utf8

$endTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[定時観測終了] $endTime`n" -Encoding utf8
