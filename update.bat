@echo off
setlocal
cd /d "%~dp0"
title Ozon Assistant Update
timeout /t 3 /nobreak >nul
if not exist ".venv\Scripts\python.exe" goto no_runtime
.venv\Scripts\python.exe updater.py
if errorlevel 1 goto update_error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto update_error
start "" "%~dp0run.bat"
goto end

:no_runtime
echo Python environment not found. Start run.bat first.
pause
goto end

:update_error
echo Update failed. Check your internet connection and try again.
pause

:end
endlocal
