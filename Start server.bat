@echo off
rem Starts the app for the whole office. Leave this window open while the team is working.
rem Teammates open it with "Product Research.url" from the shared drive.
cd /d "%~dp0"
set HOST=0.0.0.0
set OPEN_BROWSER=0
".venv\Scripts\python.exe" app.py
echo.
echo The app has stopped. Close this window, or run "Start server.bat" again.
pause
