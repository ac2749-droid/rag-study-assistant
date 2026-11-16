@echo off
rem Double-click this file to start the app and open it in your browser.
rem Closing this window stops the app. (Set NO_BROWSER=1 to skip opening the browser.)
setlocal
cd /d "%~dp0"
title Anthony's Study Assistant
set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%"

if not exist ".venv\Scripts\python.exe" goto no_venv
if not exist ".env" goto no_env

rem Is something already listening on the port? 0 = this app, 2 = another program, 1 = free.
powershell -NoProfile -Command "$l = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $l) { exit 1 }; try { $h = Invoke-RestMethod '%URL%/health' -TimeoutSec 3; if ($h.status -eq 'ok') { exit 0 } } catch {}; exit 2"
set "PORTSTATE=%errorlevel%"
if "%PORTSTATE%"=="0" goto already_running
if "%PORTSTATE%"=="2" goto port_busy

echo.
echo   Starting Anthony's Study Assistant...
echo   Your browser will open in a few seconds.
echo   Keep this window open while you use the app. Close it to stop the app.
echo.
if not defined NO_BROWSER start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing '%URL%/health' -TimeoutSec 2 | Out-Null; Start-Process '%URL%'; break } catch { Start-Sleep -Milliseconds 500 } }"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
echo.
echo   The app has stopped. If that was not on purpose, read the messages above.
pause
exit /b 0

:already_running
echo The app is already running. Opening it in your browser...
if not defined NO_BROWSER start "" "%URL%"
ping -n 4 127.0.0.1 >nul
exit /b 0

:port_busy
echo.
echo   Port %PORT% is already used by another program, so the app cannot start.
echo   Close that program (or restart your computer) and try again.
echo.
pause
exit /b 1

:no_venv
echo.
echo   The app is not set up yet: the ".venv" folder is missing.
echo   Open README.md and follow the "Setup" section once, then try again.
echo.
pause
exit /b 1

:no_env
echo.
echo   No ".env" file was found, so the app has no Gemini API key.
echo   Copy ".env.example" to ".env" and put your key in it.
if exist ".env.txt" echo   NOTE: you have ".env.txt". Windows hid the real extension; rename it to ".env".
echo.
pause
exit /b 1
