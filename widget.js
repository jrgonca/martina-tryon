/*!
 * TryOn Widget — Virtual try-on para PDPs de e-commerce
 * Plataforma agnostica (Nuvemshop, Shopify, Woo etc). Hoje configurado pra Martina.
 *
 * Como ativar pro publico final: remover o gate de cookie no init().
 * Como ativar pra teste: cookie mtryon=1 ou query ?mtryon=1
 *
 * Tudo isolado em Shadow DOM. Nao polui o tema da loja.
 */
(function () {
  if (window.TryOn && window.TryOn._loaded) return;

  var API_URL = 'https://martina-tryon.onrender.com';
  var BRAND_LABEL = 'Provar virtualmente';
  var BRAND_EMOJI = '👗';

  // ============================================================
  // GATE: so ativa em modo teste por enquanto
  // ============================================================
  function isTestMode() {
    var qs = new URLSearchParams(location.search);
    if (qs.get('mtryon') === '1') {
      document.cookie = 'mtryon=1; max-age=2592000; path=/';
      return true;
    }
    return document.cookie.indexOf('mtryon=1') >= 0;
  }

  // ============================================================
  // PDP detection: hoje so Nuvemshop ("/produtos/<slug>/")
  // ============================================================
  function isProductPage() {
    return /\/produtos\/[^/?#]+\/?/.test(location.pathname);
  }

  // ============================================================
  // Produto info: tenta DOM scrape primeiro (rapido)
  // ============================================================
  function scrapeProductFromDOM() {
    // tema Idea da Nuvemshop: h1.product-name, .gallery-cell img
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

  // ============================================================
  // Cache local (hash bem simples — soma de caracteres)
  // ============================================================
  function hash(s) {
    var h = 0;
    if (!s) return 'x';
    for (var i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }
    return 'h' + Math.abs(h).toString(36);
  }
  function cacheKey(personHash, garmentUrl) { return 'tryon_' + personHash + '_' + hash(garmentUrl); }
  function getCached(personHash, garmentUrl) { try { return sessionStorage.getItem(cacheKey(personHash, garmentUrl)); } catch (e) { return null; } }
  function setCached(personHash, garmentUrl, b64) { try { sessionStorage.setItem(cacheKey(personHash, garmentUrl), b64); } catch (e) {} }

  // ============================================================
  // Rate limit anti-abuse (10 geracoes / dia / dispositivo)
  // ============================================================
  function rateLimitOk() {
    try {
      var key = 'tryon_rl_' + new Date().toISOString().slice(0, 10);
      var n = parseInt(sessionStorage.getItem(key) || '0', 10);
      if (n >= 10) return false;
      sessionStorage.setItem(key, String(n + 1));
      return true;
    } catch (e) { return true; }
  }

  // ============================================================
  // Analytics
  // ============================================================
  function track(event, params) {
    try {
      if (window.dataLayer) window.dataLayer.push(Object.assign({ event: 'tryon_' + event }, params || {}));
    } catch (e) {}
  }

  // ============================================================
  // Resize imagem cliente
  // ============================================================
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

  // ============================================================
  // Backend call
  // ============================================================
  function callTryOnOnce(personDataUri, garmentImageUrl, garmentDescription) {
    // timeout 150s — se backend nao retornar em 2.5min, falha em vez de pendurar
    var ctl = ('AbortController' in window) ? new AbortController() : null;
    var timer = ctl ? setTimeout(function(){ ctl.abort(); }, 150000) : null;
    return fetch(API_URL + '/tryon', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: ctl ? ctl.signal : undefined,
      body: JSON.stringify({
        person_image: personDataUri,
        garment_image_url: garmentImageUrl,
        garment_description: garmentDescription,
        quality: 'medium',  // medium = ~60s e ~R$1; high seria 90s+. UX ganha
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
  function callTryOn(personDataUri, productUrl, garmentImageUrl, garmentDescription) {
    // 1 retry com backoff se for erro transiente (5xx ou network)
    return callTryOnOnce(personDataUri, garmentImageUrl, garmentDescription).catch(function (e) {
      var transient = !e.status || (e.status >= 500 && e.status < 600);
      if (!transient) throw e;
      return new Promise(function (res) { setTimeout(res, 2500); })
        .then(function () { return callTryOnOnce(personDataUri, garmentImageUrl, garmentDescription); });
    });
  }

  function resolveProductIfNeeded(currentImageUrl) {
    // se imagem do DOM ja parece de catalogo (acdn-us.mitiendanube.com), usa direto.
    if (currentImageUrl && /acdn-us\.mitiendanube\.com|cdn\.shopify\.com|cloudinary\.com/.test(currentImageUrl)) {
      return Promise.resolve({ image_url: currentImageUrl, image_url_hd: currentImageUrl, suggested_category: null });
    }
    // fallback: pede pro backend resolver via og:image
    return fetch(API_URL + '/resolve-product', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_url: location.href }),
    }).then(function (r) { return r.json(); });
  }

  // ============================================================
  // UI — botao + modal em Shadow DOM (isolado do tema da loja)
  // ============================================================
  var STYLES = (
    ':host{all:initial;font-family:-apple-system,system-ui,sans-serif;}' +
    '*{box-sizing:border-box}' +
    '.btn{position:fixed;bottom:90px;right:20px;z-index:99998;background:#111;color:#fff;border:0;border-radius:999px;padding:13px 20px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;box-shadow:0 10px 30px rgba(0,0,0,.25);font-weight:600;display:flex;align-items:center;gap:8px}' +
    '@media(max-width:760px){.btn{bottom:80px;right:14px;padding:11px 16px;font-size:12px}}' +
    '.btn:hover{transform:translateY(-2px)}' +
    '.btn .emoji{font-size:18px}' +
    '.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99999;align-items:center;justify-content:center;padding:16px;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}' +
    '.overlay.show{display:flex}' +
    '.modal{background:#fff;width:100%;max-width:980px;max-height:92vh;border-radius:18px;display:flex;flex-direction:column;overflow:hidden}' +
    '.head{padding:18px 22px;border-bottom:1px solid #eee;display:flex;align-items:center;justify-content:space-between}' +
    '.head h2{margin:0;font-size:14px;letter-spacing:.2em;font-weight:700;text-transform:uppercase}' +
    '.head button{background:none;border:0;font-size:24px;cursor:pointer;color:#666;line-height:1}' +
    '.body{padding:22px;overflow:auto}' +
    '.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}' +
    '@media(max-width:760px){.grid{grid-template-columns:1fr}}' +
    '.card{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:14px}' +
    '.card h3{margin:0 0 10px;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#666;font-weight:700}' +
    '.preview{aspect-ratio:3/4;background:#f0f0f0;border-radius:8px;display:flex;align-items:center;justify-content:center;overflow:hidden}' +
    '.preview img{width:100%;height:100%;object-fit:cover}' +
    '.preview .empty{color:#999;font-size:12px;text-align:center;padding:18px}' +
    '.controls{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}' +
    '.btn-line{flex:1;min-width:120px;padding:10px 12px;background:#111;color:#fff;border:0;border-radius:6px;cursor:pointer;font-size:12px;letter-spacing:.1em;text-transform:uppercase;font-weight:600}' +
    '.btn-line.ghost{background:#fff;color:#111;border:1px solid #ddd}' +
    '.btn-line input{display:none}' +
    '.cta-row{padding:16px 22px;border-top:1px solid #eee;display:flex;gap:10px;align-items:center;justify-content:space-between;background:#fff}' +
    '.cta-row .status{font-size:12px;color:#666;font-family:ui-monospace,monospace;flex:1}' +
    '.cta-row .status.err{color:#dc2626}' +
    '.cta-row .status.ok{color:#059669}' +
    '.go{background:#111;color:#fff;border:0;padding:14px 28px;border-radius:8px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;cursor:pointer;font-weight:700}' +
    '.go:disabled{opacity:.4;cursor:not-allowed}' +
    '.bar{height:3px;background:#eee;overflow:hidden;border-radius:99px}' +
    '.bar>div{height:100%;background:#111;width:0;transition:width .3s}' +
    '.meta{font-size:10px;color:#999;margin-top:14px;text-align:center;letter-spacing:.06em}' +
    '#camera{display:none;width:100%;border-radius:8px}' +
    '.snap{margin-top:10px;display:none;text-align:center}' +
    '.snap.show{display:block}' +
    '.snap button{background:#fff;border:2px solid #111;color:#111;padding:10px 20px;border-radius:99px;cursor:pointer;font-weight:600;font-size:12px;letter-spacing:.1em;text-transform:uppercase}'
  );

  var TPL = (
    '<style>' + STYLES + '</style>' +
    '<button class="btn" id="trigger" aria-label="' + BRAND_LABEL + '">' +
      '<span class="emoji">' + BRAND_EMOJI + '</span><span>' + BRAND_LABEL + '</span>' +
    '</button>' +
    '<div class="overlay" id="overlay">' +
      '<div class="modal" role="dialog" aria-label="Provador virtual">' +
        '<div class="head"><h2>Provador Virtual</h2><button id="close" aria-label="Fechar">×</button></div>' +
        '<div class="body">' +
          '<div class="grid">' +
            '<div class="card">' +
              '<h3>1. Você</h3>' +
              '<div class="preview" id="pvPerson"><div class="empty">Sua foto de corpo inteiro</div></div>' +
              '<video id="camera" autoplay playsinline muted></video>' +
              '<div class="snap" id="snapWrap"><button id="snap">📸 Capturar</button></div>' +
              '<div class="controls">' +
                '<button class="btn-line ghost" id="useCamera">📷 Câmera</button>' +
                '<label class="btn-line">Enviar foto<input type="file" id="filePerson" accept="image/*"></label>' +
              '</div>' +
            '</div>' +
            '<div class="card">' +
              '<h3>2. Peça</h3>' +
              '<div class="preview" id="pvGarment"><div class="empty">carregando peça…</div></div>' +
              '<div class="meta" id="garmentInfo">—</div>' +
            '</div>' +
            '<div class="card">' +
              '<h3>3. Como ficaria</h3>' +
              '<div class="preview" id="pvResult"><div class="empty">Aperte PROVAR</div></div>' +
              '<div class="bar"><div id="bar"></div></div>' +
            '</div>' +
          '</div>' +
          '<div class="meta">Resultado gerado por IA. Cores e detalhes finos podem variar do produto real.</div>' +
        '</div>' +
        '<div class="cta-row">' +
          '<div class="status" id="status">Pronto.</div>' +
          '<button class="go" id="go" disabled>PROVAR</button>' +
        '</div>' +
      '</div>' +
    '</div>'
  );

  // ============================================================
  // Init
  // ============================================================
  function init() {
    if (!isTestMode()) return;
    if (!isProductPage()) return;

    // host element + Shadow DOM
    var host = document.createElement('div');
    host.id = 'tryon-host';
    host.style.all = 'initial';
    document.body.appendChild(host);
    var root = host.attachShadow({ mode: 'open' });
    root.innerHTML = TPL;

    var $ = function (s) { return root.querySelector(s); };
    var state = {
      personDataUri: null,
      personHash: null,
      garmentUrl: null,
      garmentInfo: null,
      progressInterval: null,
    };

    function setPreview(el, src) { el.innerHTML = '<img src="' + src + '" alt="">'; }
    function setStatus(msg, cls) {
      var s = $('#status'); s.textContent = msg; s.className = 'status' + (cls ? ' ' + cls : '');
    }
    function setBar(p) { $('#bar').style.width = Math.max(0, Math.min(100, p)) + '%'; }
    function progress(start) {
      var p = 5; setBar(p);
      clearInterval(state.progressInterval);
      if (start) state.progressInterval = setInterval(function () { p = Math.min(94, p + 1.8); setBar(p); }, 700);
    }
    function updateGoButton() { $('#go').disabled = !(state.personDataUri && state.garmentUrl); }

    // Trigger button
    $('#trigger').addEventListener('click', function () {
      track('open', { product: state.garmentInfo && state.garmentInfo.name });
      $('#overlay').classList.add('show');
      // pre-warm backend (evita cold start no PROVAR)
      try { fetch(API_URL + '/', { method: 'GET', cache: 'no-store' }).catch(function(){}); } catch(e){}
      // resolve peça quando abre o modal (lazy)
      if (!state.garmentUrl) loadGarment();
    });
    $('#close').addEventListener('click', function () {
      $('#overlay').classList.remove('show');
      stopCamera();
    });
    $('#overlay').addEventListener('click', function (e) {
      if (e.target === $('#overlay')) { $('#overlay').classList.remove('show'); stopCamera(); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && $('#overlay').classList.contains('show')) {
        $('#overlay').classList.remove('show');
        stopCamera();
      }
    });

    // Upload foto pessoa
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

    // Camera ao vivo
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

    // Carregar peça (chamado on-open)
    function loadGarment() {
      var dom = scrapeProductFromDOM();
      state.garmentInfo = dom;
      $('#garmentInfo').textContent = dom.name || '';
      setStatus('Buscando peça…');
      resolveProductIfNeeded(dom.image).then(function (r) {
        var img = r.image_url_hd || r.image_url || dom.image;
        state.garmentUrl = img;
        if (img) setPreview($('#pvGarment'), img);
        var note = r.suggested_category ? ' • ' + r.suggested_category : '';
        setStatus('Peça pronta' + note, 'ok');
        updateGoButton();
      }).catch(function (e) {
        // fallback: se DOM scrape pegou imagem, usa ela mesmo
        if (dom.image) {
          state.garmentUrl = dom.image;
          setPreview($('#pvGarment'), dom.image);
          setStatus('Peça pronta', 'ok');
          updateGoButton();
        } else {
          setStatus('Não consegui achar a imagem da peça', 'err');
        }
      });
    }

    // PROVAR
    $('#go').addEventListener('click', async function () {
      if (!state.personDataUri || !state.garmentUrl) return;
      if (!rateLimitOk()) { setStatus('Limite diário atingido', 'err'); return; }

      // cache
      var cached = getCached(state.personHash, state.garmentUrl);
      if (cached) {
        setBar(100);
        setPreview($('#pvResult'), 'data:image/jpeg;base64,' + cached);
        setStatus('Pronto (do cache).', 'ok');
        track('result', { cached: true });
        return;
      }

      $('#go').disabled = true;
      setStatus('Gerando — pode levar 1 a 2 minutos…'); progress(true);
      // pulso de mensagens pra usuario nao desistir
      var msgs = ['Analisando sua foto…','Identificando a peça…','Ajustando proporções…','Renderizando o resultado…','Quase lá, é a IA capricha…'];
      var msgIx = 0;
      var msgTimer = setInterval(function(){
        msgIx = (msgIx + 1) % msgs.length;
        setStatus(msgs[msgIx]);
      }, 12000);
      state._msgTimer = msgTimer;
      track('generate', { product: state.garmentInfo && state.garmentInfo.name });
      try {
        var d = await callTryOn(
          state.personDataUri,
          location.href,
          state.garmentUrl,
          state.garmentInfo && state.garmentInfo.name
        );
        clearInterval(state.progressInterval); clearInterval(state._msgTimer); setBar(100);
        setPreview($('#pvResult'), 'data:image/jpeg;base64,' + d.image_b64);
        setStatus('Pronto.', 'ok');
        setCached(state.personHash, state.garmentUrl, d.image_b64);
        track('result', { cached: false });
      } catch (e) {
        clearInterval(state.progressInterval); clearInterval(state._msgTimer); setBar(0);
        setStatus('Erro: ' + e.message, 'err');
        track('error', { message: e.message });
      } finally {
        $('#go').disabled = false;
      }
    });
  }

  // expoe namespace
  window.TryOn = { _loaded: true, init: init };

  // auto-init
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
