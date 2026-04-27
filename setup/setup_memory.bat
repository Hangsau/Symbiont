@echo off
REM setup_memory.bat — 初始化 Claude memory 系統骨架（Windows）
REM
REM 此腳本由 Claude 代為執行，或手動在 local-agent 目錄下執行
REM 會在 Claude Code 的主專案下建立 memory/ 目錄結構

setlocal EnableDelayedExpansion

echo ============================================================
echo  local-agent memory 系統初始化
echo ============================================================
echo.

REM ── 找出 primary_project memory 路徑 ────────────────────────────
REM 用 Python + config_loader 解析，確保與 memory_audit.py 邏輯一致
set "AGENT_DIR=%~dp0.."
pushd "%AGENT_DIR%"
set "AGENT_DIR=%CD%"
popd

set "FIND_PATH_PY=%TEMP%\find_memory_path.py"
> "%FIND_PATH_PY%" (
    echo import sys
    echo sys.path.insert(0, r"%AGENT_DIR%"^)
    echo from src.utils.config_loader import load_config, get_path
    echo cfg = load_config(^)
    echo print(get_path(cfg, "memory_dir"^)^)
)

for /f "delims=" %%i in ('python "%FIND_PATH_PY%" 2^>nul') do set "MEMORY_DIR=%%i"
del "%FIND_PATH_PY%" >nul 2>&1

if "%MEMORY_DIR%"=="" (
    echo [錯誤] 無法解析 memory 路徑，請確認 Python 已安裝且 local-agent 安裝完成
    pause
    exit /b 1
)

echo  memory 目錄：%MEMORY_DIR%
echo.

REM ── 建立目錄結構 ─────────────────────────────────────────────────
if not exist "%MEMORY_DIR%" mkdir "%MEMORY_DIR%"
if not exist "%MEMORY_DIR%\archive" mkdir "%MEMORY_DIR%\archive"
if not exist "%MEMORY_DIR%\thoughts" mkdir "%MEMORY_DIR%\thoughts"
echo [1/3] 目錄建立完成

REM ── 建立 MEMORY.md（空索引）──────────────────────────────────────
if not exist "%MEMORY_DIR%\MEMORY.md" (
    > "%MEMORY_DIR%\MEMORY.md" (
        echo # Memory Index
        echo.
        echo ^<!-- 每行格式：- [標題](檔名.md^) — 一行描述 ^-->
        echo ^<!-- local-agent memory_audit.py 自動維護此索引 ^-->
    )
    echo [2/3] MEMORY.md 已建立
) else (
    echo [2/3] MEMORY.md 已存在，略過
)

REM ── 複製 SCHEMA.md（從 local-agent 模板）────────────────────────
set "SCHEMA_SRC=%AGENT_DIR%\docs\MEMORY_SCHEMA.md"
set "SCHEMA_DEST=%MEMORY_DIR%\SCHEMA.md"

if not exist "%SCHEMA_DEST%" (
    if exist "%SCHEMA_SRC%" (
        copy "%SCHEMA_SRC%" "%SCHEMA_DEST%" >nul
        echo [3/3] SCHEMA.md 已複製
    ) else (
        echo [3/3] SCHEMA.md 模板未找到（略過，可手動建立）
    )
) else (
    echo [3/3] SCHEMA.md 已存在，略過
)

REM ── 啟用 memory_audit ────────────────────────────────────────────
echo.
echo  正在啟用 config.yaml memory_audit.enabled...
set "ENABLE_PY=%TEMP%\enable_memory_audit.py"
> "%ENABLE_PY%" (
    echo import re, pathlib
    echo p = pathlib.Path(r"%AGENT_DIR%\config.yaml"^)
    echo content = p.read_text(encoding="utf-8"^)
    echo content = re.sub(r"enabled:\s*false", "enabled: true", content, count=1^)
    echo p.write_text(content, encoding="utf-8"^)
    echo print("       enabled: true"^)
)
python "%ENABLE_PY%"
del "%ENABLE_PY%" >nul 2>&1

REM ── 完成 ─────────────────────────────────────────────────────────
echo.
echo ============================================================
echo  memory 系統初始化完成！
echo.
echo  下一步：
echo    手動執行驗收 → python src\memory_audit.py --dry-run
echo    查看記憶目錄 → %MEMORY_DIR%
echo ============================================================
echo.
pause
