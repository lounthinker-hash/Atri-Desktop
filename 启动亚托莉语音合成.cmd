@echo off
setlocal
set "atriVoice=%~dp0tts\.venv\Scripts\python.exe"
if not exist "%atriVoice%" goto missing
"%atriVoice%" -X utf8 "%~dp0tools\check_local_setup.py" --voice
if errorlevel 1 goto failed
"%atriVoice%" -X utf8 "%~dp0tts\web.py" --open
if errorlevel 1 goto failed
exit /b 0
:missing
echo Missing tts\.venv\Scripts\python.exe.
:failed
echo See docs\local-setup.md for the optional local voice service.
pause
exit /b 1
