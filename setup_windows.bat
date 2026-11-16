@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
echo RSI-CCI setup - Python 3.12 recommended.
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -c "import sys; print(sys.version)" >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -m venv .venv
if errorlevel 1 goto venv_failed
goto install
:use_python
python -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,14)" >nul 2>nul
if errorlevel 1 (
  echo Install 64-bit Python 3.12 from python.org and enable Add Python to PATH.
  pause
  exit /b 1
)
python -m venv .venv
if errorlevel 1 goto venv_failed
:install
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment creation failed.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Check the error above and your Internet connection.
  pause
  exit /b 1
)
echo Setup complete. Run start_windows.bat.
pause
exit /b 0
:venv_failed
echo Virtual environment creation failed. Check Python installation and folder permissions.
pause
exit /b 1
