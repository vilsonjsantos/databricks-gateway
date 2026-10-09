/**
 * Exemplo de Code.gs. Só emite o token assinado; NÃO chama o gateway (o Apps Script roda nos servidores do
 * Google e não alcança a máquina da empresa — a chamada é feita pelo navegador do operador, no index.html).
 *
 * Configuração (Project Settings > Script Properties):
 *   GATEWAY_TOKEN_SECRET  mesmo valor de TOKEN_SECRET do gateway
 *   GATEWAY_URL           https://<host-do-gateway>   (HTTPS, sem barra no fim)
 *   APP_ID                igual ao app_id do apps/<app_id>.yaml
 */
function doGet() {
  return HtmlService.createHtmlOutputFromFile('index').setTitle('Assistente');
}

function getGatewayConfig() {
  var p = PropertiesService.getScriptProperties();
  var secret = p.getProperty('GATEWAY_TOKEN_SECRET');
  var appId = p.getProperty('APP_ID');
  var user = Session.getActiveUser().getEmail() || 'desconhecido';
  var payload = JSON.stringify({ app: appId, sub: user, exp: Math.floor(Date.now() / 1000) + 300 });
  var body = Utilities.base64EncodeWebSafe(payload).replace(/=+$/, '');
  var sig = Utilities.base64EncodeWebSafe(
    Utilities.computeHmacSha256Signature(body, secret)
  ).replace(/=+$/, '');
  return { url: p.getProperty('GATEWAY_URL'), token: body + '.' + sig };
}
