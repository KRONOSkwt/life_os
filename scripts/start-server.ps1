param(
    [int]$Port = 8000,
    [string]$BindAddress = '127.0.0.1',
    [switch]$Https,
    [string]$TlsDirectory = ''
)

$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Puerto invalido.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Ejecuta primero scripts/setup-server.ps1.' }
if (-not $Https -and [string]::IsNullOrWhiteSpace($env:LIFE_OS_DEV_TOKEN)) {
    throw 'Configura LIFE_OS_DEV_TOKEN en esta sesion antes de iniciar el servidor.'
}

& $venvPython -I (Join-Path $PSScriptRoot 'check-venv.py') (Join-Path $projectRoot '.venv')
if ($LASTEXITCODE -ne 0) { throw 'El interprete no pertenece al entorno virtual del proyecto.' }

if ($Https) {
    if (-not $PSBoundParameters.ContainsKey('Port')) { $Port = 8443 }
    if ([string]::IsNullOrWhiteSpace($TlsDirectory)) { $TlsDirectory = Join-Path $projectRoot '.data/tls' }
    & $venvPython -I -m life_os.manage serve --host $BindAddress --port $Port --tls-dir $TlsDirectory
} else {
    $parsedAddress = $null
    if (-not [System.Net.IPAddress]::TryParse($BindAddress, [ref]$parsedAddress) -or
        -not [System.Net.IPAddress]::IsLoopback($parsedAddress)) {
        throw 'El modo HTTP de desarrollo solo permite una direccion loopback. Usa -Https para acceso de red.'
    }
    $env:LIFE_OS_MODE = 'development'
    & $venvPython -I -m uvicorn life_os.api:create_app --factory --host $BindAddress --port $Port --no-proxy-headers
}
if ($LASTEXITCODE -ne 0) { throw 'El servidor termino con un error.' }
