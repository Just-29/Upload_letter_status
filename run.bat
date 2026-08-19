@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%~dp0main.py" %*
  exit /b %ERRORLEVEL%
)
if exist ".venv\bin\python" (
  ".venv\bin\python" "%~dp0main.py" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1 && (
  py -3 "%~dp0main.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1 && (
  python "%~dp0main.py" %*
  exit /b %ERRORLEVEL%
)
where python3 >nul 2>&1 && (
  python3 "%~dp0main.py" %*
  exit /b %ERRORLEVEL%
)

echo Не найден Python. Создайте .venv или установите Python 3.
exit /b 1
