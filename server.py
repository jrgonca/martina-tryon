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
import json
import hmac
import hashlib
import sqlite3
import base64
import threading
from collections import defaultdict, deque
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)

# ---------------------------------------------------------
# Events DB (analytics do funil TryOn).
# v0 piloto: SQLite local no /tmp do Render (efêmero entre deploys — ACEITAVEL).
# SaaS v1: migra schema pra Postgres com tenant_id real, particionado.
# Schema JA E multi-tenant: coluna tenant em tudo, hoje 'martina' hardcoded.
# ---------------------------------------------------------
_DB_PATH = os.environ.get("EVENTS_DB", "/tmp/tryon_events.db")
_DB_LOCK = threading.Lock()

def _db():
    conn = sqlite3.connect(_DB_PATH, timeout=10, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _init_db():
    with _DB_LOCK, _db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant      TEXT NOT NULL,
                client_id   TEXT,
                session_id  TEXT,
                event_type  TEXT NOT NULL,
                product_url TEXT,
                product_name TEXT,
                garment_category TEXT,
                order_id    TEXT,
                order_value REAL,
                ts          REAL NOT NULL,
                ip          TEXT,
                meta        TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_tenant_ts ON events(tenant, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(tenant, event_type, ts)")
        # idempotencia: lookup rapido por (tenant, event_type, order_id) p/ dedup purchase_attributed
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_order ON events(tenant, event_type, order_id)")
        # ---- Perfil de tamanho (piloto recomendacao). Chave (tenant, user_hash).
        # user_hash = hash local gerado no widget (nao PII). SaaS v1: pode virar user_id real.
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                tenant       TEXT NOT NULL,
                user_hash    TEXT NOT NULL,
                size_top     TEXT,
                size_bottom  TEXT,
                size_dress   TEXT,
                fit_pref     TEXT,   -- 'colado' | 'ideal' | 'soltinho' | null
                updated_at   REAL,
                PRIMARY KEY (tenant, user_hash)
            )
        """)
        # ---- Feedback pos-tryon (motor de aprendizado).
        # Sistema aprende: se muita gente com tamanho_declarado X deu 'apertado' no size_suggested da peça Y,
        # sugere +1 pra proximos usuarios com perfil semelhante.
        c.execute("""
            CREATE TABLE IF NOT EXISTS size_feedback (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant            TEXT NOT NULL,
                user_hash         TEXT,
                product_url       TEXT NOT NULL,
                product_name      TEXT,
                garment_category  TEXT,
                size_declared     TEXT,   -- tamanho usual da usuaria
                size_suggested    TEXT,   -- o que o sistema sugeriu
                size_tried        TEXT,   -- qual ela viu no try-on
                feedback          TEXT,   -- 'apertado' | 'ideal' | 'largo'
                ts                REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_sizefb_product ON size_feedback(tenant, product_url, feedback)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sizefb_user ON size_feedback(tenant, user_hash, ts)")
_init_db()

# Tipos de evento aceitos (defesa contra spam de campo livre)
_VALID_EVENT_TYPES = {
    "tryon_view",         # botão injetado/visível na PDP
    "tryon_open",         # modal aberto
    "tryon_complete",     # resultado recebido OK
    "tryon_buy_click",    # clique COMPRAR no card de resultado
    "purchase_attributed" # Nuvemshop registrou compra de produto provado mesma sessão
}

# Cap de retencao do banco (v0 efêmero no /tmp Render mesmo assim).
# SaaS v1: politica formal por tenant (LGPD), particionado por mes.
_DB_RETENTION_DAYS = int(os.environ.get("DB_RETENTION_DAYS", "90"))

def _meta_sanitize(raw):
    """Aceita dict com max 10 chaves; valores stringificados curtos. Anti-spam."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in list(raw.items())[:10]:
        ks = str(k)[:32]
        if isinstance(v, (str, int, float, bool)) or v is None:
            vs = str(v)[:200]
        else:
            vs = json.dumps(v)[:200]
        out[ks] = vs
    return out

# ---------------------------------------------------------
# Auth simples do painel/stats (v0) — capability URL.
# Estrutura: storage HASH do token (sha256) hardcoded. Token original SO Junior conhece.
# Quem não tem o token, vê 404. Quem tem, acessa via /panel/<token> ou /stats?key=<token>.
# Comparacao com hmac.compare_digest pra evitar timing attack.
# SaaS v1: API keys por tenant na DB com revogacao.
# ---------------------------------------------------------
_PANEL_HASH = "82760f0698fd517afc47db92dd4ce68477907c9d5f37f17ea70edcf8164e9a87"  # sha256 do token "martina2026" (Junior lembra)

def _panel_authorized(token):
    if not token:
        return False
    given_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(given_hash, _PANEL_HASH)

# ---------------------------------------------------------
# Origin allowlist + CORS
# Pra SaaS futuro: ALLOWED_ORIGINS vira lista por tenant no DB.
# Hoje: env var CSV ou default Martina.
# ---------------------------------------------------------
_DEFAULT_ORIGINS = [
    "https://martinaoficial.com.br",
    "https://www.martinaoficial.com.br",
    "https://martina67.lojavirtualnuvem.com.br",
]
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", ",".join(_DEFAULT_ORIGINS)).split(",") if o.strip()]
# Dev sempre permitido + null (file://, sandbox de teste)
DEV_ORIGINS = ["http://localhost", "http://127.0.0.1", "null"]

def _origin_allowed(origin):
    if not origin:
        return False
    if origin in ALLOWED_ORIGINS:
        return True
    return any(origin.startswith(d) for d in DEV_ORIGINS)

# CORS dinâmico: só permite origens autorizadas (e Origin: null pra fetch direto)
CORS(app, resources={r"/*": {"origins": ALLOWED_ORIGINS + DEV_ORIGINS}}, supports_credentials=False)

# ---------------------------------------------------------
# Rate limit por IP (in-memory). Pra SaaS escalar: Redis.
# /tryon é o caro (custa OpenAI). Mais restrito.
# ---------------------------------------------------------
_RL_LOCK = threading.Lock()
_RL_BUCKETS = defaultdict(deque)  # ip -> deque de timestamps

def _check_rate_limit(ip, limit_per_min=10, limit_per_hour=60):
    """Sliding window. Limites altos pra teste interno; restringir em prod."""
    now = time.time()
    with _RL_LOCK:
        bucket = _RL_BUCKETS[ip]
        # purga >1h
        while bucket and bucket[0] < now - 3600:
            bucket.popleft()
        last_min = sum(1 for t in bucket if t > now - 60)
        last_hour = len(bucket)
        if last_min >= limit_per_min or last_hour >= limit_per_hour:
            return False
        bucket.append(now)
        return True

def _client_ip():
    # Render põe IP real em X-Forwarded-For (primeiro hop)
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"

# ---------------------------------------------------------
# Cache simples de /resolve-product (in-memory, TTL).
# v0 piloto: dict + lock. SaaS v1: Redis por tenant.
# Hit = zero scrape na loja. Miss = scrape e armazena.
# ---------------------------------------------------------
_RESOLVE_CACHE = {}              # url -> (timestamp_inserido, payload_dict)
_RESOLVE_CACHE_LOCK = threading.Lock()
_RESOLVE_TTL = int(os.environ.get("RESOLVE_TTL_S", "600"))   # 10min default
_RESOLVE_MAX = int(os.environ.get("RESOLVE_MAX", "1000"))    # cap memória

def _resolve_get(url):
    with _RESOLVE_CACHE_LOCK:
        ent = _RESOLVE_CACHE.get(url)
        if not ent:
            return None
        ts, data = ent
        if time.time() - ts > _RESOLVE_TTL:
            _RESOLVE_CACHE.pop(url, None)
            return None
        return data

def _resolve_set(url, data):
    with _RESOLVE_CACHE_LOCK:
        _RESOLVE_CACHE[url] = (time.time(), data)
        # eviction: se passar do cap, joga fora os 20% mais velhos
        if len(_RESOLVE_CACHE) > _RESOLVE_MAX:
            items = sorted(_RESOLVE_CACHE.items(), key=lambda kv: kv[1][0])
            for k, _ in items[: max(1, _RESOLVE_MAX // 5)]:
                _RESOLVE_CACHE.pop(k, None)

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
        "resolve_cache_size": len(_RESOLVE_CACHE),
        "resolve_ttl_s": _RESOLVE_TTL,
    })

# ---------------------------------------------------------
# /tryon — usa Responses API com tool image_generation (gpt-image-1)
# ---------------------------------------------------------
@app.route("/tryon", methods=["POST"])
def tryon_create():
    # Origin allowlist (anti-abuso: outro site nao pode embedar nosso widget e queimar nossa key)
    origin = request.headers.get("Origin", "")
    if origin and not _origin_allowed(origin):
        return jsonify({"error": "origin nao autorizado"}), 403
    # Rate limit por IP (10/min, 60/h) — protege OpenAI USD
    ip = _client_ip()
    if not _check_rate_limit(ip, limit_per_min=10, limit_per_hour=60):
        return jsonify({"error": "rate limit excedido — tente novamente em alguns minutos"}), 429
    if not _openai_key():
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(silent=True) or {}
    person = body.get("person_image")          # data URI ou URL publica
    garment = body.get("garment_image_url")    # URL publica
    desc = (body.get("garment_description") or "esta peca de roupa").strip()
    quality = body.get("quality") or "low"  # low | medium | high — low p/ economia 4x
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
    # Anti-abuso. Mais permissivo que /tryon (so faz scrape, nao usa OpenAI).
    origin = request.headers.get("Origin", "")
    if origin and not _origin_allowed(origin):
        return jsonify({"error": "origin nao autorizado"}), 403
    if not _check_rate_limit(_client_ip(), limit_per_min=30, limit_per_hour=300):
        return jsonify({"error": "rate limit excedido"}), 429
    body = request.get_json(silent=True) or {}
    url = (body.get("page_url") or "").strip()
    if not url:
        return jsonify({"error": "page_url obrigatorio"}), 400
    # Cache hit -> retorna direto, sem scrape (cache hint pro debug)
    cached = _resolve_get(url)
    if cached:
        out = dict(cached)
        out["_cache"] = "hit"
        return jsonify(out)
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
        # Nome do produto (og:title ou <title>). Usado pra detectar oversized/slim/etc.
        og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if not og_title:
            og_title = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:title["\']', html, re.I)
        if og_title:
            product_name = og_title.group(1).strip()[:200]
        else:
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            product_name = (title_m.group(1).strip() if title_m else "")[:200]
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
        payload = {"image_url": img, "image_url_hd": hd, "suggested_category": suggested, "product_name": product_name}
        _resolve_set(url, payload)
        out = dict(payload)
        out["_cache"] = "miss"
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------
# Recomendacao de tamanho (piloto v0).
# Regra simples: tamanho usual + palavra-chave no nome do produto -> 3 caimentos.
# Sem Vision, sem tabela por peca. Feedback loop refina peca-a-peca.
# ---------------------------------------------------------
_SIZE_GRADE = ["PP", "P", "M", "G", "GG", "XG"]  # grade padrao Martina (extensivel via env por tenant)
_OVERSIZED_KWS = ["oversized", "over size", "over-sized", "overs", " over ", "boxy", "amplo", "amplinho"]
_SLIM_KWS = ["slim", "skinny", "justa", "justo", "colada", "colado", "aderente", "canelado", "canelada", "segunda pele"]

def _size_shift(size, delta):
    """Retorna tamanho +/- delta na grade. Clamp nas extremidades."""
    if size not in _SIZE_GRADE:
        return size
    idx = _SIZE_GRADE.index(size)
    new_idx = max(0, min(len(_SIZE_GRADE) - 1, idx + delta))
    return _SIZE_GRADE[new_idx]

def _detect_fit_type(product_name):
    """Retorna 'oversized' | 'slim' | 'regular' baseado em palavras-chave do nome."""
    if not product_name:
        return "regular"
    low = " " + product_name.lower() + " "
    if any(k in low for k in _OVERSIZED_KWS):
        return "oversized"
    if any(k in low for k in _SLIM_KWS):
        return "slim"
    return "regular"

def _apply_feedback_shift(tenant, product_url, size_declared):
    """
    Ajuste dinamico: se >=3 feedbacks de usuarios com mesmo tamanho declarado deram 'apertado'
    em maioria absoluta, sugere +1. Se 'largo', sugere -1. Empate/pouco dado = 0.
    """
    try:
        with _db() as c:
            rows = c.execute("""
                SELECT feedback, COUNT(*) as n
                FROM size_feedback
                WHERE tenant=? AND product_url=? AND size_declared=?
                GROUP BY feedback
            """, (tenant, product_url, size_declared)).fetchall()
        counts = {r[0]: r[1] for r in rows}
        total = sum(counts.values())
        if total < 3:
            return 0
        apertado = counts.get("apertado", 0)
        largo = counts.get("largo", 0)
        if apertado > (total / 2):
            return +1
        if largo > (total / 2):
            return -1
    except Exception:
        pass
    return 0

@app.route("/size-recommendation", methods=["POST", "OPTIONS"])
def size_recommendation():
    if request.method == "OPTIONS":
        return ("", 204)
    origin = request.headers.get("Origin", "")
    if origin and not _origin_allowed(origin):
        return jsonify({"error": "origin nao autorizado"}), 403
    if not _check_rate_limit(_client_ip(), limit_per_min=60, limit_per_hour=600):
        return jsonify({"error": "rate limit"}), 429

    # aceita JSON ou form; sendBeacon manda text/plain com JSON
    body = request.get_json(silent=True)
    if body is None:
        try:
            body = json.loads(request.get_data(as_text=True) or "{}")
        except Exception:
            body = {}
    tenant = (body.get("tenant") or "martina")[:32]
    product_url = (body.get("product_url") or "").strip()[:500]
    product_name = (body.get("product_name") or "").strip()[:200]
    category = (body.get("category") or "upper_body")[:32]
    size_declared = (body.get("size_declared") or "").strip().upper()[:8]
    user_hash = (body.get("user_hash") or "")[:64]

    if not product_url:
        return jsonify({"error": "product_url obrigatorio"}), 400

    # Se nao mandou size_declared, tenta puxar do perfil
    if not size_declared and user_hash:
        try:
            with _db() as c:
                row = c.execute(
                    "SELECT size_top, size_bottom, size_dress FROM user_profiles WHERE tenant=? AND user_hash=?",
                    (tenant, user_hash)
                ).fetchone()
            if row:
                if category == "dresses":
                    size_declared = (row[2] or "").upper()
                elif category == "lower_body":
                    size_declared = (row[1] or "").upper()
                else:
                    size_declared = (row[0] or "").upper()
        except Exception:
            pass

    fit_type = _detect_fit_type(product_name)
    profile_status = "complete" if size_declared in _SIZE_GRADE else "empty"

    if profile_status == "empty":
        # Sem tamanho usual: sugere M (mediano) com baixa confianca. Nunca trava.
        base = "M"
        confidence = "low"
        reason = "sem seu tamanho usual, sugerimos o padrao da marca — nos diga seu tamanho pra afinar"
    else:
        # regular: 0. oversized: -1 (peca ja veste maior, tira 1). slim: +1 (veste menor, soma 1).
        delta_by_fit = {"regular": 0, "oversized": -1, "slim": +1}[fit_type]
        # feedback dinamico ajusta ainda mais
        delta_by_feedback = _apply_feedback_shift(tenant, product_url, size_declared)
        total_delta = delta_by_fit + delta_by_feedback
        base = _size_shift(size_declared, total_delta)
        confidence = "high" if delta_by_feedback != 0 else "medium"
        if fit_type == "oversized":
            reason = f"essa peca e oversized, entao tira 1 do seu {size_declared}"
        elif fit_type == "slim":
            reason = f"essa peca veste justa, entao soma 1 no seu {size_declared}"
        else:
            reason = f"caimento regular — sugerimos seu tamanho usual"
        if delta_by_feedback != 0:
            reason += " (ajustado por feedback de outras clientes)"

    # 3 caimentos: colado (-1), ideal (base), soltinho (+1)
    return jsonify({
        "size_ideal": base,
        "size_colado": _size_shift(base, -1),
        "size_soltinho": _size_shift(base, +1),
        "confidence": confidence,
        "reason": reason,
        "fit_type": fit_type,
        "profile_status": profile_status,
        "category": category,
    })

@app.route("/profile", methods=["GET", "POST", "OPTIONS"])
def user_profile():
    if request.method == "OPTIONS":
        return ("", 204)
    origin = request.headers.get("Origin", "")
    if origin and not _origin_allowed(origin):
        return jsonify({"error": "origin nao autorizado"}), 403
    tenant = (request.args.get("tenant") or "martina")[:32]
    user_hash = (request.args.get("user_hash") or "")[:64]
    if not user_hash:
        return jsonify({"error": "user_hash obrigatorio"}), 400

    if request.method == "GET":
        with _db() as c:
            row = c.execute(
                "SELECT size_top, size_bottom, size_dress, fit_pref FROM user_profiles WHERE tenant=? AND user_hash=?",
                (tenant, user_hash)
            ).fetchone()
        if not row:
            return jsonify({"exists": False})
        return jsonify({
            "exists": True,
            "size_top": row[0], "size_bottom": row[1], "size_dress": row[2], "fit_pref": row[3]
        })

    # POST: upsert
    if not _check_rate_limit(_client_ip(), limit_per_min=20, limit_per_hour=200):
        return jsonify({"error": "rate limit"}), 429
    body = request.get_json(silent=True)
    if body is None:
        try:
            body = json.loads(request.get_data(as_text=True) or "{}")
        except Exception:
            body = {}

    def _valid_size(s):
        return (s or "").strip().upper() if (s or "").strip().upper() in _SIZE_GRADE else None
    def _valid_fit(f):
        return f if f in ("colado", "ideal", "soltinho") else None

    with _DB_LOCK, _db() as c:
        c.execute("""
            INSERT INTO user_profiles (tenant, user_hash, size_top, size_bottom, size_dress, fit_pref, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant, user_hash) DO UPDATE SET
                size_top = COALESCE(excluded.size_top, size_top),
                size_bottom = COALESCE(excluded.size_bottom, size_bottom),
                size_dress = COALESCE(excluded.size_dress, size_dress),
                fit_pref = COALESCE(excluded.fit_pref, fit_pref),
                updated_at = excluded.updated_at
        """, (
            tenant, user_hash,
            _valid_size(body.get("size_top")),
            _valid_size(body.get("size_bottom")),
            _valid_size(body.get("size_dress")),
            _valid_fit(body.get("fit_pref")),
            time.time()
        ))
    return jsonify({"ok": True})

@app.route("/size-feedback", methods=["POST", "OPTIONS"])
def size_feedback():
    if request.method == "OPTIONS":
        return ("", 204)
    origin = request.headers.get("Origin", "")
    if origin and not _origin_allowed(origin):
        return jsonify({"error": "origin nao autorizado"}), 403
    if not _check_rate_limit(_client_ip(), limit_per_min=30, limit_per_hour=200):
        return jsonify({"error": "rate limit"}), 429
    body = request.get_json(silent=True)
    if body is None:
        try:
            body = json.loads(request.get_data(as_text=True) or "{}")
        except Exception:
            body = {}
    fb = (body.get("feedback") or "").strip().lower()
    if fb not in ("apertado", "ideal", "largo"):
        return jsonify({"error": "feedback invalido"}), 400
    with _DB_LOCK, _db() as c:
        c.execute("""
            INSERT INTO size_feedback (tenant, user_hash, product_url, product_name, garment_category,
                                       size_declared, size_suggested, size_tried, feedback, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            (body.get("tenant") or "martina")[:32],
            (body.get("user_hash") or "")[:64],
            (body.get("product_url") or "")[:500],
            (body.get("product_name") or "")[:200],
            (body.get("category") or "")[:32],
            (body.get("size_declared") or "").upper()[:8],
            (body.get("size_suggested") or "").upper()[:8],
            (body.get("size_tried") or "").upper()[:8],
            fb,
            time.time()
        ))
    return jsonify({"ok": True})

# ---------------------------------------------------------
# /widget.js — serve o widget pra Martina (e futuras lojas)
# Carregado pelo bootstrap nos Codigos Externos da Nuvemshop.
# ---------------------------------------------------------
_WIDGET_CACHE = {"text": None}
def _read_widget():
    if _WIDGET_CACHE["text"] is None:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(here, "widget.js"), "r", encoding="utf-8") as f:
                _WIDGET_CACHE["text"] = f.read()
        except Exception as e:
            _WIDGET_CACHE["text"] = "/* widget.js missing: " + str(e) + " */"
    return _WIDGET_CACHE["text"]

@app.route("/widget.js", methods=["GET"])
def widget_js():
    body = _read_widget()
    return Response(body, mimetype="application/javascript; charset=utf-8",
                    headers={
                        "Cache-Control": "public, max-age=300",
                        "Access-Control-Allow-Origin": "*",
                    })

# ---------------------------------------------------------
# /hotsale-price.js — script servido pra Nuvemshop (que rejeita inline).
# Substitui na listagem /sale/ o preco padrao pelo menor preco entre variantes.
# ---------------------------------------------------------
_HOTSALE_PRICE_JS = r"""/* HOTSALE — min preco na listagem + pre-selecionar variante mais barata na PDP */
(function(){
  var isSale = /\/sale\/?/i.test(location.pathname);
  var isPdp = /\/produtos\/[^\/?#]+\/?/.test(location.pathname);
  var qs = new URLSearchParams(location.search);
  var wantedSize = qs.get("mts_size");  // ?mts_size=P

  function runList(){
    var cs = document.querySelectorAll(".js-product-container[data-variants]");
    cs.forEach(function(c){
      if (c.dataset.mtsMinDone) return;
      try {
        var vs = JSON.parse(c.getAttribute("data-variants") || "[]");
        var av = vs.filter(function(v){ return v.available && v.price_number > 0; });
        if (!av.length) return;
        var eP = function(v){ return v.promotional_price_number || v.price_number; };
        var ch = av.reduce(function(a,b){ return eP(a) < eP(b) ? a : b; });
        var cp = eP(ch);
        // 1) substituir preco exibido pelo minimo
        var el = c.querySelector(".js-price-display.item-price");
        if (el) {
          var cur = parseFloat((el.textContent||"").replace(/[^0-9,]/g,"").replace(",","."));
          if (!isNaN(cur) && cp < cur - 0.01) {
            el.textContent = "R$" + cp.toFixed(2).replace(".",",");
          }
        }
        // 2) adicionar ?mts_size=<opt0> nos links do card (leva pra PDP ja com variante certa)
        if (ch.option0) {
          var links = c.querySelectorAll('a[href*="/produtos/"]');
          links.forEach(function(a){
            try {
              var u = new URL(a.href, location.origin);
              u.searchParams.set("mts_size", ch.option0);
              a.href = u.toString();
            } catch(e){}
          });
        }
        c.dataset.mtsMinDone = "1";
      } catch(e){}
    });
  }

  function runPdp(){
    if (!wantedSize) return;
    var s = document.querySelector("#variation_1, select[name='variation[0]']");
    if (!s) return;
    // opcao com value == wantedSize
    var opt = Array.prototype.find.call(s.options, function(o){ return o.value === wantedSize; });
    if (!opt) return;
    if (s.value !== wantedSize) {
      s.value = wantedSize;
      s.dispatchEvent(new Event("change", {bubbles:true}));
    }
  }

  function tick(){ if (isSale) runList(); if (isPdp) runPdp(); }
  [0, 300, 800, 1500, 3000, 6000].forEach(function(m){ setTimeout(tick, m); });
})();
"""

@app.route("/hotsale-price.js", methods=["GET"])
def hotsale_price_js():
    return Response(_HOTSALE_PRICE_JS, mimetype="application/javascript; charset=utf-8",
                    headers={
                        "Cache-Control": "public, max-age=300",
                        "Access-Control-Allow-Origin": "*",
                    })

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

# ---------------------------------------------------------
# /event — recebe evento do widget (sendBeacon)
# Permissivo no rate limit (evento é leve, alto volume esperado).
# Ignora silenciosamente se origin invalida ou tipo invalido — beacon nao
# precisa de feedback ao cliente (sem retry).
# ---------------------------------------------------------
@app.route("/event", methods=["POST", "OPTIONS"])
def post_event():
    if request.method == "OPTIONS":
        return ("", 204)
    origin = request.headers.get("Origin", "")
    if origin and not _origin_allowed(origin):
        return jsonify({"ok": False, "error": "origin"}), 204  # 204 pra beacon nao retentar
    if not _check_rate_limit(_client_ip(), limit_per_min=120, limit_per_hour=3000):
        return jsonify({"ok": False, "error": "rate"}), 204
    # sendBeacon pode mandar como text/plain — aceita ambos
    raw = request.get_data(as_text=True) or "{}"
    try:
        body = json.loads(raw)
    except Exception:
        return jsonify({"ok": False, "error": "json"}), 204
    ev = (body.get("event_type") or "").strip()
    if ev not in _VALID_EVENT_TYPES:
        return jsonify({"ok": False, "error": "type"}), 204
    tenant = (body.get("tenant") or "martina").strip().lower()
    # Sanitiza order_value (defesa contra valor forjado em magnitude absurda).
    # purchase_attributed pode ser inflado por adversario — log do IP fica pra audit.
    try:
        order_value = float(body.get("order_value") or 0)
        if order_value < 0 or order_value > 1_000_000:  # R$ 1M cap absoluto
            order_value = 0
    except Exception:
        order_value = 0
    meta = _meta_sanitize(body.get("meta"))
    order_id_raw = (body.get("order_id") or "")[:64]
    # IDEMPOTENCIA: purchase_attributed com mesmo order_id nao duplica.
    # Snippet pode disparar 2x (polling repete) — backend protege.
    if ev == "purchase_attributed" and order_id_raw:
        try:
            with _DB_LOCK, _db() as c:
                exists = c.execute(
                    "SELECT 1 FROM events WHERE tenant=? AND event_type='purchase_attributed' AND order_id=? LIMIT 1",
                    (tenant, order_id_raw)
                ).fetchone()
                if exists:
                    return jsonify({"ok": True, "dedup": True}), 200
        except Exception:
            pass  # se falhar o check, segue insercao normal (vale a perda eventual de idempotencia)
    try:
        with _DB_LOCK, _db() as c:
            c.execute("""
                INSERT INTO events (tenant, client_id, session_id, event_type,
                                    product_url, product_name, garment_category,
                                    order_id, order_value, ts, ip, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant,
                (body.get("client_id") or "")[:64],
                (body.get("session_id") or "")[:64],
                ev,
                (body.get("product_url") or "")[:500],
                (body.get("product_name") or "")[:200],
                (body.get("garment_category") or "")[:32],
                order_id_raw,
                order_value,
                time.time(),
                _client_ip(),
                json.dumps(meta)[:2000],
            ))
            # cleanup oportunistico (1% chance) — retencao 90d default
            if int(time.time()) % 100 == 0:
                c.execute("DELETE FROM events WHERE ts < ?", (time.time() - _DB_RETENTION_DAYS * 86400,))
        return jsonify({"ok": True}), 200
    except Exception:
        return jsonify({"ok": False, "error": "db"}), 204

# ---------------------------------------------------------
# /stats — agrega funil pra painel
# Filtros: tenant, from (YYYY-MM-DD), to (YYYY-MM-DD)
# ---------------------------------------------------------
def _parse_day(s, default_ts):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d"))
    except Exception:
        return default_ts

@app.route("/stats", methods=["GET"])
def get_stats():
    # Auth via ?key=<token>. Token validado contra sha256 hardcoded.
    if not _panel_authorized(request.args.get("key", "")):
        return jsonify({"error": "unauthorized"}), 401
    tenant = (request.args.get("tenant") or "martina").strip().lower()
    now = time.time()
    t_from = _parse_day(request.args.get("from", ""), now - 30 * 86400)
    t_to = _parse_day(request.args.get("to", ""), now) + 86400  # inclui dia inteiro
    with _DB_LOCK, _db() as c:
        # Contagens por tipo
        counts = {ev: 0 for ev in _VALID_EVENT_TYPES}
        for row in c.execute("""
            SELECT event_type, COUNT(*) FROM events
            WHERE tenant=? AND ts BETWEEN ? AND ?
            GROUP BY event_type
        """, (tenant, t_from, t_to)):
            counts[row[0]] = row[1]
        # Faturamento atribuido
        rev = c.execute("""
            SELECT COALESCE(SUM(order_value), 0) FROM events
            WHERE tenant=? AND event_type='purchase_attributed' AND ts BETWEEN ? AND ?
        """, (tenant, t_from, t_to)).fetchone()[0] or 0
        # Top produtos provados (event_type=tryon_complete)
        top_provados = [
            {"name": r[0], "n": r[1]}
            for r in c.execute("""
                SELECT product_name, COUNT(*) AS n FROM events
                WHERE tenant=? AND event_type='tryon_complete' AND ts BETWEEN ? AND ?
                  AND product_name <> ''
                GROUP BY product_name ORDER BY n DESC LIMIT 10
            """, (tenant, t_from, t_to))
        ]
        # Top produtos atribuidos
        top_atrib = [
            {"name": r[0], "n": r[1], "value": r[2] or 0}
            for r in c.execute("""
                SELECT product_name, COUNT(*) AS n, COALESCE(SUM(order_value), 0) AS v FROM events
                WHERE tenant=? AND event_type='purchase_attributed' AND ts BETWEEN ? AND ?
                  AND product_name <> ''
                GROUP BY product_name ORDER BY v DESC LIMIT 10
            """, (tenant, t_from, t_to))
        ]
    # Custo estimado OpenAI: completes x ~$0.011 (quality=low 1024x1024)
    cost_usd = round(counts["tryon_complete"] * 0.011, 2)
    # Taxas
    def pct(n, d):
        return round(100.0 * n / d, 1) if d > 0 else 0.0
    # Funnel relativo: cada etapa vs view inicial (drop-off)
    base = max(1, counts["tryon_view"])
    funnel = [
        {"label": "Viu botão",      "n": counts["tryon_view"],         "pct": 100.0},
        {"label": "Abriu modal",    "n": counts["tryon_open"],         "pct": pct(counts["tryon_open"], base)},
        {"label": "Provou (IA OK)", "n": counts["tryon_complete"],     "pct": pct(counts["tryon_complete"], base)},
        {"label": "Clicou comprar", "n": counts["tryon_buy_click"],    "pct": pct(counts["tryon_buy_click"], base)},
        {"label": "Comprou",        "n": counts["purchase_attributed"],"pct": pct(counts["purchase_attributed"], base)},
    ]
    return jsonify({
        "tenant": tenant,
        "from": time.strftime("%Y-%m-%d", time.localtime(t_from)),
        "to": time.strftime("%Y-%m-%d", time.localtime(t_to - 86400)),
        "counts": counts,
        "rates": {
            "ctr_btn": pct(counts["tryon_open"], counts["tryon_view"]),
            "taxa_prova": pct(counts["tryon_complete"], counts["tryon_open"]),
            "taxa_buy_click": pct(counts["tryon_buy_click"], counts["tryon_complete"]),
            "taxa_compra": pct(counts["purchase_attributed"], counts["tryon_complete"]),
        },
        "funnel": funnel,
        "revenue_brl": round(rev, 2),
        "cost_openai_usd": cost_usd,
        "top_provados": top_provados,
        "top_atribuidos": top_atrib,
    })

# ---------------------------------------------------------
# /panel — HTML do painel (estilo martina)
# ---------------------------------------------------------
PANEL_HTML = r"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>TryOn - Painel Martina</title>
<style>
:root{--bg:#fafafa;--fg:#111;--muted:#666;--line:#e5e5e5;--accent:#111;--ok:#059669;--err:#dc2626}
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px}
h1{font-weight:700;letter-spacing:.3em;margin:0 0 4px;text-align:center}
.sub{text-align:center;color:var(--muted);margin-bottom:24px;font-size:13px;letter-spacing:.18em;text-transform:uppercase}
.wrap{max-width:1100px;margin:0 auto}
.filters{display:flex;gap:12px;justify-content:center;margin-bottom:24px;flex-wrap:wrap}
.filters label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
.filters input,.filters button{padding:8px 12px;border:1px solid var(--line);border-radius:6px;font-size:13px;background:#fff}
.filters button{background:#111;color:#fff;border:0;cursor:pointer;letter-spacing:.1em;text-transform:uppercase;font-weight:600}
.row{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}
@media(max-width:900px){.row{grid-template-columns:repeat(2,1fr)}}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;text-align:center}
.kpi .label{font-size:10px;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;margin-bottom:8px}
.kpi .v{font-size:28px;font-weight:700;letter-spacing:.02em}
.kpi .sub2{font-size:11px;color:var(--muted);margin-top:4px}
.row2{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
@media(max-width:900px){.row2{grid-template-columns:repeat(2,1fr)}}
.taxa{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;text-align:center}
.taxa .label{font-size:10px;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;margin-bottom:6px}
.taxa .v{font-size:24px;font-weight:700;color:var(--ok)}
.tables{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.tables{grid-template-columns:1fr}}
.table{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}
.table h3{margin:0 0 12px;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:6px 4px;border-bottom:1px solid #f0f0f0}
td:last-child{text-align:right;font-variant-numeric:tabular-nums}
.financ{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:24px;text-align:center}
.financ .pair{display:inline-block;margin:0 16px}
.financ .label{font-size:10px;color:var(--muted);letter-spacing:.18em;text-transform:uppercase}
.financ .v{font-size:22px;font-weight:700}
.financ .v.ok{color:var(--ok)}
.financ .v.err{color:var(--err)}
.empty{text-align:center;color:#999;font-size:13px;padding:24px}
.foot{text-align:center;color:#999;font-size:11px;margin-top:24px;letter-spacing:.1em}
</style></head><body>
<div class="wrap">
  <h1>MARTINA</h1>
  <div class="sub">Painel TryOn - Conversão (mesma sessão)</div>

  <div class="filters">
    <label>De <input type="date" id="from"></label>
    <label>Ate <input type="date" id="to"></label>
    <button onclick="load()">Atualizar</button>
  </div>

  <div class="financ" id="financ">Carregando...</div>

  <div class="row">
    <div class="kpi"><div class="label">Views</div><div class="v" id="v_view">-</div><div class="sub2">botao visto na PDP</div></div>
    <div class="kpi"><div class="label">Aberturas</div><div class="v" id="v_open">-</div><div class="sub2">modal aberto</div></div>
    <div class="kpi"><div class="label">Provas OK</div><div class="v" id="v_complete">-</div><div class="sub2">IA gerou</div></div>
    <div class="kpi"><div class="label">Clique Comprar</div><div class="v" id="v_buy">-</div><div class="sub2">no card resultado</div></div>
    <div class="kpi"><div class="label">Compras</div><div class="v" id="v_purchase">-</div><div class="sub2">atribuidas (mesma sessão)</div></div>
  </div>

  <div class="row2">
    <div class="taxa"><div class="label">CTR Botão</div><div class="v" id="r_ctr">-</div></div>
    <div class="taxa"><div class="label">Taxa de Prova</div><div class="v" id="r_prova">-</div></div>
    <div class="taxa"><div class="label">Buy Click</div><div class="v" id="r_buy">-</div></div>
    <div class="taxa"><div class="label">Taxa Compra</div><div class="v" id="r_compra">-</div></div>
  </div>

  <div class="table" style="margin-bottom:24px">
    <h3>Funil completo (% sobre quem viu o botão)</h3>
    <div id="funnel"><div class="empty">Sem dados</div></div>
  </div>

  <div class="tables">
    <div class="table">
      <h3>Top produtos provados</h3>
      <div id="t_prov"><div class="empty">Sem dados</div></div>
    </div>
    <div class="table">
      <h3>Top produtos atribuidos (compra)</h3>
      <div id="t_atrib"><div class="empty">Sem dados</div></div>
    </div>
  </div>

  <div class="foot">v0 piloto Martina | refresh manual no botao acima</div>
</div>

<script>
const API = location.origin;
const TENANT = (new URLSearchParams(location.search).get('tenant') || 'martina').toLowerCase();
const TOKEN = "__TOKEN_INJECTED__"; // injetado server-side a partir do path /panel/<token>
document.querySelector('h1').textContent = TENANT.toUpperCase();
function fmtBRL(n){ return 'R$ ' + (n||0).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2}); }
function fmtUSD(n){ return 'US$ ' + (n||0).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}); }
function fmtN(n){ return (n||0).toLocaleString('pt-BR'); }

async function load(){
  const f = document.getElementById('from').value;
  const t = document.getElementById('to').value;
  const url = `${API}/stats?tenant=${encodeURIComponent(TENANT)}${f?'&from='+f:''}${t?'&to='+t:''}&key=${encodeURIComponent(TOKEN)}`;
  try {
    const r = await fetch(url, {cache:'no-store'});
    const d = await r.json();
    // KPIs
    document.getElementById('v_view').textContent = fmtN(d.counts.tryon_view);
    document.getElementById('v_open').textContent = fmtN(d.counts.tryon_open);
    document.getElementById('v_complete').textContent = fmtN(d.counts.tryon_complete);
    document.getElementById('v_buy').textContent = fmtN(d.counts.tryon_buy_click);
    document.getElementById('v_purchase').textContent = fmtN(d.counts.purchase_attributed);
    // Taxas
    document.getElementById('r_ctr').textContent = d.rates.ctr_btn + '%';
    document.getElementById('r_prova').textContent = d.rates.taxa_prova + '%';
    document.getElementById('r_buy').textContent = d.rates.taxa_buy_click + '%';
    document.getElementById('r_compra').textContent = d.rates.taxa_compra + '%';
    // Financeiro
    const lift = d.revenue_brl - (d.cost_openai_usd * 5.5); // ~BRL aproximado
    document.getElementById('financ').innerHTML = `
      <div class="pair"><div class="label">Faturamento atribuido</div><div class="v ok">${fmtBRL(d.revenue_brl)}</div></div>
      <div class="pair"><div class="label">Custo OpenAI</div><div class="v err">${fmtUSD(d.cost_openai_usd)}</div></div>
      <div class="pair"><div class="label">Resultado bruto (BRL)</div><div class="v ${lift>=0?'ok':'err'}">${fmtBRL(lift)}</div></div>
    `;
    // Funil (barras horizontais)
    document.getElementById('funnel').innerHTML = (d.funnel||[]).map(s=>{
      const w = Math.max(2, s.pct);
      return `<div style="display:flex;align-items:center;gap:12px;margin:8px 0">
        <div style="width:130px;font-size:12px;color:#666">${s.label}</div>
        <div style="flex:1;background:#f0f0f0;border-radius:4px;height:24px;position:relative;overflow:hidden">
          <div style="width:${w}%;background:#111;height:100%;transition:width .3s"></div>
        </div>
        <div style="width:140px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums">${fmtN(s.n)} <span style="color:#999">(${s.pct}%)</span></div>
      </div>`;
    }).join('') || '<div class="empty">Sem dados</div>';
    // Tabelas
    document.getElementById('t_prov').innerHTML = d.top_provados.length
      ? '<table>' + d.top_provados.map(r => `<tr><td>${r.name}</td><td>${fmtN(r.n)}</td></tr>`).join('') + '</table>'
      : '<div class="empty">Sem provas no periodo</div>';
    document.getElementById('t_atrib').innerHTML = d.top_atribuidos.length
      ? '<table>' + d.top_atribuidos.map(r => `<tr><td>${r.name}</td><td>${fmtBRL(r.value)}</td></tr>`).join('') + '</table>'
      : '<div class="empty">Sem compras atribuidas no periodo</div>';
  } catch(e) {
    document.getElementById('financ').innerHTML = '<div class="empty">Erro carregando: ' + e.message + '</div>';
  }
}

// Defaults: ultimos 30 dias
(function(){
  const t = new Date(), f = new Date(t.getTime() - 30*86400*1000);
  document.getElementById('from').value = f.toISOString().slice(0,10);
  document.getElementById('to').value = t.toISOString().slice(0,10);
  load();
})();
</script>
</body></html>
"""

@app.route("/panel/<token>", methods=["GET"])
def panel_page_token(token):
    # Capability URL: /panel/<token>. Token verificado vs sha256 hardcoded.
    if not _panel_authorized(token):
        # 404 generico — nao revela se path existe
        return Response("Not Found", status=404, mimetype="text/plain")
    # Injeta o token no HTML pra fetch /stats?key=<token> funcionar
    body = PANEL_HTML.replace("__TOKEN_INJECTED__", token)
    return Response(body, mimetype="text/html")

@app.route("/panel", methods=["GET"])
def panel_root():
    # /panel sem token devolve 404 genérico (nao revela existência do path)
    return Response("Not Found", status=404, mimetype="text/plain")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
