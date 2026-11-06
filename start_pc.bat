@echo off
cd /d "%~dp0"
py pc_client.py
if errorlevel 1 (
  echo.
  echo If Python packages are missing, run:
  echo py -m pip install -r requirements.txt
)
pause
