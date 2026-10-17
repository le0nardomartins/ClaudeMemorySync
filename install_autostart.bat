@echo off
rem Dois cliques: instala o sync automatico (a cada 15 min, ao entrar no Windows).
rem Para remover: install_autostart.bat -Uninstall
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autostart.ps1" %*
echo.
pause
