"""
Martina TryOn — backend Flask (substitui o Cloudflare Worker)
Roda no Render. Variavel obrigatoria: REPLICATE_API_TOKEN (formato r8_...)

Endpoints:
  GET  /                 healthcheck
  GET  /test             pagina de teste hospedada
  POST /tryon            cria prediction
  GET  /tryon/<id>       polling
  POST /resolve-product  recebe {page_url} e devolve URL da imagem da peca via og:image
"""
import os
import re
import time
import json
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

REPLICATE_MODEL_OWNER = "cuuupid"
REPLICATE_MODEL_NAME  = "idm-vton"

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

def _token():
    t = os.environ.get("REPLICATE_API_TOKEN", "")
    return t or None

def _hdr():
    return {
        "Authorization": f"Token {_token()}",
        "Content-Type": "application/json",
    }

_version_cache = {"id": None, "ts": 0}
def get_latest_version_id():
    if _version_cache["id"] and time.time() - _version_cache["ts"] < 3600:
        return _version_cache["id"]
    r = requests.get(
        f"https://api.replicate.com/v1/models/{REPLICATE_MODEL_OWNER}/{REPLICATE_MODEL_NAME}",
        headers=_hdr(), timeout=20,
    )
    r.raise_for_status()
    vid = r.json()["latest_version"]["id"]
    _version_cache["id"] = vid
    _version_cache["ts"] = time.time()
    return vid

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "martina-tryon",
        "model": f"{REPLICATE_MODEL_OWNER}/{REPLICATE_MODEL_NAME}",
        "has_token": bool(_token()),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

@app.route("/tryon", methods=["POST"])
def tryon_create():
    if not _token():
        return jsonify({"error": "REPLICATE_API_TOKEN not configured"}), 500
    body = request.get_json(silent=True) or {}
    person = body.get("person_image")
    garment = body.get("garment_image_url")
    desc = body.get("garment_description") or "shirt"
    category = body.get("category") or "upper_body"
    if not person or not garment:
        return jsonify({"error": "person_image e garment_image_url obrigatorios"}), 400
    try:
        vid = get_latest_version_id()
        r = requests.post(
            "https://api.replicate.com/v1/predictions",
            headers=_hdr(),
            json={
                "version": vid,
                "input": {
                    "human_img": person,
                    "garm_img": garment,
                    "garment_des": desc,
                    "category": category,
                    "crop": False, "force_dc": False, "mask_only": False,
                },
            }, timeout=30,
        )
        data = r.json()
        if not r.ok:
            return jsonify({"error": f"replicate {r.status_code}", "detail": data}), 500
        return jsonify({
            "id": data["id"], "status": data["status"],
            "poll_url": f"{request.host_url.rstrip('/')}/tryon/{data['id']}",
        }), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tryon/<pid>", methods=["GET"])
def tryon_status(pid):
    if not _token():
        return jsonify({"error": "REPLICATE_API_TOKEN not configured"}), 500
    try:
        r = requests.get(f"https://api.replicate.com/v1/predictions/{pid}",
                         headers=_hdr(), timeout=20)
        d = r.json()
        if not r.ok:
            return jsonify({"error": f"replicate {r.status_code}", "detail": d}), 500
        out = d.get("output")
        if isinstance(out, list) and out: out = out[0]
        return jsonify({
            "id": d["id"], "status": d["status"], "output": out, "error": d.get("error"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/resolve-product", methods=["POST"])
def resolve_product():
    """Recebe {page_url} e tenta extrair URL da imagem principal do produto (og:image)."""
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
        # tenta substituir versao -640- por -1024- pra qualidade maior
        hd = re.sub(r"-640-0\.(webp|jpg|jpeg|png)$", r"-1024-0.\1", img, flags=re.I)
        return jsonify({"image_url": img, "image_url_hd": hd})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================================================
# Pagina de teste hospedada em /test
# =========================================================
TEST_HTML = r"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Martina TryOn — Teste</title>
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
input[type=url],input[type=text]{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:6px;margin-top:10px;font-size:13px}
.actions{text-align:center;margin:24px 0 8px}
button.go{background:var(--accent);color:#fff;border:0;padding:14px 28px;border-radius:8px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;cursor:pointer}
button.go:disabled{opacity:.5;cursor:not-allowed}
.status{font-family:ui-monospace,monospace;font-size:12px;color:var(--muted);padding:8px 12px;background:#fff;border:1px solid var(--line);border-radius:8px;margin-top:8px;min-height:32px;white-space:pre-wrap}
.status.err{color:var(--err)}.status.ok{color:var(--ok)}
.progress{height:4px;background:#eee;border-radius:999px;overflow:hidden;margin-top:6px}
.progress>div{height:100%;background:var(--accent);width:0%;transition:width .3s}
.meta{font-size:11px;color:#888;margin-top:8px;text-align:center}
.help{font-size:11px;color:#888;margin-top:6px;line-height:1.5}
</style></head><body>
<div class="wrap">
  <h1>MARTINA</h1>
  <div class="sub">Provador Virtual — Teste</div>

  <div class="row">
    <div class="card">
      <h3>1. Sua foto</h3>
      <div class="preview" id="prevPerson"><div class="empty">Sobe uma foto de corpo inteiro, de frente, sem cortar a peça que vai trocar</div></div>
      <label class="upload">Escolher foto<input type="file" id="filePerson" accept="image/*"></label>
    </div>
    <div class="card">
      <h3>2. Peça da Martina</h3>
      <div class="preview" id="prevGarment"><div class="empty">Cola a URL da PÁGINA do produto (.com.br/produtos/...) — eu extraio a imagem automaticamente</div></div>
      <input type="url" id="productPage" placeholder="https://www.martinaoficial.com.br/produtos/...">
      <input type="text" id="garmentDesc" placeholder="Descrição (ex: blusa preta de manga longa)">
      <div class="help">Dica: copia a URL direto da barra do navegador quando estiver na página do produto.</div>
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

  <div class="meta">Modelo: cuuupid/idm-vton via Replicate · ~15-25s/imagem · ~R$ 0,28 por geração</div>
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
  if (!u || !u.includes("/produtos/")) return;
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
      setStatus("Peça resolvida: " + img.split("/").pop(), "ok");
    } catch(err){ setStatus("Erro extraindo imagem: " + err.message, "err"); }
  }, 500);
});

$("#btnGo").addEventListener("click", async ()=>{
  if (!personState.dataUri){ setStatus("Falta a foto da pessoa.", "err"); return; }
  if (!garmentState.url){ setStatus("Falta a URL do produto.", "err"); return; }
  $("#btnGo").disabled = true;
  setStatus("Enviando..."); setBar(5);
  try {
    const r = await fetch(API + "/tryon", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({
        person_image: personState.dataUri,
        garment_image_url: garmentState.url,
        garment_description: $("#garmentDesc").value.trim() || "shirt",
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    setStatus("Iniciado · id " + data.id); setBar(15);
    let tries=0;
    while (tries < 60){
      await new Promise(res => setTimeout(res, 2500));
      tries++;
      const pr = await fetch(API + "/tryon/" + data.id);
      const pd = await pr.json();
      setBar(Math.min(95, 15 + tries*4));
      setStatus(`Status: ${pd.status} · ${tries}`);
      if (pd.status === "succeeded"){
        setBar(100); setStatus("Pronto.", "ok");
        setPreview($("#prevResult"), pd.output);
        return;
      }
      if (["failed","canceled"].includes(pd.status)){
        throw new Error(pd.error || ("prediction " + pd.status));
      }
    }
    throw new Error("timeout");
  } catch(e){ setStatus("Erro: " + e.message, "err"); setBar(0); }
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
