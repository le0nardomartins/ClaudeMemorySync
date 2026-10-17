# Faz o sync da memória do Claude rodar sozinho, a cada 5 minutos, sempre que
# você entrar no Windows. Roda escondido (pythonw) e grava o log em sync.log.
#
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1            # instala e já inicia
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall # remove
param([switch]$Uninstall, [int]$Interval = 300)

$ErrorActionPreference = 'Stop'
$TaskName = 'ClaudeMemorySync'
$project  = $PSScriptRoot

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tarefa $TaskName removida."
    return
}

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { throw 'pythonw.exe não encontrado no PATH. Instale o Python ou adicione-o ao PATH.' }

$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$project\src\main.py`" --loop --interval $Interval" -WorkingDirectory $project
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Se o processo cair, o Windows reinicia; sem limite de tempo de execução
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Sincroniza ~/.claude/claude_memory_sharing com os outros PCs a cada 5 minutos.' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Tarefa $TaskName instalada e iniciada (a cada $Interval s, ao entrar no Windows)."
Write-Host "Log: $project\sync.log"
