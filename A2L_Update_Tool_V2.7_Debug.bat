@echo off
setlocal
cd /d %~dp0

echo ========================================
echo A2L_Update_Tool_V2.7 Debug Mode
echo ========================================
echo.

if exist "%~dp0dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe" (
    echo [INFO] Running: dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe --autorun
    echo.
    "%~dp0dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe" --autorun
    echo.
    echo [INFO] Exit code: %errorlevel%
    goto end
)

if exist "%~dp0A2L_Update_Tool_main.py" (
    echo [INFO] Running: py A2L_Update_Tool_main.py --autorun
    echo.
    py "%~dp0A2L_Update_Tool_main.py" --autorun
    echo.
    echo [INFO] Exit code: %errorlevel%
    goto end
)

echo [ERROR] Cannot find dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe or A2L_Update_Tool_main.py

:end
echo.
echo [INFO] Press any key to close this window...
pause >nul
exit /b %errorlevel%
