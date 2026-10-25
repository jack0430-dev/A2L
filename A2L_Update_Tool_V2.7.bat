@echo off
setlocal
cd /d %~dp0

if exist "%~dp0dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe" (
    start "" /min "%~dp0dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe" --autorun --hidden
    exit /b 0
)

if exist "%~dp0A2L_Update_Tool_main.py" (
    start "" /min py "%~dp0A2L_Update_Tool_main.py" --autorun --hidden
    exit /b 0
)

echo [ERROR] Cannot find dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe or A2L_Update_Tool_main.py
exit /b 1
