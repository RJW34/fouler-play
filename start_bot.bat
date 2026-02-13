@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0start_one_touch.bat"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
