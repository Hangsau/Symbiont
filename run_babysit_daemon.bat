@echo off
cd /d "%~dp0"
for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do set "PYTHONW=%%i"
if not defined PYTHONW (
    for /f "delims=" %%i in ('where python.exe 2^>nul') do set "PYTHONW=%%i"
)
start "" "%PYTHONW%" src\babysit.py --daemon
