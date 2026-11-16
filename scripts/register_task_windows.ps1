<#
.SYNOPSIS
Windows タスクスケジューラへ 9時・14時・20時・23時 の4回定時観測タスクを登録・解除するスクリプト

.EXAMPLE
.\scripts\register_task_windows.ps1
.\scripts\register_task_windows.ps1 -Times "09:00,14:00,20:00,23:00"
.\scripts\register_task_windows.ps1 -Unregister
#>
param(
    [string]$Times = "09:00,14:00,20:00,23:00",
    [string]$TaskName = "HarryNoteLabBackup",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] タスク '$TaskName' を解除しました。"
    } else {
        Write-Host "[INFO] タスク '$TaskName' は登録されていません。"
    }
    exit 0
}

$RunnerScript = Join-Path $PSScriptRoot "run_backup_scheduled.ps1"
if (-not (Test-Path $RunnerScript)) {
    throw "実行スクリプトが見つかりません: $RunnerScript"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunnerScript`""

$Triggers = @()
foreach ($t in ($Times -split ",")) {
    $trimmed = $t.Trim()
    if ($trimmed -match "^(?:[01]?\d|2[0-3]):[0-5]\d$") {
        $Triggers += (New-ScheduledTaskTrigger -Daily -At $trimmed)
    }
}

if ($Triggers.Count -eq 0) {
    throw "有効な時刻が指定されていません: $Times"
}

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Description "ハリーのnote実験記録 (09:00/14:00/20:00/23:00 定時観測バックアップ)" `
    -Force | Out-Null

Write-Host "========================================================"
Write-Host "[OK] Windows タスクスケジューラに登録しました"
Write-Host "  タスク名 : $TaskName"
Write-Host "  実行時刻 : 毎日 $Times"
Write-Host "========================================================"
