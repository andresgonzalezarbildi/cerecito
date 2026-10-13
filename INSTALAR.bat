@echo off
setlocal
cd /d "%~dp0"
if defined CERECITO_PYTHON (
  "%CERECITO_PYTHON%" -m venv .venv
) else (
  py -3 -m venv .venv
)
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
".venv\Scripts\python.exe" -c "import sys;sys.path.insert(0,'src/backend');from runtime_paths import initialize;print(initialize())"
if errorlevel 1 goto error
echo Instalacion lista. Abrir ABRIR_CERECITO.bat.
pause
exit /b 0
:error
echo No se completo la instalacion. Se necesita Python 3.11 o posterior y acceso a las dependencias.
pause
exit /b 1
