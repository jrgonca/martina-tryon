
(function () {
if (window.TryOn && window.TryOn._loaded) return;
var API_URL = 'https://martina-tryon.onrender.com';
var BRAND_LABEL = 'Provar virtualmente';
// Ícone de cabide minimalista (line-art) — currentColor pra herdar a cor do botão.
var BRAND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 12V9.5a2 2 0 1 1 3-1.732"/><path d="M12 12 3.5 18.2A.5.5 0 0 0 3.8 19h16.4a.5.5 0 0 0 .3-.8L12 12z"/></svg>';
var MOUNT_SELECTORS = [
null,
'.js-addtocart',
'.koba-add',
'.js-add-to-cart-button',
'button[name="add-cart"]',
'.js-product-form',
'.js-add-to-cart-form',
'.js-add-cart-form',
'[data-store="product-form"]',
'.product-form-section',
'.product-info .product-form',
'.product-buy',
'.js-product-buy',
'#form_buy',
'.product-actions',
'[data-product-form]',
'form[action*="/cart/add"]',
'form.cart',
'.product-add-form',
'[class*="add-to-cart"]',
'[class*="addToCart"]'
];
function findMountPoint() {
var override = OWN_SCRIPT && OWN_SCRIPT.getAttribute('data-mount');
if (override) MOUNT_SELECTORS[0] = override;
for (var i = 0; i < MOUNT_SELECTORS.length; i++) {
if (!MOUNT_SELECTORS[i]) continue;
var el = document.querySelector(MOUNT_SELECTORS[i]);
if (el) return el;
}
return null;
}
function isTestMode() {
var qs = new URLSearchParams(location.search);
if (qs.get('mtryon') === '1') {
document.cookie = 'mtryon=1; max-age=2592000; path=/; SameSite=Lax';
return true;
}
return document.cookie.indexOf('mtryon=1') >= 0;
}
var OWN_SCRIPT = document.currentScript || (function(){
var ss = document.querySelectorAll('script[data-tryon],script[src*="widget.js"]');
return ss[ss.length - 1] || null;
})();
function isProductPage() {
return /\/produtos\/[^/?#]+\/?/.test(location.pathname);
}
function scrapeProductFromDOM() {
var nameEl = document.querySelector('h1.product-name, [itemprop="name"], h1.product-title, h1');
var name = nameEl ? nameEl.textContent.trim() : document.title.split('|')[0].trim();
var imgEl = document.querySelector(
'.gallery-cell.is-selected img, ' +
'.js-product-image-zoom, ' +
'[data-image-gallery] img:first-child, ' +
'.product-gallery img:first-child, ' +
'img[itemprop="image"]'
);
var img = imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-zoom') || imgEl.getAttribute('data-src')) : null;
if (img && img.indexOf('//') === 0) img = location.protocol + img;
return { name: name, image: img };
}
function hash(s) {
var h = 0;
if (!s) return 'x';
for (var i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }
return 'h' + Math.abs(h).toString(36);
}
function cacheKey(personHash, garmentUrl) { return 'tryon_' + personHash + '_' + hash(garmentUrl); }
function getCached(personHash, garmentUrl) { try { return sessionStorage.getItem(cacheKey(personHash, garmentUrl)); } catch (e) { return null; } }
function setCached(personHash, garmentUrl, b64) { try { sessionStorage.setItem(cacheKey(personHash, garmentUrl), b64); } catch (e) {} }
function rateLimitOk() {
if (document.cookie.indexOf('mtryon=1') >= 0) return true;
try {
var key = 'tryon_rl_' + new Date().toISOString().slice(0, 10);
var n = parseInt(sessionStorage.getItem(key) || '0', 10);
if (n >= 10) return false;
sessionStorage.setItem(key, String(n + 1));
return true;
} catch (e) { return true; }
}
function track(event, params) {
try {
if (window.dataLayer) window.dataLayer.push(Object.assign({ event: 'tryon_' + event }, params || {}));
} catch (e) {}
}
function uuid(){ try { return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g,function(c){return (c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16);}); } catch(e){ return 'x'+Math.random().toString(36).slice(2)+Date.now().toString(36); } }
function getClientId(){ try { var v = localStorage.getItem('tryon_cid'); if (!v) { v = uuid(); localStorage.setItem('tryon_cid', v); } return v; } catch(e){ return ''; } }
function getSessionId(){ try { var v = sessionStorage.getItem('tryon_sid'); if (!v) { v = uuid(); sessionStorage.setItem('tryon_sid', v); } return v; } catch(e){ return ''; } }
function markProvado(name, url){
// localStorage com TTL 24h — atravessa subdominio (checkout/thank-you da Nuvemshop)
// e cobre quem prova hoje e compra hoje mesmo em sessao nova.
try {
var key='tryon_provados';
var TTL = 24 * 3600 * 1000;
var now = Date.now();
var arr = [];
try { arr = JSON.parse(localStorage.getItem(key)||'[]'); } catch(e) {}
// purga expirados
arr = arr.filter(function(it){ return (now - (it.ts||0)) < TTL; });
arr.push({name:(name||'').slice(0,200), url:(url||'').slice(0,500), ts:now});
if (arr.length > 50) arr = arr.slice(-50);
localStorage.setItem(key, JSON.stringify(arr));
} catch(e) {}
}
// _ANALYTICS_CTX é populado pelo init() — guarda referencia ao state do widget
var _ANALYTICS_CTX = null;
function emit(eventType, extra){
try {
var s = _ANALYTICS_CTX || {};
var name = (s.garmentInfo && s.garmentInfo.name)
  || ((document.querySelector('h1.product-name, [itemprop="name"], h1.product-title, h1')||{}).textContent || '').trim();
var cat = (s.garmentInfo && s.garmentInfo.category) || '';
var payload = {
tenant: 'martina',
event_type: eventType,
client_id: getClientId(),
session_id: getSessionId(),
product_url: location.href,
product_name: name.slice(0,200),
garment_category: cat
};
if (extra) Object.assign(payload, extra);
var body = JSON.stringify(payload);
var url = API_URL + '/event';
if (navigator.sendBeacon) {
// text/plain evita CORS preflight (que estava falhando silenciosamente).
// Backend aceita qualquer Content-Type via request.get_data(as_text=True).
navigator.sendBeacon(url, new Blob([body], {type:'text/plain'}));
} else {
fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:body, keepalive:true, mode:'cors'}).catch(function(){});
}
} catch (e) {}
}
function resizeImage(file, maxSide) {
maxSide = maxSide || 1024;
return new Promise(function (res, rej) {
var img = new Image();
img.onload = function () {
var s = Math.min(1, maxSide / Math.max(img.width, img.height));
var w = Math.round(img.width * s);
var h = Math.round(img.height * s);
var c = document.createElement('canvas');
c.width = w; c.height = h;
c.getContext('2d').drawImage(img, 0, 0, w, h);
res(c.toDataURL('image/jpeg', 0.92));
};
img.onerror = rej;
img.src = URL.createObjectURL(file);
});
}
function callTryOnOnce(personDataUri, garmentImageUrl, garmentDescription, externalCtl) {
var ctl = externalCtl || (('AbortController' in window) ? new AbortController() : null);
var timer = ctl ? setTimeout(function(){ ctl.abort(); }, 150000) : null;
return fetch(API_URL + '/tryon', {
method: 'POST',
headers: { 'Content-Type': 'application/json' },
signal: ctl ? ctl.signal : undefined,
body: JSON.stringify({
person_image: personDataUri,
garment_image_url: garmentImageUrl,
garment_description: garmentDescription,
quality: 'low', // ~$0.011/img (4x mais barato que medium). Junior validou que qualidade está OK.
}),
}).then(function (r) {
if (timer) clearTimeout(timer);
return r.json().then(function (d) {
if (!r.ok) {
var err = new Error(d.error || 'Erro ' + r.status);
err.status = r.status;
throw err;
}
return d;
});
}, function(e){
if (timer) clearTimeout(timer);
if (e && e.name === 'AbortError') {
var te = new Error('Timeout — a IA demorou demais. Tenta de novo.');
te.status = 0;
throw te;
}
throw e;
});
}
function callTryOn(personDataUri, productUrl, garmentImageUrl, garmentDescription, externalCtl) {
return callTryOnOnce(personDataUri, garmentImageUrl, garmentDescription, externalCtl).catch(function (e) {
if (externalCtl && externalCtl.signal && externalCtl.signal.aborted) throw e;
var transient = !e.status || (e.status >= 500 && e.status < 600);
if (!transient) throw e;
return new Promise(function (res) { setTimeout(res, 2500); })
.then(function () { return callTryOnOnce(personDataUri, garmentImageUrl, garmentDescription, externalCtl); });
});
}
function resolveProductIfNeeded(currentImageUrl) {
if (currentImageUrl && /acdn-us\.mitiendanube\.com|cdn\.shopify\.com|cloudinary\.com/.test(currentImageUrl)) {
return Promise.resolve({ image_url: currentImageUrl, image_url_hd: currentImageUrl, suggested_category: null });
}
return fetch(API_URL + '/resolve-product', {
method: 'POST',
headers: { 'Content-Type': 'application/json' },
body: JSON.stringify({ page_url: location.href }),
}).then(function (r) { return r.json(); });
}
var STYLES = (
':host{all:initial;font-family:-apple-system,system-ui,sans-serif;display:block;}' +
':host([data-mode="inline"]){width:100%;margin:16px 0;}' +
'*{box-sizing:border-box}' +
'.btn{width:100%;background:#fff;color:#111;border:1.5px solid #111;border-radius:4px;padding:14px 22px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;cursor:pointer;font-weight:700;display:flex;align-items:center;justify-content:center;gap:10px;transition:all .2s}' +
'.btn:hover{background:#111;color:#fff}' +
':host([data-mode="floating"]) .btn{position:fixed;bottom:90px;right:20px;width:auto;z-index:99998;background:#111;color:#fff;border:0;border-radius:999px;padding:13px 20px;font-size:13px;letter-spacing:.08em;box-shadow:0 10px 30px rgba(0,0,0,.25);font-weight:600;justify-content:flex-start}' +
':host([data-mode="floating"]) .btn:hover{background:#111;color:#fff;transform:translateY(-2px)}' +
'@media(max-width:760px){:host([data-mode="floating"]) .btn{bottom:80px;right:14px;padding:11px 16px;font-size:12px}}' +
'.btn .icon{width:18px;height:18px;display:inline-flex;align-items:center;flex-shrink:0}' +
'.btn .icon svg{width:100%;height:100%;display:block}' +
'.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99999;align-items:center;justify-content:center;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}' +
'.overlay.show{display:flex}' +
'.modal{background:#fff;width:100%;height:100%;display:flex;flex-direction:column;overflow:hidden}' +
'@media(min-width:761px){.overlay{padding:24px}.modal{max-width:920px;height:auto;max-height:92vh;border-radius:18px}}' +
'.head{padding:16px 18px;border-bottom:1px solid #eee;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}' +
'.head h2{margin:0;font-size:13px;letter-spacing:.2em;font-weight:700;text-transform:uppercase}' +
'.head button{background:none;border:0;font-size:28px;cursor:pointer;color:#666;line-height:1;padding:0 4px;-webkit-tap-highlight-color:transparent}' +
'.body{padding:14px;overflow:auto;flex:1;-webkit-overflow-scrolling:touch}' +
'@media(min-width:761px){.body{padding:22px}}' +
'.grid{display:grid;grid-template-columns:1fr;gap:12px;max-width:520px;margin:0 auto}' +
'@media(min-width:761px){.grid{gap:18px}}' +
'#cardResult{display:none}' +
'.modal[data-step="result"] #cardPerson{display:none}' +
'.modal[data-step="result"] #cardResult{display:block}' +
'.modal[data-step="result"] #goRow{display:none}' +
'.card{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:14px}' +
'.card h3{margin:0 0 12px;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#666;font-weight:700}' +
'.preview{aspect-ratio:3/4;max-height:45vh;background:#f0f0f0;border-radius:10px;display:flex;align-items:center;justify-content:center;overflow:hidden}' +
'@media(min-width:761px){.preview{aspect-ratio:3/4;max-height:none}}' +
'.preview img{width:100%;height:100%;object-fit:cover}' +
'.preview .empty{color:#999;font-size:13px;text-align:center;padding:24px;line-height:1.5}' +
'.controls{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}' +
'.btn-line{flex:1;min-width:130px;min-height:46px;padding:12px 14px;background:#111;color:#fff;border:0;border-radius:8px;cursor:pointer;font-size:13px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;display:flex;align-items:center;justify-content:center;gap:6px;-webkit-tap-highlight-color:transparent}' +
'.btn-line.ghost{background:#fff;color:#111;border:1.5px solid #111}' +
'.btn-line input{display:none}' +
'.cta-row{padding:14px 18px;border-top:1px solid #eee;display:flex;gap:10px;align-items:center;justify-content:space-between;background:#fff;flex-shrink:0}' +
'.cta-row .status{font-size:12px;color:#666;font-family:ui-monospace,monospace;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
'.cta-row .status.err{color:#dc2626}' +
'.cta-row .status.ok{color:#059669}' +
'.go{background:#111;color:#fff;border:0;padding:16px 28px;border-radius:10px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;cursor:pointer;font-weight:700;min-height:52px;min-width:140px;-webkit-tap-highlight-color:transparent}' +
'.go:disabled{opacity:.35;cursor:not-allowed}' +
'.bar{height:3px;background:#eee;overflow:hidden;border-radius:99px;margin-top:10px}' +
'.bar>div{height:100%;background:#111;width:0;transition:width .3s}' +
'.status-inline{font-size:13px;color:#666;margin-top:14px;text-align:center;font-family:ui-monospace,monospace;min-height:18px}' +
'.status-inline.err{color:#dc2626}' +
'.status-inline.ok{color:#059669}' +
'.meta{font-size:10px;color:#999;margin-top:14px;text-align:center;letter-spacing:.06em;line-height:1.5}' +
'#camera{display:none;width:100%;border-radius:10px;background:#000}' +
'.snap{margin-top:12px;display:none;text-align:center}' +
'.snap.show{display:block}' +
'.snap button{background:#fff;border:2px solid #111;color:#111;padding:14px 28px;border-radius:99px;cursor:pointer;font-weight:700;font-size:13px;letter-spacing:.1em;text-transform:uppercase;min-height:48px;-webkit-tap-highlight-color:transparent}' +
'.result-actions{display:none;gap:10px;margin-top:14px;flex-direction:column}' +
'@media(min-width:761px){.result-actions{flex-direction:row}}' +
'.result-actions.show{display:flex}' +
'.btn-buy{flex:1;background:#111;color:#fff;border:0;padding:16px 20px;border-radius:10px;font-size:14px;letter-spacing:.2em;text-transform:uppercase;cursor:pointer;font-weight:700;min-height:54px;-webkit-tap-highlight-color:transparent;transition:transform .15s}' +
'.btn-buy:active{transform:scale(.97)}' +
'.btn-retry{flex:1;background:#fff;color:#111;border:1.5px solid #111;padding:16px 20px;border-radius:10px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;font-weight:600;min-height:54px;-webkit-tap-highlight-color:transparent;display:flex;align-items:center;justify-content:center;gap:6px}' +
// ---- Tela inicial de escolha (Provar OU Medir tamanho)
'#chooseView, #quizView, #quizResultView{display:none}' +
'.modal[data-step="choose"] #cardPerson,.modal[data-step="choose"] #cardResult,.modal[data-step="choose"] #goRow{display:none}' +
'.modal[data-step="choose"] #chooseView{display:block}' +
'.modal[data-step="quiz"] #cardPerson,.modal[data-step="quiz"] #cardResult,.modal[data-step="quiz"] #goRow{display:none}' +
'.modal[data-step="quiz"] #quizView{display:block}' +
'.modal[data-step="quiz-result"] #cardPerson,.modal[data-step="quiz-result"] #cardResult,.modal[data-step="quiz-result"] #goRow{display:none}' +
'.modal[data-step="quiz-result"] #quizResultView{display:block}' +
'.choose-wrap{max-width:640px;margin:0 auto;padding:6px 4px 12px}' +
'.choose-sub{text-align:center;color:#666;font-size:13px;margin:0 0 22px;line-height:1.45}' +
'.choose-grid{display:grid;grid-template-columns:1fr;gap:12px}' +
'@media(min-width:640px){.choose-grid{grid-template-columns:1fr 1fr;gap:16px}}' +
'.choose-card{background:#fff;border:1.5px solid #eee;border-radius:14px;padding:24px 20px;cursor:pointer;text-align:center;transition:all .18s;-webkit-tap-highlight-color:transparent;min-height:220px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px}' +
'.choose-card:hover,.choose-card:focus{border-color:#111;background:#fafafa;transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.08)}' +
'.choose-card .cc-ico{width:52px;height:52px;display:inline-flex;align-items:center;justify-content:center;background:#111;color:#fff;border-radius:99px;font-size:24px;margin-bottom:6px}' +
'.choose-card h4{margin:0;font-size:14px;letter-spacing:.16em;text-transform:uppercase;font-weight:700;color:#111}' +
'.choose-card p{margin:2px 0 0;font-size:12px;color:#666;line-height:1.4;max-width:220px}' +
// ---- Quiz de tamanho
'.quiz-wrap{max-width:460px;margin:0 auto;padding:6px 4px 4px}' +
'.quiz-sub{text-align:center;color:#666;font-size:13px;margin:0 0 18px;line-height:1.4}' +
'.quiz-q{margin:14px 0}' +
'.quiz-q label{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#111;font-weight:700;margin:0 0 8px}' +
'.quiz-sizes{display:flex;flex-wrap:wrap;gap:6px}' +
'.quiz-sizes button{flex:1;min-width:48px;background:#fff;color:#111;border:1px solid #ddd;padding:11px 8px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:600;font-family:inherit;transition:all .15s;-webkit-tap-highlight-color:transparent}' +
'.quiz-sizes button:hover{border-color:#111}' +
'.quiz-sizes button.on{background:#111;color:#fff;border-color:#111}' +
'.quiz-fits{display:flex;flex-direction:column;gap:8px}' +
'.quiz-fits button{text-align:left;background:#fff;color:#111;border:1px solid #ddd;padding:12px 14px;border-radius:8px;cursor:pointer;font-family:inherit;transition:all .15s;-webkit-tap-highlight-color:transparent}' +
'.quiz-fits button:hover{border-color:#111}' +
'.quiz-fits button.on{background:#f7f7f7;border-color:#111}' +
'.quiz-fits .fl{font-size:13px;font-weight:700;letter-spacing:.06em;display:block}' +
'.quiz-fits .fd{font-size:11px;color:#666;margin-top:3px}' +
'.quiz-go{width:100%;margin-top:18px;background:#111;color:#fff;border:0;padding:15px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;font-weight:700;border-radius:10px;cursor:pointer;font-family:inherit;-webkit-tap-highlight-color:transparent;min-height:52px}' +
'.quiz-go:disabled{opacity:.35;cursor:not-allowed}' +
// ---- Resultado do quiz
'.quiz-result-wrap{max-width:460px;margin:0 auto;padding:6px 4px 4px}' +
'.quiz-big{text-align:center;padding:22px 0 14px}' +
'.quiz-big .rt{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:#666;margin:0 0 8px}' +
'.quiz-big .rs{font-size:64px;font-weight:800;color:#111;letter-spacing:.05em;line-height:1}' +
'.quiz-big .rc{font-size:11px;color:#666;margin-top:10px;letter-spacing:.05em;text-transform:uppercase}' +
'.quiz-reason{background:#f8f8f8;border-radius:8px;padding:12px 14px;font-size:13px;color:#333;line-height:1.5;margin:0 0 14px}' +
'.quiz-3fits{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin:0 0 18px}' +
'.quiz-3fits div{background:#fafafa;border:1px solid #eee;border-radius:6px;padding:10px 6px;text-align:center}' +
'.quiz-3fits div.hi{border-color:#111;background:#fff}' +
'.quiz-3fits .fl2{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#888;margin:0 0 4px}' +
'.quiz-3fits .fs2{font-size:20px;font-weight:800;color:#111}' +
'.quiz-3fits div.hi .fl2{color:#111;font-weight:700}' +
'.quiz-buy{width:100%;background:#111;color:#fff;border:0;padding:16px;font-size:14px;letter-spacing:.2em;text-transform:uppercase;font-weight:700;border-radius:10px;cursor:pointer;font-family:inherit;-webkit-tap-highlight-color:transparent;min-height:54px;transition:transform .15s}' +
'.quiz-buy:active{transform:scale(.97)}' +
'.quiz-buy.err{background:#dc2626}' +
'.quiz-restart{width:100%;background:none;color:#666;border:0;padding:10px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;font-family:inherit;margin-top:6px;-webkit-tap-highlight-color:transparent}' +
// Loading interno do quiz
'.quiz-loading{text-align:center;padding:40px 20px;color:#666;font-size:13px}'
);
var TPL = (
'<style>' + STYLES + '</style>' +
'<button class="btn" id="trigger" aria-label="' + BRAND_LABEL + '">' +
'<span class="icon">' + BRAND_ICON + '</span><span>' + BRAND_LABEL + '</span>' +
'</button>' +
'<div class="overlay" id="overlay">' +
'<div class="modal" role="dialog" aria-label="Provador virtual">' +
'<div class="head"><h2>Provador Virtual</h2><button id="close" aria-label="Fechar">×</button></div>' +
'<div class="body">' +
// Tela inicial de escolha
'<div id="chooseView" class="choose-wrap">' +
'<p class="choose-sub">Escolhe como quer usar o provador:</p>' +
'<div class="choose-grid">' +
'<button class="choose-card" id="chooseTryOn" type="button">' +
'<div class="cc-ico">📸</div>' +
'<h4>Provar virtualmente</h4>' +
'<p>Envie sua foto e veja como a peça fica em você usando IA</p>' +
'</button>' +
'<button class="choose-card" id="chooseSize" type="button">' +
'<div class="cc-ico">📏</div>' +
'<h4>Descobrir meu tamanho</h4>' +
'<p>3 perguntas rápidas pra descobrir o tamanho ideal pra você</p>' +
'</button>' +
'</div>' +
'</div>' +
// Tela do quiz (3 perguntas)
'<div id="quizView" class="quiz-wrap">' +
'<p class="quiz-sub">3 perguntinhas pra achar seu tamanho.</p>' +
'<div class="quiz-q">' +
'<label id="quizSizeLabel">Qual seu tamanho usual em blusas?</label>' +
'<div class="quiz-sizes" id="quizSizes"></div>' +
'</div>' +
'<div class="quiz-q">' +
'<label>Como voce prefere que caia?</label>' +
'<div class="quiz-fits" id="quizFits"></div>' +
'</div>' +
'<button class="quiz-go" id="quizGo" type="button" disabled>Ver minha recomendação</button>' +
'</div>' +
// Tela resultado do quiz
'<div id="quizResultView" class="quiz-result-wrap">' +
'<div class="quiz-big">' +
'<div class="rt">Recomendamos</div>' +
'<div class="rs" id="qrSize">M</div>' +
'<div class="rc" id="qrConf">Confiança: alta</div>' +
'</div>' +
'<div class="quiz-reason" id="qrReason"></div>' +
'<div class="quiz-3fits" id="qr3fits"></div>' +
'<button class="quiz-buy" id="qrBuy" type="button">Comprar agora</button>' +
'<button class="quiz-restart" id="qrRestart" type="button">Refazer</button>' +
'</div>' +
'<div class="grid">' +
'<div class="card" id="cardPerson">' +
'<h3>Sua foto</h3>' +
'<div class="preview" id="pvPerson"><div class="empty">Tire ou envie uma foto de corpo inteiro, de frente</div></div>' +
'<video id="camera" autoplay playsinline muted></video>' +
'<div class="snap" id="snapWrap"><button id="snap">📸 Capturar</button></div>' +
'<div class="controls">' +
'<button class="btn-line ghost" id="useCamera">📷 Câmera</button>' +
'<label class="btn-line">Enviar foto<input type="file" id="filePerson" accept="image/*"></label>' +
'</div>' +
'</div>' +
'<div class="card" id="cardResult">' +
'<h3>Como ficaria em você</h3>' +
'<div class="preview" id="pvResult"><div class="empty">Gerando…</div></div>' +
'<div class="status-inline" id="statusResult">Analisando sua foto…</div>' +
'<div class="bar"><div id="bar"></div></div>' +
'<div class="result-actions" id="resultActions">' +
'<button class="btn-buy" id="buyBtn">IR PRA COMPRA</button>' +
'<button class="btn-retry" id="retryBtn">↻ Testar novamente</button>' +
'</div>' +
'</div>' +
'</div>' +
'<div class="meta">Resultado gerado por IA. Cores e detalhes finos podem variar do produto real.</div>' +
'</div>' +
'<div class="cta-row" id="goRow">' +
'<div class="status" id="status">Pronto.</div>' +
'<button class="go" id="go" disabled>PROVAR</button>' +
'</div>' +
'</div>' +
'</div>'
);
function init() {
// SOFT-LAUNCH AGRESSIVO: gate ?mtryon=1 removido — widget aparece pra todas as visitantes.
// IP rate limit (10/min, 60/h em /tryon) + origin allowlist protegem custo OpenAI.
// Pra reverter rapidamente: descomentar a linha abaixo.
// if (!isTestMode()) return;
if (!isProductPage()) return;
var host = document.createElement('div');
host.id = 'tryon-host';
var mount = findMountPoint();
var positionPref = (OWN_SCRIPT && OWN_SCRIPT.getAttribute('data-position')) || 'afterend';
if (mount) {
host.setAttribute('data-mode', 'inline');
try {
mount.insertAdjacentElement(positionPref, host);
} catch (e) {
document.body.appendChild(host);
host.setAttribute('data-mode', 'floating');
}
} else {
host.setAttribute('data-mode', 'floating');
document.body.appendChild(host);
}
var root = host.attachShadow({ mode: 'open' });
root.innerHTML = TPL;
var $ = function (s) { return root.querySelector(s); };
var state = {
personDataUri: null,
personHash: null,
garmentUrl: null,
garmentInfo: null,
progressInterval: null,
_msgTimer: null,
_abortCtl: null,
};
// Conecta state pro emit() (analytics)
_ANALYTICS_CTX = state;
// View do botão: dispara depois de garmentInfo carregar pra ter nome do produto.
// Disparo uma só vez por carregamento de pagina, mesmo se widget remount.
if (!window.__tryon_viewed) {
  window.__tryon_viewed = true;
  setTimeout(function(){ emit('tryon_view'); }, 1500);
}
function abortGeneration() {
clearInterval(state.progressInterval); state.progressInterval = null;
clearInterval(state._msgTimer); state._msgTimer = null;
if (state._abortCtl) { try { state._abortCtl.abort(); } catch(e){} state._abortCtl = null; }
}
function resetResultUI() {
$('#resultActions').classList.remove('show');
$('#pvResult').innerHTML = '<div class="empty">Gerando…</div>';
setBar(0);
}
function setPreview(el, src) { el.innerHTML = '<img src="' + src + '" alt="">'; }
function setStatus(msg, cls) {
var s = $('#status'); s.textContent = msg; s.className = 'status' + (cls ? ' ' + cls : '');
var s2 = $('#statusResult'); if (s2) { s2.textContent = msg; s2.className = 'status-inline' + (cls ? ' ' + cls : ''); }
}
var modalEl = root.querySelector('.modal');
function goToStep(step) {
if (step) modalEl.setAttribute('data-step', step);
else modalEl.removeAttribute('data-step');
}
function setBar(p) { $('#bar').style.width = Math.max(0, Math.min(100, p)) + '%'; }
function progress(start) {
var p = 5; setBar(p);
clearInterval(state.progressInterval);
if (start) state.progressInterval = setInterval(function () { p = Math.min(94, p + 1.8); setBar(p); }, 700);
}
function updateGoButton() { $('#go').disabled = !(state.personDataUri && state.garmentUrl); }
$('#trigger').addEventListener('click', function () {
track('open', { product: state.garmentInfo && state.garmentInfo.name });
emit('tryon_open');
$('#overlay').classList.add('show');
goToStep('choose'); // abre com tela de escolha
resetResultUI();
setStatus('Pronto.', 'ok');
try { fetch(API_URL + '/', { method: 'GET', cache: 'no-store' }).catch(function(){}); } catch(e){}
if (!state.garmentUrl) loadGarment();
});

// ---- Escolha inicial ----
$('#chooseTryOn').addEventListener('click', function(){
track('choose_tryon');
goToStep(null); // volta pra fluxo atual (cardPerson visível)
});
$('#chooseSize').addEventListener('click', function(){
track('choose_size_quiz');
goToStep('quiz');
renderQuizStep();
});

// ---- Quiz de tamanho (dentro do provador) ----
var TOPS = ["PP","P","M","G","GG","XG"];
var BOTTOMS = ["36","38","40","42","44","46","48"];
var FITS = [
{ id: "colado", label: "Coladinho", desc: "gosto de peca marcando o corpo" },
{ id: "ideal", label: "No jeito", desc: "caimento padrao da marca" },
{ id: "soltinho", label: "Soltinho", desc: "peca mais folgada e confortavel" }
];
var quizState = { size: null, fit: null, category: null, isBottom: false };

function detectCategoryLocal(){
var t = (document.title + " " + ((document.querySelector("h1")||{}).textContent || "")).toLowerCase();
if (/vestid|dress/.test(t)) return "dresses";
if (/cal[cç]a|short|saia|jeans/.test(t)) return "lower_body";
return "upper_body";
}
function getUserHashLocal(){
try {
var v = localStorage.getItem("mts_uh");
if (!v) { v = "u" + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("mts_uh", v); }
return v;
} catch(e){ return "anon"; }
}
function getProfileLocal(){ try { return JSON.parse(localStorage.getItem("mts_profile") || "{}"); } catch(e){ return {}; } }
function saveProfileLocal(p){
try { localStorage.setItem("mts_profile", JSON.stringify(p)); } catch(e){}
try {
fetch(API_URL + "/profile?tenant=martina&user_hash=" + encodeURIComponent(getUserHashLocal()), {
method: "POST",
headers: { "Content-Type": "application/json" },
body: JSON.stringify({
size_top: p.size_top || null,
size_bottom: p.size_bottom || null,
size_dress: p.size_dress || p.size_top || null,
fit_pref: p.fit_pref || null
}),
keepalive: true
}).catch(function(){});
} catch(e){}
}

function renderQuizStep(){
quizState.category = detectCategoryLocal();
quizState.isBottom = quizState.category === "lower_body";
var sizes = quizState.isBottom ? BOTTOMS : TOPS;
var label = quizState.isBottom ? "Qual seu tamanho usual em calças?"
: (quizState.category === "dresses" ? "Qual seu tamanho usual em vestidos?" : "Qual seu tamanho usual em blusas?");
$('#quizSizeLabel').textContent = label;
var p = getProfileLocal();
quizState.size = (quizState.isBottom ? p.size_bottom : p.size_top) || null;
quizState.fit = p.fit_pref || null;
// render size buttons
var sizesEl = $('#quizSizes');
sizesEl.innerHTML = sizes.map(function(s){
return '<button type="button" data-v="' + s + '"' + (s===quizState.size ? ' class="on"' : '') + '>' + s + '</button>';
}).join('');
sizesEl.onclick = function(e){
var b = e.target.closest('button[data-v]'); if (!b) return;
sizesEl.querySelectorAll('button').forEach(function(x){ x.classList.remove('on'); });
b.classList.add('on');
quizState.size = b.getAttribute('data-v');
updateQuizGo();
};
// render fit buttons
var fitsEl = $('#quizFits');
fitsEl.innerHTML = FITS.map(function(f){
return '<button type="button" data-v="' + f.id + '"' + (f.id===quizState.fit ? ' class="on"' : '') + '><span class="fl">' + f.label + '</span><div class="fd">' + f.desc + '</div></button>';
}).join('');
fitsEl.onclick = function(e){
var b = e.target.closest('button[data-v]'); if (!b) return;
fitsEl.querySelectorAll('button').forEach(function(x){ x.classList.remove('on'); });
b.classList.add('on');
quizState.fit = b.getAttribute('data-v');
updateQuizGo();
};
updateQuizGo();
}
function updateQuizGo(){
$('#quizGo').disabled = !(quizState.size && quizState.fit);
}

$('#quizGo').addEventListener('click', function(){
if (!quizState.size || !quizState.fit) return;
var btn = $('#quizGo');
btn.disabled = true; btn.textContent = 'Calculando…';
// salva perfil
saveProfileLocal({
size_top: quizState.isBottom ? null : quizState.size,
size_bottom: quizState.isBottom ? quizState.size : null,
size_dress: quizState.isBottom ? null : quizState.size,
fit_pref: quizState.fit
});
track('quiz_submit', { size: quizState.size, fit: quizState.fit, category: quizState.category });

fetch(API_URL + '/size-recommendation', {
method: 'POST',
headers: { 'Content-Type': 'application/json' },
body: JSON.stringify({
tenant: 'martina',
product_url: location.href,
product_name: (state.garmentInfo && state.garmentInfo.name) || '',
category: quizState.category,
size_declared: quizState.size,
user_hash: getUserHashLocal()
})
})
.then(function(r){ return r.json(); })
.then(function(data){
btn.disabled = false; btn.textContent = 'Ver minha recomendação';
renderQuizResult(data);
})
.catch(function(){
btn.disabled = false; btn.textContent = 'Ver minha recomendação';
setStatus('Erro ao calcular. Tenta de novo.', 'err');
});
});

function renderQuizResult(data){
var pick = data.size_ideal;
if (quizState.fit === 'colado') pick = data.size_colado;
else if (quizState.fit === 'soltinho') pick = data.size_soltinho;
quizState.picked = pick;

$('#qrSize').textContent = pick;
$('#qrConf').textContent = 'Confiança: ' + (data.confidence === 'high' ? 'alta' : data.confidence === 'medium' ? 'média' : 'baixa');
$('#qrReason').textContent = data.reason || '';
$('#qr3fits').innerHTML =
'<div' + (quizState.fit==='colado'?' class="hi"':'') + '><div class="fl2">Coladinho</div><div class="fs2">' + data.size_colado + '</div></div>' +
'<div' + (quizState.fit==='ideal'?' class="hi"':'') + '><div class="fl2">No jeito</div><div class="fs2">' + data.size_ideal + '</div></div>' +
'<div' + (quizState.fit==='soltinho'?' class="hi"':'') + '><div class="fl2">Soltinho</div><div class="fs2">' + data.size_soltinho + '</div></div>';
$('#qrBuy').textContent = 'Comprar tamanho ' + pick;
$('#qrBuy').classList.remove('err');
goToStep('quiz-result');
track('quiz_result', { size_picked: pick, confidence: data.confidence });
emit('tryon_size_recommendation', { size_declared: quizState.size, fit_pref: quizState.fit, size_picked: pick });
}

function selectVariantSize(size){
var s = document.querySelector("#variation_1, select[name='variation[0]'], select[name*='tamanho' i], select[name*='size' i]");
if (!s) return false;
var opt = Array.prototype.find.call(s.options, function(o){ return o.value === size || o.text.trim().toUpperCase() === size; });
if (!opt) return false;
s.value = opt.value;
s.dispatchEvent(new Event('change', { bubbles: true }));
return true;
}

$('#qrBuy').addEventListener('click', function(){
var pick = quizState.picked;
var ok = selectVariantSize(pick);
var btn = $('#qrBuy');
if (!ok) {
btn.textContent = 'Não achei o tamanho ' + pick + ' — escolhe manualmente';
btn.classList.add('err');
track('quiz_buy_no_variant', { size: pick });
return;
}
track('quiz_buy_click', { size: pick });
emit('tryon_buy_click_from_quiz', { size_picked: pick });
$('#overlay').classList.remove('show');
stopCamera();
setTimeout(scrollToBuy, 320);
});
$('#qrRestart').addEventListener('click', function(){
goToStep('quiz');
renderQuizStep();
});
function closeModal() {
$('#overlay').classList.remove('show');
stopCamera();
abortGeneration();
$('#go').disabled = !(state.personDataUri && state.garmentUrl);
}
$('#close').addEventListener('click', closeModal);
$('#overlay').addEventListener('click', function (e) {
if (e.target === $('#overlay')) closeModal();
});
document.addEventListener('keydown', function (e) {
if (e.key === 'Escape' && $('#overlay').classList.contains('show')) closeModal();
});
$('#filePerson').addEventListener('change', async function (e) {
var f = e.target.files[0]; if (!f) return;
try {
var d = await resizeImage(f, 1024);
state.personDataUri = d;
state.personHash = hash(d.slice(-200));
setPreview($('#pvPerson'), d);
updateGoButton();
track('upload_photo');
} catch (err) { setStatus('Erro ao ler foto', 'err'); }
});
var stream = null;
async function startCamera() {
try {
stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
var v = $('#camera'); v.srcObject = stream; v.style.display = 'block';
$('#pvPerson').style.display = 'none';
$('#snapWrap').classList.add('show');
track('camera_open');
} catch (e) { setStatus('Câmera não disponível: ' + e.message, 'err'); }
}
function stopCamera() {
if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
stream = null;
$('#camera').style.display = 'none';
$('#camera').srcObject = null;
$('#pvPerson').style.display = '';
$('#snapWrap').classList.remove('show');
}
$('#useCamera').addEventListener('click', startCamera);
$('#snap').addEventListener('click', function () {
var v = $('#camera');
var c = document.createElement('canvas');
c.width = v.videoWidth; c.height = v.videoHeight;
c.getContext('2d').drawImage(v, 0, 0);
var d = c.toDataURL('image/jpeg', 0.92);
state.personDataUri = d;
state.personHash = hash(d.slice(-200));
setPreview($('#pvPerson'), d);
stopCamera();
updateGoButton();
track('camera_snap');
});
function loadGarment() {
var dom = scrapeProductFromDOM();
state.garmentInfo = dom;
setStatus('Carregando…');
resolveProductIfNeeded(dom.image).then(function (r) {
var img = r.image_url_hd || r.image_url || dom.image;
state.garmentUrl = img;
setStatus('Pronto.', 'ok');
updateGoButton();
}).catch(function (e) {
if (dom.image) {
state.garmentUrl = dom.image;
setStatus('Pronto.', 'ok');
updateGoButton();
} else {
setStatus('Não consegui carregar a peça', 'err');
}
});
}
function scrollToBuy() {
// Estrategia: scrolla + highlight pro user ver onde esta o COMPRAR nativo da loja.
// NAO clica auto pra nao gerar pedido sem tamanho/cor escolhidos (UX ruim, devolucao).
// Auto-click eh tentado SO se houver UM unico botao Comprar e UM unico tamanho disponivel
// (caso raro, mas seguro de automatizar).
var target = findMountPoint();
if (!target) return;
try {
target.scrollIntoView({ behavior: 'smooth', block: 'center' });
var orig = target.style.cssText;
target.style.transition = 'box-shadow .4s';
target.style.boxShadow = '0 0 0 4px rgba(255,200,0,.6)';
setTimeout(function(){ target.style.cssText = orig; }, 1600);
} catch(e) {}
// Tenta auto-click APENAS se nao tem variantes a escolher (1 tamanho, 1 cor)
try {
var sizeSelects = document.querySelectorAll('select[name*="size" i], select[name*="tamanho" i], .js-product-variants select');
var hasUnselectedSize = false;
sizeSelects.forEach(function(s){ if (s && s.options.length > 1 && !s.value) hasUnselectedSize = true; });
if (hasUnselectedSize) return; // user precisa escolher manualmente
// se chegou aqui: nao tem tamanho a escolher OU ja esta selecionado
var nativeBuy = document.querySelector('button[name=add-cart], .js-add-to-cart-button, .koba-add, .js-addtocart');
if (nativeBuy && !nativeBuy.disabled) {
  setTimeout(function(){ try { nativeBuy.click(); } catch(e){} }, 800);
}
} catch(e) {}
}
$('#buyBtn').addEventListener('click', function () {
track('buy_click', { product: state.garmentInfo && state.garmentInfo.name });
emit('tryon_buy_click');
$('#overlay').classList.remove('show');
stopCamera();
setTimeout(scrollToBuy, 320);
});
$('#retryBtn').addEventListener('click', function () {
track('retry');
goToStep(null);
resetResultUI();
setStatus('Pronto pra testar de novo.', 'ok');
$('#go').disabled = !(state.personDataUri && state.garmentUrl);
});
$('#go').addEventListener('click', async function () {
if (!state.personDataUri || !state.garmentUrl) return;
if (!rateLimitOk()) { setStatus('Limite diário atingido', 'err'); return; }
var cached = getCached(state.personHash, state.garmentUrl);
if (cached) {
setBar(100);
setPreview($('#pvResult'), 'data:image/jpeg;base64,' + cached);
setStatus('Pronto (do cache).', 'ok');
track('result', { cached: true });
return;
}
$('#go').disabled = true;
goToStep('result');
setStatus('Gerando — pode levar 1 a 2 minutos…'); progress(true);
var msgs = ['Analisando sua foto…','Identificando a peça…','Ajustando proporções…','Renderizando o resultado…','Quase lá, é a IA capricha…'];
var msgIx = 0;
state._msgTimer = setInterval(function(){
msgIx = (msgIx + 1) % msgs.length;
setStatus(msgs[msgIx]);
}, 12000);
state._abortCtl = ('AbortController' in window) ? new AbortController() : null;
track('generate', { product: state.garmentInfo && state.garmentInfo.name });
try {
var d = await callTryOn(
state.personDataUri,
location.href,
state.garmentUrl,
state.garmentInfo && state.garmentInfo.name,
state._abortCtl
);
clearInterval(state.progressInterval); clearInterval(state._msgTimer);
state.progressInterval = null; state._msgTimer = null; state._abortCtl = null;
if (!$('#overlay').classList.contains('show')) return;
setBar(100);
setPreview($('#pvResult'), 'data:image/jpeg;base64,' + d.image_b64);
setStatus('Pronto.', 'ok');
$('#resultActions').classList.add('show');
setCached(state.personHash, state.garmentUrl, d.image_b64);
track('result', { cached: false });
emit('tryon_complete');
markProvado((state.garmentInfo && state.garmentInfo.name) || (document.querySelector('h1')||{}).textContent || '', location.href);
} catch (e) {
clearInterval(state.progressInterval); clearInterval(state._msgTimer);
state.progressInterval = null; state._msgTimer = null;
if (e && (e.name === 'AbortError' || (state._abortCtl && state._abortCtl.signal && state._abortCtl.signal.aborted))) {
state._abortCtl = null;
return;
}
state._abortCtl = null;
setBar(0);
goToStep(null);
setStatus('Erro: ' + e.message + ' — tenta de novo', 'err');
track('error', { message: e.message });
} finally {
$('#go').disabled = false;
}
});
}
window.TryOn = { _loaded: true, init: init };
if (document.readyState === 'loading') {
document.addEventListener('DOMContentLoaded', init);
} else {
init();
}
})();
