@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0launcher.py"
  exit /b 0
)
if defined CERECITO_PYTHON (
  start "" "%CERECITO_PYTHON%" "%~dp0launcher.py"
  exit /b 0
)
echo Primera instalacion: ejecutar INSTALAR.bat. Despues usar ABRIR_CERECITO.bat.
pause
