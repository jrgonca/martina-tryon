"""
Martina TryOn — backend Flask (substitui o Cloudflare Worker)
Roda no Render. Variavel obrigatoria: REPLICATE_API_TOKEN (formato r8_...)

Endpoints:
  GET  /                 healthcheck
  POST /tryon            cria prediction
  GET  /tryon/<id>       polling
"""
import os
import sys
import time
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

REPLICATE_MODEL_OWNER = "cuuupid"
REPLICATE_MODEL_NAME  = "idm-vton"

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

def _token():
    t = os.environ.get("REPLICATE_API_TOKEN", "")
    if not t:
        return None
    return t

def _hdr():
    return {
        "Authorization": f"Token {_token()}",
        "Content-Type": "application/json",
    }

# cache do version id por 1h
_version_cache = {"id": None, "ts": 0}
def get_latest_version_id():
    if _version_cache["id"] and time.time() - _version_cache["ts"] < 3600:
        return _version_cache["id"]
    r = requests.get(
        f"https://api.replicate.com/v1/models/{REPLICATE_MODEL_OWNER}/{REPLICATE_MODEL_NAME}",
        headers=_hdr(),
        timeout=20,
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
    if not person or not garment:
        return jsonify({"error": "person_image e garment_image_url são obrigatórios"}), 400

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
                    "category": "upper_body",
                    "crop": False,
                    "force_dc": False,
                    "mask_only": False,
                },
            },
            timeout=30,
        )
        data = r.json()
        if not r.ok:
            return jsonify({"error": f"replicate {r.status_code}", "detail": data}), 500
        return jsonify({
            "id": data["id"],
            "status": data["status"],
            "poll_url": f"{request.host_url.rstrip('/')}/tryon/{data['id']}",
        }), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tryon/<pid>", methods=["GET"])
def tryon_status(pid):
    if not _token():
        return jsonify({"error": "REPLICATE_API_TOKEN not configured"}), 500
    try:
        r = requests.get(
            f"https://api.replicate.com/v1/predictions/{pid}",
            headers=_hdr(),
            timeout=20,
        )
        d = r.json()
        if not r.ok:
            return jsonify({"error": f"replicate {r.status_code}", "detail": d}), 500
        out = d.get("output")
        if isinstance(out, list) and out:
            out = out[0]
        return jsonify({
            "id": d["id"],
            "status": d["status"],
            "output": out,
            "error": d.get("error"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
