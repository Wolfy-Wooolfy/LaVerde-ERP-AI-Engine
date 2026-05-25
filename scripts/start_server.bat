@echo off
setlocal

:: Navigate to repo root relative to this script's location
cd /d "%~dp0.."

echo ============================================================
echo  LaVerde ERP AI Engine -- Server Start
echo  Decision 6.4: kill python, check port, clear cache, start
echo ============================================================
echo.

:: Step 1: Kill all Python processes
:: NOTE: /T kills child processes too. This affects ALL python.exe on this
:: machine -- Jupyter, scripts, anything running Python. Intentional for
:: a dev tool; be aware if other Python work is open.
echo [1/4] Killing all Python processes...
taskkill /F /IM python.exe /T >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo       Done -- Python processes terminated.
) else (
    echo       No Python processes were running. OK, continuing.
)
echo.

:: Step 2: Wait 2s for port 8000 to release, then verify it is free
echo [2/4] Waiting for port 8000 to clear (2s)...
timeout /t 2 /nobreak >nul
netstat -ano | findstr "LISTENING" | findstr ":8000 " >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo.
    echo  WARNING: Port 8000 is still in use ^(another process holds it^).
    echo  uvicorn will likely fail with error 10048 ^(address already in use^).
    echo  Close the process on port 8000 then press any key to try anyway,
    echo  or close this window to abort.
    echo.
    pause
) else (
    echo       Port 8000 is free.
)
echo.

:: Step 3: Clear all __pycache__ directories in the repo
echo [3/4] Clearing __pycache__ directories...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" (
        rmdir /s /q "%%d"
        echo       Removed: %%d
    )
)
echo       Done -- bytecode cache cleared.
echo.

:: Step 4: Start uvicorn (no --reload per Decision 6.4)
echo [4/4] Starting uvicorn on 0.0.0.0:8000 ...
echo       Ctrl+C to stop. Close this window to kill the server.
echo.
echo ============================================================
echo.
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

echo.
echo ============================================================
echo  Server stopped.
pause
