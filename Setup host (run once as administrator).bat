@echo off
rem One-time setup on the PC that runs the app: lets other office PCs reach it on port 5000.
rem Right-click this file and choose "Run as administrator".
net session >nul 2>&1
if errorlevel 1 (
  echo This needs administrator rights. Right-click the file and choose "Run as administrator".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="Product Research app" >nul 2>&1
netsh advfirewall firewall add rule name="Product Research app" dir=in action=allow protocol=TCP localport=5000 profile=any
echo.
echo Done. Other PCs on the office network can now open the app while "Start server.bat" is running.
pause
