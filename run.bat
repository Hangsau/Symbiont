@echo off
:: Symbiont Windows 入口腳本
:: 用途：Task Scheduler 呼叫 / 手動執行
::
:: 用法：
::   run.bat evolve [--dry-run]
::   run.bat memory_audit [--dry-run]
::   run.bat babysit
::
:: Task Scheduler 範例（開機補跑 evolve）：
::   Action: C:\claudehome\projects\Symbiont\run.bat
::   Arguments: evolve
::   Start in: C:\claudehome\projects\Symbiont

:: ── 確保 claude CLI 在 PATH ────────────────────────────────────
:: 優先找 npm global（winget/npm 安裝路徑）
set "PATH=%APPDATA%\npm;%LOCALAPPDATA%\AnthropicClaude;%PATH%"

:: ── 切換到腳本所在目錄（確保相對路徑正確）────────────────────
cd /d "%~dp0"

:: ── 參數檢查 ───────────────────────────────────────────────────
if "%~1"=="" (
    echo Usage: run.bat ^<script^> [options]
    echo   Scripts: evolve, memory_audit, babysit
    echo   Options: --dry-run
    exit /b 1
)

:: ── 執行 ───────────────────────────────────────────────────────
python "src\%~1.py" %2 %3 %4
exit /b %ERRORLEVEL%
