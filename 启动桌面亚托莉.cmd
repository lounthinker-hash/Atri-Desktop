@echo off
setlocal
set "atriPython=%~dp0.venv\Scripts\python.exe"
if exist "%atriPython%" goto check
if defined VIRTUAL_ENV set "atriPython=%VIRTUAL_ENV%\Scripts\python.exe"
if exist "%atriPython%" goto check
set "atriPython="
for /f "delims=" %%I in ('where.exe python.exe 2^>nul') do if not defined atriPython set "atriPython=%%I"
if not defined atriPython goto failed
:check
"%atriPython%" -X utf8 "%~dp0tools\check_local_setup.py" --desktop
if errorlevel 1 goto failed
for %%I in ("%atriPython%") do set "atriWindow=%%~dpIpythonw.exe"
if not exist "%atriWindow%" set "atriWindow=%atriPython%"
start "" "%atriWindow%" "%~dp0desktop_atri.pyw"
exit /b %errorlevel%
:failed
echo See docs\local-setup.md for installation. Try examples\motion_viewer.py for an asset-free preview.
pause
exit /b 1
