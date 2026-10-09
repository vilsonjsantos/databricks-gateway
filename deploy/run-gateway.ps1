# Sobe o gateway (usado pelo serviço e para testes manuais). NÃO TESTADO na máquina da empresa.
# Uso: powershell -ExecutionPolicy Bypass -File deploy\run-gateway.ps1 [-Port 8080]
param([int]$Port = 8080, [string]$BindHost = "0.0.0.0")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path logs | Out-Null
$py = Join-Path $root ".venv\Scripts\python.exe"
# HTTPS: se GATEWAY_SSL_CERT/GATEWAY_SSL_KEY existirem no .env do sistema, o uvicorn serve TLS direto;
# caso contrário, colocar um proxy reverso com TLS na frente (ver docs/DEPLOY.md).
$tls = @()
if ($env:GATEWAY_SSL_CERT -and $env:GATEWAY_SSL_KEY) { $tls = @("--ssl-certfile", $env:GATEWAY_SSL_CERT, "--ssl-keyfile", $env:GATEWAY_SSL_KEY) }
& $py -m uvicorn gateway.main:app --host $BindHost --port $Port --no-access-log @tls
