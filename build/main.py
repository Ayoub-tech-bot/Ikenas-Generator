import os
import json
import uuid
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import auth_db
import generate_local
import openedx_publish

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
APP_HTML_PATH = os.path.join(BUILD_DIR, "..", "app.html")
DEFAULT_BACKEND = "cli"
DEFAULT_MODEL = None
CHARS_PER_TOKEN_CODE = 3.2

jobs = {}
jobs_lock = threading.Lock()
publish_jobs = {}
publish_jobs_lock = threading.Lock()

AVAILABLE_MODELS = auth_db.AVAILABLE_MODELS if hasattr(auth_db, "AVAILABLE_MODELS") else [
    {"provider": "anthropic", "id": "claude-opus-4-5", "label": "Claude Opus 4.5"},
    {"provider": "anthropic", "id": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
    {"provider": "openai", "id": "gpt-4o", "label": "GPT-4o"},
    {"provider": "gemini", "id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
]

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise ou session expiree.")
    token = authorization.split("Bearer ")[1].strip()
    user = auth_db.get_session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentification requise ou session expiree.")
    return user

def require_admin(user = Depends(get_current_user)):
    if user["role"] not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Reserve aux administrateurs.")
    return user

def require_super_admin(user = Depends(get_current_user)):
    if user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Reserve au super-administrateur.")
    return user

@app.get("/", response_class=HTMLResponse)
@app.get("/app.html", response_class=HTMLResponse)
def serve_app_html():
    try:
        with open(APP_HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        raise HTTPException(status_code=500, detail="app.html introuvable sur le serveur")

@app.get("/api/ping")
def ping():
    return {"ok": True, "backend": DEFAULT_BACKEND}

@app.get("/api/auth/me")
def get_me(user=Depends(get_current_user)):
    data = auth_db._user_public(user)
    data["schoolName"] = auth_db.get_school_name(user["school_id"])
    return {
        "user": data,
        "usageToday": auth_db.get_usage_today(user["id"]),
        "usage7d": auth_db.get_usage_period(user["id"], 7),
        "usage30d": auth_db.get_usage_period(user["id"], 30),
    }

@app.get("/api/models")
def get_models(user=Depends(get_current_user)):
    return {"models": AVAILABLE_MODELS}

@app.get("/api/org-keys")
def get_org_keys(admin=Depends(require_admin)):
    school_id = admin["school_id"] if admin["role"] == "admin" else None
    return {"status": auth_db.org_key_status(school_id)}

@app.get("/api/schools")
def get_schools(super_admin=Depends(require_super_admin)):
    schools = auth_db.list_schools()
    for s in schools:
        s.update(auth_db.school_stats(s["id"]))
    return {"schools": schools}

@app.get("/api/users")
def get_users(admin=Depends(require_admin)):
    all_users = auth_db.list_users()
    if admin["role"] == "admin":
        all_users = [u for u in all_users if u["role"] == "professor" and u.get("schoolId") == admin["school_id"]]
    return {"users": all_users}

@app.get("/api/catalogue")
def get_catalogue(request: Request, user=Depends(get_current_user)):
    school_id_req = request.query_params.get("schoolId")
    school_id = auth_db.resolve_school_id(user, school_id_req)
    if school_id is None:
        raise HTTPException(status_code=400, detail="Choisissez une ecole.")
    return {"catalogue": auth_db.kv_get(f"catalogue:{school_id}", [])}

@app.get("/api/programme")
def get_programme(request: Request, user=Depends(get_current_user)):
    school_id_req = request.query_params.get("schoolId")
    school_id = auth_db.resolve_school_id(user, school_id_req)
    if school_id is None:
        raise HTTPException(status_code=400, detail="Choisissez une ecole.")
    programme = auth_db.kv_get(f"programme:{school_id}", [])
    if user["role"] == "professor":
        allowed = set(json.loads(user.get("niveaux_assignes", "[]")))
        programme = [r for r in programme if r.get("niveau") in allowed]
        programme = [r for r in programme if auth_db.check_sujet_allowed(user, r.get("sujet"))]
    return {"programme": programme}

@app.post("/api/auth/login")
async def login(request: Request):
    payload = await request.json()
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    row = auth_db.get_user_by_username(username)
    if row is None or not row.get("is_active") or not auth_db.verify_password(password, row["password_hash"], row["password_salt"]):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    token = auth_db.create_session(row["id"])
    data = auth_db._user_public(row)
    data["schoolName"] = auth_db.get_school_name(row["school_id"])
    return {
        "token": token,
        "user": data,
        "usageToday": auth_db.get_usage_today(row["id"]),
        "usage7d": auth_db.get_usage_period(row["id"], 7),
        "usage30d": auth_db.get_usage_period(row["id"], 30),
    }

@app.post("/api/auth/logout")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ")[1].strip()
        auth_db.delete_session(token)
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
