@echo off
setlocal
cd /d "%~dp0"
title Ozon Assistant

where py.exe >nul 2>nul
if errorlevel 1 goto try_python
set "PYTHON_CMD=py -3.14"
goto check_runtime

:try_python
where python.exe >nul 2>nul
if errorlevel 1 goto no_python
set "PYTHON_CMD=python"

:check_runtime
%PYTHON_CMD% -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul
if errorlevel 1 goto install_runtime
goto setup

:install_runtime
where py.exe >nul 2>nul
if errorlevel 1 goto no_python
echo Installing Python 3.14...
py install 3.14
if errorlevel 1 goto no_python
set "PYTHON_CMD=py -3.14"

:setup
if exist ".venv\Scripts\python.exe" goto start_app
echo Creating the application environment...
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto setup_error
echo Installing application components. Please wait...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto setup_error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto setup_error

:start_app
echo Starting Ozon Assistant...
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe app.py
if errorlevel 1 goto app_error
goto end

:no_python
echo Python 3.14 was not found.
echo Open Command Prompt and run: py install 3.14
pause
goto end

:setup_error
echo Setup failed. Check your internet connection and try again.
pause
goto end

:app_error
echo The application stopped with an error.
pause

:end
endlocal
