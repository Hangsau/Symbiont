@echo off
REM setup_windows.bat — 安裝 Symbiont（Windows）
REM 執行後 Symbiont 會在每次 Claude Code session 結束時自動觸發
REM
REM 需求：Python 3.10+、Claude Code CLI 已登入
REM 用法：在 Symbiont 目錄下執行，或告訴 Claude「幫我安裝 Symbiont」

setlocal EnableDelayedExpansion

REM ── 動態取得 Symbiont 根目錄（不寫死路徑）────────────────────
set "AGENT_DIR=%~dp0.."
REM 移除結尾反斜線並解析絕對路徑
pushd "%AGENT_DIR%"
set "AGENT_DIR=%CD%"
popd

echo ============================================================
echo  Symbiont 安裝程式
echo  路徑：%AGENT_DIR%
echo ============================================================
echo.

REM ── 1. 安裝 Python 依賴 ──────────────────────────────────────────
echo [1/4] 安裝 Python 依賴（pip install -r requirements.txt）...
python -m pip install -r "%AGENT_DIR%\requirements.txt" --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [錯誤] pip install 失敗，請確認 Python 3.10+ 已安裝
    pause
    exit /b 1
)
echo       完成

REM ── 2. 設定 Stop hook（settings.json）───────────────────────────
echo.
echo [2/4] 設定 Stop hook（~\.claude\settings.json）...

set "SETTINGS=%USERPROFILE%\.claude\settings.json"
set "HOOK_SCRIPT=%USERPROFILE%\.claude\scripts\symbiont-stop-hook.sh"
REM 將 AGENT_DIR 寫入 hook 的環境變數，確保路徑正確
set "ADD_HOOK_PY=%TEMP%\add_symbiont_hook.py"

> "%ADD_HOOK_PY%" (
    echo import json, pathlib, sys
    echo p = pathlib.Path(r"%SETTINGS%"^)
    echo hook_cmd = r'LOCAL_AGENT_DIR=\"%AGENT_DIR:\=/%\" bash \"%USERPROFILE:\=/%/.claude/scripts/symbiont-stop-hook.sh\"'
    echo if not p.exists(^):
    echo     cfg = {}
    echo else:
    echo     cfg = json.loads(p.read_text(encoding="utf-8"^)^)
    echo hooks = cfg.setdefault("hooks", {^}^)
    echo stop_hooks = hooks.setdefault("Stop", []^)
    echo # 避免重複加入
    echo already = any("symbiont-stop-hook" in str(h^) for h in stop_hooks^)
    echo if already:
    echo     print("       Stop hook 已存在，略過")
    echo     sys.exit(0^)
    echo stop_hooks.append({"hooks": [{"type": "command", "command": hook_cmd}]}^)
    echo p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False^), encoding="utf-8"^)
    echo print("       Stop hook 已加入"^)
)

python "%ADD_HOOK_PY%"
del "%ADD_HOOK_PY%" >nul 2>&1
echo       完成

REM ── 3. Task Scheduler — evolve 補跑 + babysit 每 2 分鐘 ──────────
echo.
echo [3/4] 設定 Task Scheduler...

REM evolve 補跑：開機時若 pending_evolve.txt 存在才跑
set "EVOLVE_CMD=cmd /c if exist \"%AGENT_DIR%\data\pending_evolve.txt\" (cd /d \"%AGENT_DIR%\" ^& python src\evolve.py)"

schtasks /Query /TN "symbiont-evolve" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    schtasks /Delete /TN "symbiont-evolve" /F >nul 2>&1
)
schtasks /Create /TN "symbiont-evolve" /TR "%EVOLVE_CMD%" /SC ONLOGON /DELAY 0002:00 /RU "%USERNAME%" /F >nul
if %ERRORLEVEL% EQU 0 (
    echo       symbiont-evolve 已設定（登入後 2 分鐘補跑）
) else (
    echo [警告] symbiont-evolve Task Scheduler 設定失敗（可手動執行）
)

REM memory_audit 補跑：開機時若 pending_audit.txt 存在才跑
set "AUDIT_CMD=cmd /c if exist \"%AGENT_DIR%\data\pending_audit.txt\" (cd /d \"%AGENT_DIR%\" ^& python src\memory_audit.py)"

schtasks /Query /TN "symbiont-memory-audit" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    schtasks /Delete /TN "symbiont-memory-audit" /F >nul 2>&1
)
schtasks /Create /TN "symbiont-memory-audit" /TR "%AUDIT_CMD%" /SC ONLOGON /DELAY 0002:30 /RU "%USERNAME%" /F >nul
if %ERRORLEVEL% EQU 0 (
    echo       symbiont-memory-audit 已設定（登入後 2.5 分鐘補跑）
) else (
    echo [警告] symbiont-memory-audit Task Scheduler 設定失敗（可手動執行）
)

REM babysit 每 2 分鐘（若 agents.yaml 存在才設定）
if exist "%AGENT_DIR%\data\agents.yaml" (
    set "BABYSIT_CMD=wscript //B \"%AGENT_DIR%\run_silent.vbs\" babysit"
    schtasks /Query /TN "symbiont-babysit" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        schtasks /Delete /TN "symbiont-babysit" /F >nul 2>&1
    )
    schtasks /Create /TN "symbiont-babysit" /TR "!BABYSIT_CMD!" /SC MINUTE /MO 2 /F >nul
    if %ERRORLEVEL% EQU 0 (
        echo       symbiont-babysit 已設定（每 2 分鐘）
    ) else (
        echo [警告] symbiont-babysit Task Scheduler 設定失敗
    )
) else (
    echo       agents.yaml 不存在，略過 babysit Task Scheduler
    echo       （如需啟用 babysit，設定 agents.yaml 後告訴 Claude「啟用 babysit」）
)

REM ── 4. 初始化 data/ 目錄 ─────────────────────────────────────────
echo.
echo [4/4] 初始化 data/ 目錄...
if not exist "%AGENT_DIR%\data" mkdir "%AGENT_DIR%\data"
if not exist "%AGENT_DIR%\data\state.json" echo {} > "%AGENT_DIR%\data\state.json"
if not exist "%AGENT_DIR%\data\teaching_state" mkdir "%AGENT_DIR%\data\teaching_state"
echo       完成

REM ── 完成 ─────────────────────────────────────────────────────────
echo.
echo ============================================================
echo  安裝完成！
echo.
echo  下一步：
echo    編輯 config.yaml 啟用功能：
echo      memory_audit.enabled: true    ← 啟用每日記憶維護
echo    複製 data\agents.example.yaml → data\agents.yaml 填入 agent 設定
echo    立即驗證 → python src\evolve.py --dry-run
echo    查看操作手冊 → docs\COMMANDS.md
echo ============================================================
echo.
pause
