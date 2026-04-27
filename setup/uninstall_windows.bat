@echo off
REM uninstall_windows.bat — 移除 local-agent（Windows）
REM 執行完畢後，請手動刪除 local-agent 資料夾本身

setlocal EnableDelayedExpansion

echo ============================================================
echo  local-agent 移除程式
echo ============================================================
echo.

REM ── 1. 刪除 Task Scheduler 任務 ─────────────────────────────
echo [1/3] 移除 Task Scheduler 任務...

schtasks /Query /TN "local-agent-evolve" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    schtasks /Delete /TN "local-agent-evolve" /F
    echo       已刪除：local-agent-evolve
) else (
    echo       略過（不存在）：local-agent-evolve
)

schtasks /Query /TN "local-agent-memory-audit" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    schtasks /Delete /TN "local-agent-memory-audit" /F
    echo       已刪除：local-agent-memory-audit
) else (
    echo       略過（不存在）：local-agent-memory-audit
)

schtasks /Query /TN "local-agent-babysit" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    schtasks /Delete /TN "local-agent-babysit" /F
    echo       已刪除：local-agent-babysit
) else (
    echo       略過（不存在）：local-agent-babysit
)

REM ── 2. 移除 Stop hook（~/.claude/settings.json）─────────────
echo.
echo [2/3] 移除 Stop hook from ~/.claude/settings.json...

set "SETTINGS=%USERPROFILE%\.claude\settings.json"
set "REMOVE_HOOK_PY=%TEMP%\remove_hook.py"

> "%REMOVE_HOOK_PY%" (
    echo import json, sys, pathlib
    echo p = pathlib.Path(r"%SETTINGS%"^)
    echo if not p.exists(^):
    echo     print("       settings.json 不存在，略過")
    echo     sys.exit(0^)
    echo cfg = json.loads(p.read_text(encoding="utf-8"^)^)
    echo hooks = cfg.get("hooks", {^}^)
    echo stop_hooks = hooks.get("Stop", []^)
    echo before = len(stop_hooks^)
    echo stop_hooks = [h for h in stop_hooks if "evolve" not in str(h^) and "local-agent" not in str(h^)]
    echo removed = before - len(stop_hooks^)
    echo if removed:
    echo     hooks["Stop"] = stop_hooks
    echo     cfg["hooks"] = hooks
    echo     p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False^), encoding="utf-8"^)
    echo     print(f"       已移除 {removed} 個 hook"^)
    echo else:
    echo     print("       無 local-agent hook，略過"^)
)

python "%REMOVE_HOOK_PY%"
del "%REMOVE_HOOK_PY%" >nul 2>&1

REM ── 3. 刪除 wrap_done_file ───────────────────────────────────
echo.
echo [3/3] 清除暫態旗標檔...

set "WRAP_DONE=%USERPROFILE%\.claude\.wrap_done.txt"
if exist "%WRAP_DONE%" (
    del "%WRAP_DONE%"
    echo       已刪除：%WRAP_DONE%
) else (
    echo       不存在（略過）：%WRAP_DONE%
)

REM ── 完成 ─────────────────────────────────────────────────────
echo.
echo ============================================================
echo  完成！請手動刪除 local-agent 資料夾：
echo  %~dp0..
echo ============================================================
echo.
pause
