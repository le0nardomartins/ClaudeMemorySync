@echo off
rem Dois cliques: para a tarefa ClaudeMemorySync e inicia de novo,
rem para o sync passar a usar o codigo novo de src\.
setlocal
set TASK=ClaudeMemorySync

schtasks /query /tn %TASK% >nul 2>&1
if errorlevel 1 (
    echo Tarefa %TASK% nao instalada. Rode install_autostart.bat primeiro.
    goto fim
)

echo Parando %TASK%...
schtasks /end /tn %TASK% >nul 2>&1
rem Garante que nenhum main.py antigo ficou rodando (segura o .sync.lock)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -like '*%~dp0src\main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell -NoProfile -Command "Start-Sleep -Seconds 2"

echo Iniciando %TASK%...
schtasks /run /tn %TASK% >nul
if errorlevel 1 (
    echo Falha ao iniciar a tarefa.
    goto fim
)
echo Pronto. Log: %~dp0sync.log

:fim
echo.
pause
