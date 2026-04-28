@echo off
cd /d "%~dp0"
start "" /min python src\babysit.py --daemon
