@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo    ACTUALIZACION COMPLETA DE CERECITO
echo ==========================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%~dp0actualizar_cerecito_completo.py"
  goto fin
)
if defined CERECITO_PYTHON (
  "%CERECITO_PYTHON%" "%~dp0actualizar_cerecito_completo.py"
  goto fin
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0actualizar_cerecito_completo.py"
  goto fin
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0actualizar_cerecito_completo.py"
  goto fin
)
echo ERROR: No se encontro Python. Ejecuta primero INSTALAR.bat.
:fin
echo.
pause
