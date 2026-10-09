# Instala o gateway como serviço do Windows via NSSM (https://nssm.cc) com auto-restart e log rotativo.
# NÃO TESTADO. Rodar como Administrador. Pré-requisitos: NSSM no PATH, .venv criado, .env preenchido.
#
# IMPORTANTE (login por navegador): o serviço precisa rodar COM A CONTA DO USUÁRIO que fez o login humano
# (python -m scripts.login), pois o perfil do navegador e o cache de token do SDK ficam no perfil dessa conta.
# Em AUTH_MODE=browser use -User/-Password; LocalSystem NÃO enxerga esse perfil.
param([string]$Name = "DatabricksGateway", [int]$Port = 8080, [string]$User = "", [string]$Password = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { throw "NSSM não encontrado no PATH (https://nssm.cc)" }
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null

nssm install $Name powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$root\deploy\run-gateway.ps1`" -Port $Port"
nssm set $Name AppDirectory $root
nssm set $Name Start SERVICE_AUTO_START
nssm set $Name AppExit Default Restart            # reinicia sempre que o processo cair
nssm set $Name AppRestartDelay 5000
nssm set $Name AppThrottle 10000                  # se cair em <10s repetidamente, espera antes de tentar
nssm set $Name AppStdout "$root\logs\gateway.out.log"
nssm set $Name AppStderr "$root\logs\gateway.err.log"
nssm set $Name AppRotateFiles 1
nssm set $Name AppRotateOnline 1
nssm set $Name AppRotateBytes 10485760            # 10 MB
if ($User) { nssm set $Name ObjectName $User $Password }
nssm start $Name
Write-Host "Serviço $Name instalado. Teste: curl http://localhost:$Port/ready"
