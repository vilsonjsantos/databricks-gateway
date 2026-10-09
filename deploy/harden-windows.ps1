# Ajustes do Windows para uma máquina que precisa ficar ligada 24/7. NÃO TESTADO. Rodar como Administrador.
# Revise antes: alguns itens podem conflitar com política da TI (GPO).
$ErrorActionPreference = "Continue"
# Sem suspensão/hibernação/desligar tela-disco
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change disk-timeout-ac 0
powercfg /hibernate off
# Sincronismo de relógio (token OAuth e assinatura HMAC dependem de hora correta)
w32tm /config /update /syncfromflags:domhier 2>$null
Start-Service w32time -ErrorAction SilentlyContinue
# Atualizações automáticas não reiniciam sozinhas em horário de atendimento (ajustar as horas ativas)
New-Item -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Name ActiveHoursStart -Value 6
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Name ActiveHoursEnd -Value 22
# Firewall: libera a porta do gateway (ajustar porta e, se possível, o escopo de endereços das estações)
New-NetFirewallRule -DisplayName "Databricks Gateway" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -ErrorAction SilentlyContinue | Out-Null
Write-Host "Feito. Pendente manual: login automático/tela bloqueada conforme a política da empresa (ver docs/DEPLOY.md)."
