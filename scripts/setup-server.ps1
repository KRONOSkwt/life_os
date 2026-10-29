param(
    [string]$Python = 'python',
    [switch]$Smoke
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvPath 'Scripts/python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        throw 'No se encontro Python. Instala Python 3.12 o posterior, o indica -Python con la ruta al ejecutable.'
    }
    & $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Se requiere Python 3.12 o posterior.' }
    & $Python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
}

# Always use this interpreter directly: activating a shell is not required.
& $venvPython -I (Join-Path $PSScriptRoot 'check-venv.py') $venvPath
if ($LASTEXITCODE -ne 0) {
    throw '.venv no es un entorno virtual aislado valido de Python 3.12 o posterior. No se instalaron dependencias.'
}

$serverPath = Join-Path $projectRoot 'apps/server'
& $venvPython -I -m pip --require-virtualenv install -e "${serverPath}[test]"
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias. Comprueba la conexion a PyPI.' }

Push-Location $serverPath
try {
    & $venvPython -I -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Las pruebas del servidor no pasaron.' }
} finally {
    Pop-Location
}

if ($Smoke) {
    & $venvPython -I (Join-Path $PSScriptRoot 'smoke-server.py')
    if ($LASTEXITCODE -ne 0) { throw 'La comprobacion HTTP del servidor no paso.' }
    & $venvPython -I (Join-Path $PSScriptRoot 'smoke-pairing.py')
    if ($LASTEXITCODE -ne 0) { throw 'La comprobacion HTTPS de vinculacion no paso.' }
}
