"""
Martina TryOn — backend Flask (motor: OpenAI Responses + gpt-image-1)
Roda no Render. Variavel obrigatoria: OPENAI_API_KEY (formato sk-...)

Endpoints:
  GET  /                 healthcheck
  GET  /test             pagina de teste hospedada
  POST /tryon            cria try-on (sincrono ~20-40s) -> {image_b64}
  POST /resolve-product  recebe {page_url} e devolve URL da imagem (og:image)
"""
import os
import re
import time
import base64
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------------------------------------------------------
def _openai_key():
    t = os.environ.get("OPENAI_API_KEY", "")
    return t or None

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "martina-tryon",
        "engine": "openai-gpt-image-1",
        "has_token": bool(_openai_key()),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

# ---------------------------------------------------------
# /tryon — usa Responses API com tool image_generation (gpt-image-1)
# ---------------------------------------------------------
@app.route("/tryon", methods=["POST"])
def tryon_create():
    if not _openai_key():
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(silent=True) or {}
    person = body.get("person_image")          # data URI ou URL publica
    garment = body.get("garment_image_url")    # URL publica
    desc = (body.get("garment_description") or "esta peca de roupa").strip()
    quality = body.get("quality") or "medium"  # low | medium | high
    size = body.get("size") or "1024x1024"     # 1024x1024 | 1024x1536 | 1536x1024

    # auto-detecta categoria pela descricao se nao for fornecida
    # ordem: peca > tecido. "jaqueta jeans" vence sobre "jeans" sozinho.
    category = body.get("category")
    if not category:
        dlow = desc.lower()
        upper_keys = ["jaqueta", "casaco", "blazer", "camisa", "camiseta", "blusa", "regata", "polo",
                      " top ", "cropped", "moletom", "sueter", "suéter", "tricot", "cardigan",
                      "colete", "coat", "hoodie", "shirt", "tee", "jacket"]
        lower_keys = ["calca", "calça", "short", "bermuda", "saia", "legging", "pant", "trouser",
                      "jeans"]  # jeans aqui so como fallback se nao tiver upper_key antes
        dress_keys = ["vestido", "dress", "macacao", "macacão", "jumpsuit", "macaquinho"]
        if any(w in dlow for w in dress_keys):
            category = "dresses"
        elif any(w in dlow for w in upper_keys):
            category = "upper_body"
        elif any(w in dlow for w in lower_keys):
            category = "lower_body"
        else:
            category = "upper_body"

    if not person or not garment:
        return jsonify({"error": "person_image e garment_image_url obrigatorios"}), 400

    # Constrói prompt categoria-aware
    if category == "lower_body":
        body_region = (
            "Substitua APENAS a parte de baixo da roupa da pessoa (calca, short, bermuda ou saia) "
            "pela peca da primeira imagem. NAO mude a blusa, camisa ou parte de cima do corpo. "
            "NAO mude os sapatos."
        )
        fidelity = (
            "Preserve com maxima fidelidade a cor, textura, lavagem (no caso de jeans), "
            "rasgos, costuras, bolsos, comprimento, modelagem (skinny/wide/oversized) e formato da peca."
        )
    elif category == "dresses":
        body_region = (
            "Substitua o conjunto de roupa atual da pessoa (blusa+calca ou blusa+saia) "
            "por este vestido ou macacao da primeira imagem, cobrindo o corpo inteiro como mostrado."
        )
        fidelity = (
            "Preserve com maxima fidelidade a cor, textura, estampa, decote, alcas, comprimento, "
            "modelagem e formato do vestido/macacao."
        )
    else:  # upper_body (default)
        body_region = (
            "Substitua APENAS a parte de cima da roupa da pessoa (blusa, camisa, camiseta, regata ou jaqueta) "
            "pela peca da primeira imagem. NAO mude a calca, short ou parte de baixo do corpo. "
            "NAO mude os sapatos."
        )
        fidelity = (
            "Preserve com maxima fidelidade a textura, cor, padrao, estampa, recortes, "
            "comprimento das mangas, decote, modelagem e formato da peca."
        )

    prompt = (
        f"Coloque a peca de roupa que aparece na primeira imagem (descricao: {desc}, categoria: {category}) "
        f"no corpo da pessoa que aparece na segunda imagem. "
        f"Mantenha exatamente a face, cabelo, maos, e o cenario/fundo da pessoa. "
        f"{body_region} "
        f"{fidelity} "
        f"Mantenha a pose, iluminacao e perspectiva originais da segunda imagem. "
        f"Resultado fotorealista, sem texto, sem marca dagua, sem distorcao corporal."
    )

    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {_openai_key()}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4.1-mini",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": garment},
                            {"type": "input_image", "image_url": person},
                        ],
                    }
                ],
                "tools": [
                    {
                        "type": "image_generation",
                        "output_format": "jpeg",
                        "quality": quality,
                        "size": size,
                    }
                ],
            },
            timeout=180,
        )
        data = r.json()
        if not r.ok:
            return jsonify({"error": f"openai {r.status_code}", "detail": data}), 500

        # Procura o output da ferramenta image_generation
        img_b64 = None
        for item in data.get("output", []):
            if item.get("type") == "image_generation_call":
                img_b64 = item.get("result")
                if img_b64:
                    break
        if not img_b64:
            return jsonify({
                "error": "openai response sem imagem",
                "output_types": [it.get("type") for it in data.get("output", [])],
                "detail": data,
            }), 500

        return jsonify({
            "image_b64": img_b64,
            "model": "gpt-image-1",
            "usage": data.get("usage"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------
# /resolve-product — extrai imagem do produto Nuvemshop
# ---------------------------------------------------------
@app.route("/resolve-product", methods=["POST"])
def resolve_product():
    body = request.get_json(silent=True) or {}
    url = (body.get("page_url") or "").strip()
    if not url:
        return jsonify({"error": "page_url obrigatorio"}), 400
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
        html = r.text
        og = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if not og:
            og = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', html, re.I)
        if not og:
            return jsonify({"error": "og:image nao encontrada", "status": r.status_code}), 404
        img = og.group(1).replace("http://", "https://")
        # tenta substituir versao -640- por -1024- pra qualidade maior, mas VALIDA antes
        hd_candidate = re.sub(r"-640-0\.(webp|jpg|jpeg|png)$", r"-1024-0.\1", img, flags=re.I)
        hd = img  # default 640
        if hd_candidate != img:
            try:
                hr = requests.head(hd_candidate, timeout=5, allow_redirects=True)
                if hr.status_code == 200:
                    hd = hd_candidate
            except Exception:
                pass
        # detecta categoria pela URL + titulo. ordem: peca > tecido.
        ulow = (url + " " + html[:5000]).lower()
        upper_keys = ["jaqueta", "casaco", "blazer", "camisa", "camiseta", "blusa", "regata", "polo",
                      "cropped", "moletom", "sueter", "suéter", "tricot", "cardigan", "colete",
                      "coat", "hoodie", "shirt", "tee", "jacket", "/top-", "/tops-", "-top-"]
        lower_keys = ["calca", "calça", "short", "bermuda", "saia", "legging", "pant", "trouser", "jeans"]
        dress_keys = ["vestido", "dress", "macacao", "macacão", "jumpsuit", "macaquinho"]
        if any(w in ulow for w in dress_keys):
            suggested = "dresses"
        elif any(w in ulow for w in upper_keys):
            suggested = "upper_body"
        elif any(w in ulow for w in lower_keys):
            suggested = "lower_body"
        else:
            suggested = "upper_body"
        return jsonify({"image_url": img, "image_url_hd": hd, "suggested_category": suggested})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------
# /test — pagina HTML hospedada
# ---------------------------------------------------------
TEST_HTML = r"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Martina TryOn - Teste</title>
<style>
:root{--bg:#fafafa;--fg:#111;--muted:#666;--line:#e5e5e5;--accent:#000;--ok:#059669;--err:#dc2626}
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px}
h1{font-weight:700;letter-spacing:.3em;margin:0 0 4px;text-align:center}
.sub{text-align:center;color:var(--muted);margin-bottom:28px;font-size:13px;letter-spacing:.18em;text-transform:uppercase}
.wrap{max-width:1100px;margin:0 auto}
.row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
@media (max-width:900px){.row{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px;min-height:320px}
.card h3{margin:0 0 10px;font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}
.preview{aspect-ratio:3/4;background:#f4f4f4;border-radius:8px;display:flex;align-items:center;justify-content:center;overflow:hidden}
.preview img{width:100%;height:100%;object-fit:cover}
.preview .empty{color:#aaa;font-size:12px;text-align:center;padding:24px}
label.upload{display:block;margin-top:10px;padding:10px 14px;background:#111;color:#fff;border-radius:6px;text-align:center;cursor:pointer;font-size:13px;letter-spacing:.12em;text-transform:uppercase}
label.upload input{display:none}
input[type=url],input[type=text],select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:6px;margin-top:10px;font-size:13px;background:#fff}
.actions{text-align:center;margin:24px 0 8px}
button.go{background:var(--accent);color:#fff;border:0;padding:14px 28px;border-radius:8px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;cursor:pointer}
button.go:disabled{opacity:.5;cursor:not-allowed}
.status{font-family:ui-monospace,monospace;font-size:12px;color:var(--muted);padding:8px 12px;background:#fff;border:1px solid var(--line);border-radius:8px;margin-top:8px;min-height:32px;white-space:pre-wrap}
.status.err{color:var(--err)}.status.ok{color:var(--ok)}
.progress{height:4px;background:#eee;border-radius:999px;overflow:hidden;margin-top:6px}
.progress>div{height:100%;background:var(--accent);width:0%;transition:width .3s}
.meta{font-size:11px;color:#888;margin-top:8px;text-align:center}
.help{font-size:11px;color:#888;margin-top:6px;line-height:1.5}
.opts{display:flex;gap:8px;margin-top:10px}
.opts select{flex:1;margin-top:0}
</style></head><body>
<div class="wrap">
  <h1>MARTINA</h1>
  <div class="sub">Provador Virtual - Teste (motor: GPT-Image-1)</div>

  <div class="row">
    <div class="card">
      <h3>1. Sua foto</h3>
      <div class="preview" id="prevPerson"><div class="empty">Sobe uma foto sua de corpo inteiro, de frente</div></div>
      <label class="upload">Escolher foto<input type="file" id="filePerson" accept="image/*"></label>
    </div>
    <div class="card">
      <h3>2. Peça da Martina</h3>
      <div class="preview" id="prevGarment"><div class="empty">Cola a URL da pagina do produto (.com.br/produtos/...)</div></div>
      <input type="url" id="productPage" placeholder="URL da pagina do produto (qualquer loja)">
      <input type="text" id="garmentDesc" placeholder="Descricao (ex: calca jeans navy blue oversized)">
      <div class="opts">
        <select id="category">
          <option value="auto" selected>Categoria: auto-detectar</option>
          <option value="upper_body">Parte de cima (blusa/camisa/jaqueta)</option>
          <option value="lower_body">Parte de baixo (calca/short/saia)</option>
          <option value="dresses">Vestido / macacao</option>
        </select>
        <select id="quality">
          <option value="medium">Qualidade media (~R$ 1)</option>
          <option value="high" selected>Qualidade alta (~R$ 2,30)</option>
          <option value="low">Qualidade rapida (~R$ 0,30)</option>
        </select>
      </div>
      <div class="help">Dica: copia a URL direto da barra do navegador quando estiver na pagina do produto.</div>
    </div>
    <div class="card">
      <h3>3. Resultado</h3>
      <div class="preview" id="prevResult"><div class="empty">Aperta PROVAR</div></div>
      <div class="status" id="statusBox">pronto.</div>
      <div class="progress"><div id="bar"></div></div>
    </div>
  </div>

  <div class="actions">
    <button class="go" id="btnGo">PROVAR</button>
  </div>

  <div class="meta">Modelo: openai/gpt-image-1 (via Responses API) - ~20-40s/imagem</div>
</div>

<script>
const API = location.origin;
const $ = s => document.querySelector(s);
const personState = { dataUri: null };
const garmentState = { url: null };

function setPreview(el, src){ el.innerHTML = `<img src="${src}" alt="">`; }
function setStatus(msg, cls=""){ const s=$("#statusBox"); s.textContent=msg; s.className="status "+cls; }
function setBar(p){ $("#bar").style.width = Math.max(0,Math.min(100,p)) + "%"; }

async function resize(file, maxSide=1024){
  const img = await new Promise((res,rej)=>{ const i=new Image(); i.onload=()=>res(i); i.onerror=rej; i.src=URL.createObjectURL(file); });
  const s = Math.min(1, maxSide / Math.max(img.width, img.height));
  const w = Math.round(img.width*s), h = Math.round(img.height*s);
  const c = document.createElement("canvas"); c.width=w; c.height=h;
  c.getContext("2d").drawImage(img, 0, 0, w, h);
  return c.toDataURL("image/jpeg", .92);
}

$("#filePerson").addEventListener("change", async e=>{
  const f = e.target.files[0]; if (!f) return;
  const d = await resize(f, 1024); personState.dataUri = d;
  setPreview($("#prevPerson"), d);
});

let resolveTimer = null;
$("#productPage").addEventListener("input", e=>{
  clearTimeout(resolveTimer);
  const u = e.target.value.trim();
  if (!u || !/^https?:\/\//.test(u)) return;
  resolveTimer = setTimeout(async ()=>{
    setStatus("Extraindo imagem do produto...");
    try {
      const r = await fetch(API + "/resolve-product", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page_url: u }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "erro");
      const img = d.image_url_hd || d.image_url;
      garmentState.url = img;
      setPreview($("#prevGarment"), img);
      // se categoria estava em "auto", aplica a sugestao do backend
      if ($("#category").value === "auto" && d.suggested_category) {
        garmentState.suggestedCategory = d.suggested_category;
      }
      const catNote = d.suggested_category ? ` (categoria detectada: ${d.suggested_category})` : "";
      setStatus("Peca resolvida: " + img.split("/").pop() + catNote, "ok");
    } catch(err){ setStatus("Erro extraindo imagem: " + err.message, "err"); }
  }, 500);
});

let progressInterval = null;
function startFakeProgress(){
  let p = 5;
  setBar(p);
  if (progressInterval) clearInterval(progressInterval);
  progressInterval = setInterval(() => {
    p = Math.min(95, p + 1.5);
    setBar(p);
  }, 700);
}
function stopProgress(final=100){
  if (progressInterval) clearInterval(progressInterval);
  progressInterval = null;
  setBar(final);
}

$("#btnGo").addEventListener("click", async ()=>{
  if (!personState.dataUri){ setStatus("Falta a foto da pessoa.", "err"); return; }
  if (!garmentState.url){ setStatus("Falta a URL do produto.", "err"); return; }
  $("#btnGo").disabled = true;
  setStatus("Gerando (~20-40s)..."); startFakeProgress();
  try {
    const r = await fetch(API + "/tryon", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({
        person_image: personState.dataUri,
        garment_image_url: garmentState.url,
        garment_description: $("#garmentDesc").value.trim() || "esta peca de roupa",
        quality: $("#quality").value,
        category: ($("#category").value === "auto" ? (garmentState.suggestedCategory || null) : $("#category").value),
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    stopProgress(100);
    setStatus("Pronto.", "ok");
    setPreview($("#prevResult"), "data:image/jpeg;base64," + data.image_b64);
  } catch(e){ stopProgress(0); setStatus("Erro: " + e.message, "err"); }
  finally { $("#btnGo").disabled = false; }
});
</script>
</body></html>
"""

@app.route("/test", methods=["GET"])
def test_page():
    return Response(TEST_HTML, mimetype="text/html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
