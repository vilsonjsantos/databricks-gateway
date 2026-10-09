# Abre o painel executivo em tela cheia (modo quiosque) no Edge. NÃO TESTADO na máquina da empresa.
# Use a PANEL_KEY (só-leitura, dados agregados), NUNCA a ADMIN_KEY: a chave vai na URL e fica no histórico do navegador.
# Uso manual:   powershell -ExecutionPolicy Bypass -File deploy\open-painel.ps1 -Key "<PANEL_KEY>"
# Início automático (a cada login do usuário que fica logado na máquina):
#   schtasks /Create /SC ONLOGON /TN PainelAssistente /TR "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File <raiz>\deploy\open-painel.ps1 -Key <PANEL_KEY>"
# Se PANEL_LOCALHOST_NO_KEY=true (e SEM proxy reverso), não precisa de -Key.
# Dica: o monitor/TV não deve entrar em repouso (harden-windows.ps1 já desliga o desligamento de tela do Windows).
param([string]$Url = "http://localhost:8080", [string]$Key = "", [string]$Tema = "escuro")
$q = "?tema=$Tema"
if ($Key) { $q += "&k=$Key" }
$edge = (Get-Command msedge -ErrorAction SilentlyContinue).Source
if (-not $edge) { $edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe" }
# espera o gateway responder antes de abrir (o serviço pode estar subindo junto com o Windows)
for ($i = 0; $i -lt 60; $i++) { try { Invoke-RestMethod "$Url/health" -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 3 } }
Start-Process $edge -ArgumentList "--kiosk", "$Url/painel$q", "--edge-kiosk-type=fullscreen", "--no-first-run"
