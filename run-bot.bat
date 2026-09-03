@echo off
REM Keeps the attendance bot running: if main.py ever exits (crash, machine
REM reboot + auto-logon, power loss at a site), wait a few seconds and start it
REM again. Output goes to bot-restart.log. Run this instead of "python main.py"
REM for unattended use; register it to start at logon with install-autostart.ps1.
cd /d "%~dp0"
:loop
echo [%date% %time%] starting main.py >> bot-restart.log
".venv\Scripts\python.exe" main.py >> bot-restart.log 2>&1
echo [%date% %time%] main.py exited (code %errorlevel%), restarting in 10s >> bot-restart.log
timeout /t 10 /nobreak >nul
goto loop
