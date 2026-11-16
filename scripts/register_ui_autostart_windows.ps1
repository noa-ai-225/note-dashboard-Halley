<#
.SYNOPSIS
PCログオン時に管理UI＆内蔵4時刻スケジューラ(--lan)を裏で自動起動する登録スクリプト
#>
param(
    [int]$Port = 8766,
    [string]$TaskName = "HarryNoteLabUI",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] 自動起動タスク '$TaskName' を解除しました。"
    } else {
        Write-Host "[INFO] 自動起動タスク '$TaskName' は登録されていません。"
    }
    exit 0
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
$SysPyW = Get-Command pythonw.exe -ErrorAction SilentlyContinue
$PythonExe = if (Test-Path $VenvPy) { $VenvPy } elseif ($SysPyW) { $SysPyW.Source } else { "python.exe" }
$UiScript = Join-Path $RepoRoot "src\ui_server.py"

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$UiScript`" --lan --port $Port --no-browser" `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "ハリーのnote実験記録 モバイル管理UI＆定時観測サーバー自動起動" `
    -Force | Out-Null

Write-Host "========================================================"
Write-Host "[OK] PCログオン時のバックグラウンド自動起動を登録しました"
Write-Host "  タスク名 : $TaskName"
Write-Host "  待受URL  : http://<このPCのIP>:$Port"
Write-Host "========================================================"
