param([switch]$NoPause, [switch]$DetectOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Find-CerecitoPython {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:CERECITO_PYTHON) { $candidates.Add($env:CERECITO_PYTHON) }
    $candidates.Add((Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))
    foreach ($name in @('python', 'python3', 'py')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($command) { $candidates.Add($command.Source) }
    }
    foreach ($root in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        if (Test-Path -LiteralPath $root) {
            foreach ($version in Get-ChildItem -LiteralPath $root) {
                $key = Join-Path $version.PSPath 'InstallPath'
                if (Test-Path -LiteralPath $key) {
                    $install = Get-Item -LiteralPath $key
                    $executable = $install.GetValue('ExecutablePath')
                    if ($executable) { $candidates.Add($executable) }
                    $directory = $install.GetValue('')
                    if ($directory) { $candidates.Add((Join-Path $directory 'python.exe')) }
                }
            }
        }
    }
    foreach ($root in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:ProgramData)) {
        if ($root) {
            foreach ($directory in @('miniconda3', 'anaconda3', 'miniforge3', 'mambaforge')) {
                $candidates.Add((Join-Path $root "$directory\python.exe"))
            }
        }
    }
    foreach ($root in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)) {
        if (Test-Path -LiteralPath $root) {
            foreach ($directory in Get-ChildItem -LiteralPath $root -Directory -Filter 'Python*') {
                $candidates.Add((Join-Path $directory.FullName 'python.exe'))
            }
        }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        # WindowsApps contains Store aliases, not installed interpreters.
        if ($candidate -match '\\WindowsApps\\' -or !(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $arguments = @('-c', 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)')
        if ([IO.Path]::GetFileName($candidate) -eq 'py.exe') { $arguments = @('-3') + $arguments }
        & $candidate @arguments
        if ($LASTEXITCODE -eq 0) { return $candidate }
        if ($env:CERECITO_PYTHON -and $candidate -eq $env:CERECITO_PYTHON) {
            throw 'CERECITO_PYTHON debe indicar Python 3.11 o posterior.'
        }
    }
    throw 'No se encontro Python 3.11 o posterior. Instala Python o indica su ejecutable en CERECITO_PYTHON y vuelve a ejecutar INSTALAR.bat.'
}

try {
    $pythonExecutable = Find-CerecitoPython
    Write-Host "Python encontrado: $pythonExecutable"
    if ($DetectOnly) { exit 0 }
    $venvExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $venvExecutable)) {
        $arguments = @('-m', 'venv', '.venv')
        if ([IO.Path]::GetFileName($pythonExecutable) -eq 'py.exe') { $arguments = @('-3') + $arguments }
        & $pythonExecutable @arguments
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno de Python.' }
    }
    & $venvExecutable -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias. Revisa el error de descarga o instalacion que aparece arriba.' }
    & $venvExecutable -c "import sys;sys.path.insert(0,'src/backend');from runtime_paths import initialize;from workspace_store import Store;Store(initialize())"
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron preparar los datos locales. Revisa el error que aparece arriba.' }
    Write-Host 'Instalacion lista. Abri ABRIR_CERECITO.bat.'
    if (!$NoPause) { Read-Host 'Presiona Enter para cerrar' | Out-Null }
    exit 0
} catch {
    Write-Host "No se completo la instalacion: $($_.Exception.Message)" -ForegroundColor Red
    if (!$NoPause) { Read-Host 'Presiona Enter para cerrar' | Out-Null }
    exit 1
}
