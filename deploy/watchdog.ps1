# Vigia externo: se o gateway parar de responder, reinicia o serviço e avisa no webhook. NÃO TESTADO.
# Agendar a cada 1-2 min: schtasks /Create /SC MINUTE /MO 1 /TN GatewayWatchdog /RU SYSTEM /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <raiz>\deploy\watchdog.ps1"
param([string]$Service = "DatabricksGateway", [string]$Url = "http://localhost:8080", [int]$Failures = 3,
      [string]$WebhookUrl = $env:ALERT_WEBHOOK_URL)
$state = Join-Path $env:TEMP "gateway-watchdog.count"
function Notify($msg) {
  Write-Host $msg
  if ($WebhookUrl) { try { Invoke-RestMethod -Uri $WebhookUrl -Method Post -ContentType "application/json" -Body (@{text = $msg} | ConvertTo-Json) | Out-Null } catch {} }
}
try { $ok = (Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 5).status -eq "ok" } catch { $ok = $false }
if ($ok) { if (Test-Path $state) { Remove-Item $state -Force }; exit 0 }
$n = 1 + [int]((Get-Content $state -ErrorAction SilentlyContinue) -as [int])
Set-Content $state $n
if ($n -ge $Failures) {
  Notify "[watchdog] gateway sem resposta há $n checagens; reiniciando o serviço $Service"
  Restart-Service $Service -Force
  Remove-Item $state -Force
}
