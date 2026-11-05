@echo off
cd /d "%~dp0"
if not exist ".env" (
  echo No .env found - creating unique local secrets once.
  python scripts\bootstrap.py
  if errorlevel 1 (
    echo Bootstrap failed. Run it manually: python scripts\bootstrap.py
    pause
    exit /b 1
  )
  echo SAVE the admin password printed above. It is shown only once.
  pause
)
echo.
echo Starting Ninja Image Hub on http://127.0.0.1:8081
echo Keep this window open; press Ctrl+C to stop the panel.
echo If it fails with "address already in use", run: python scripts\doctor.py
echo.
python -m uvicorn app:app --host 127.0.0.1 --port 8081 --workers 1
pause
