@echo off
rem phoneuse's CLI in the installed or portable Windows app (windows/scripts/package.ps1): runs bin\phoneuse
rem with the app's Python, core\.venv when the installer made one from the system's Python,
rem else the standalone Python it ships. The portable app keeps its settings in data\.
setlocal
set "CORE=%~dp0.."
if exist "%CORE%\..\portable" if not defined PHONEUSE_CONFIG_DIR set "PHONEUSE_CONFIG_DIR=%CORE%\..\data"
set "PY=%CORE%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%CORE%\python\python.exe"
set PYTHONUTF8=1
"%PY%" "%~dp0phoneuse" %*
exit /b %ERRORLEVEL%
