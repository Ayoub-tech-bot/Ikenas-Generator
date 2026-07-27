#!/usr/bin/env python3
"""
Serveur de generation, comptes et donnees partagees pour app.html.

Ce serveur est desormais la source de verite pour :
  - les comptes (super_admin / admin / professor), l'authentification et les quotas
  - le catalogue de modeles HTML (lecture pour tous les connectes, ecriture admin+)
  - le programme scolaire et les exercices generes (lecture filtree par niveaux
    assignes pour un professeur, ecriture limitee a ses niveaux ; admin+ illimite)
  - la generation elle-meme (inchangee : CLI claude local ou API directe)

Fournisseurs de generation supportes (transmis par app.html dans chaque requete) :
  cli        : CLI `claude` local (pas de cle API necessaire)
  anthropic  : API Anthropic  — necessite ANTHROPIC_API_KEY ou cle dans la requete
  openai     : API OpenAI     — necessite OPENAI_API_KEY ou cle dans la requete
  gemini     : API Google Gemini — necessite GOOGLE_API_KEY ou cle dans la requete
  mistral    : API Mistral    — necessite MISTRAL_API_KEY ou cle dans la requete
  grok       : API xAI Grok   — necessite XAI_API_KEY ou cle dans la requete
  kimi       : API Moonshot AI Kimi — necessite MOONSHOT_API_KEY ou cle dans la requete

Usage :
  python build/server.py                     # port 8765, ecoute sur toutes les interfaces
  python build/server.py --backend anthropic
  python build/server.py --host 127.0.0.1    # restreindre a cette seule machine
"""
import argparse
import gzip
import json
import os
import queue
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import auth_db
import generate_local
import openedx_publish

jobs = {}
jobs_lock = threading.Lock()
job_queue = queue.Queue()

# Variable d'environnement de repli pour chaque fournisseur, quand ni la requete ni une
# cle "ecole"/"plateforme" (auth_db.get_org_key) n'en fournit une. Utilise a la fois par
# process_job (generation texte) et par le handler /api/illustrate (image Grok).
PROVIDER_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "gemini":    "GOOGLE_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "grok":      "XAI_API_KEY",
    "kimi":      "MOONSHOT_API_KEY",
}


def _resolve_api_key(provider, api_key, school_id):
    """Chaine de resolution partagee : cle envoyee par le client (perso) -> cle de l'ecole
    du professeur -> cle plateforme (super-admin, school_id=None) -> variable d'environnement."""
    if api_key:
        return api_key
    api_key = auth_db.get_org_key(provider, school_id)
    if api_key:
        return api_key
    api_key = auth_db.get_org_key(provider, None)
    if api_key:
        return api_key
    return os.environ.get(PROVIDER_ENV_VARS.get(provider, ""), "")

# File d'attente separee pour la publication directe Open edX (voir openedx_publish.py) :
# operation lente (plusieurs dizaines de requetes HTTP sequentielles vers Studio) et
# independante de la generation IA, elle ne doit ni bloquer ni etre bloquee par process_job.
publish_jobs = {}
publish_jobs_lock = threading.Lock()
publish_job_queue = queue.Queue()

DEFAULT_BACKEND = "cli"
DEFAULT_MODEL = None

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
APP_HTML_PATH = os.path.join(BUILD_DIR, "..", "app.html")

# ~ chars per token, calibrated the same way as the in-app estimator (app.html:
# CHARS_PER_TOKEN_PROSE / CHARS_PER_TOKEN_CODE) — used here only to check a token quota
# *before* spending real tokens; the CLI backend's own reported cost is authoritative after.
CHARS_PER_TOKEN_CODE = 3.2


# All available models surfaced to the app
AVAILABLE_MODELS = [
    # Anthropic
    {"provider": "anthropic", "id": "claude-opus-4-5",      "label": "Claude Opus 4.5"},
    {"provider": "anthropic", "id": "claude-sonnet-4-5",    "label": "Claude Sonnet 4.5"},
    {"provider": "anthropic", "id": "claude-haiku-4-5",     "label": "Claude Haiku 4.5"},
    # OpenAI
    {"provider": "openai",    "id": "gpt-4o",               "label": "GPT-4o"},
    {"provider": "openai",    "id": "gpt-4o-mini",          "label": "GPT-4o mini"},
    {"provider": "openai",    "id": "gpt-4.1",              "label": "GPT-4.1"},
    {"provider": "openai",    "id": "o3-mini",              "label": "o3-mini"},
    # Google Gemini
    {"provider": "gemini",    "id": "gemini-3.6-flash",      "label": "Gemini 3.6 Flash"},
    {"provider": "gemini",    "id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash Lite"},
    {"provider": "gemini",    "id": "gemini-2.5-flash",      "label": "Gemini 2.5 Flash"},
    {"provider": "gemini",    "id": "gemini-2.5-pro",        "label": "Gemini 2.5 Pro"},
    {"provider": "gemini",    "id": "gemini-2.0-flash",      "label": "Gemini 2.0 Flash"},
    # Mistral
    {"provider": "mistral",   "id": "mistral-large-latest", "label": "Mistral Large"},
    {"provider": "mistral",   "id": "mistral-small-latest", "label": "Mistral Small"},
    {"provider": "mistral",   "id": "codestral-latest",     "label": "Codestral"},
    {"provider": "mistral",   "id": "devstral-small-latest","label": "Devstral Small"},
    # xAI Grok
    {"provider": "grok",      "id": "grok-4.3",             "label": "Grok 4.3"},
    {"provider": "grok",      "id": "grok-4-1-fast",        "label": "Grok 4.1 Fast"},
    # Moonshot AI Kimi
    {"provider": "kimi",      "id": "kimi-k2.5",            "label": "Kimi K2.5"},
    {"provider": "kimi",      "id": "moonshot-v1-8k",       "label": "Moonshot v1 8k"},
    {"provider": "kimi",      "id": "moonshot-v1-32k",      "label": "Moonshot v1 32k"},
]


# ---------------------------------------------------------------------------
# Seed data : extrait le PLAN embarque dans app.html au tout premier demarrage
# ---------------------------------------------------------------------------

def _extract_json_object(text, marker):
    start = text.find(marker)
    if start == -1:
        return None
    i = start + len(marker)
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    in_string = False
    obj_start = i
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    return json.loads(text[obj_start:i])


def seed_from_embedded_plan():
    """Seed the OLDEST school's programme/catalogue from app.html's embedded PLAN, only if
    that school doesn't already have this data (first run of an existing single-school
    deployment, migrated to a default school by auth_db.init_db()). Uses get_oldest_school_id()
    (creation order), NOT list_schools() which is display-ordered by name — a differently-named
    newer school must never be mistaken for the legacy default one. A brand new install with
    zero schools yet starts empty — a school only gets data once created for real via
    l'ecran Ecoles ; on ne force plus le contenu Salma dans toute nouvelle ecole."""
    school_id = auth_db.get_oldest_school_id()
    if school_id is None:
        return
    prog_key, cat_key = f"programme:{school_id}", f"catalogue:{school_id}"
    if auth_db.kv_get(prog_key) is not None and auth_db.kv_get(cat_key) is not None:
        return
    try:
        with open(APP_HTML_PATH, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        print(f"[server] Avertissement : impossible de lire app.html pour l'amorçage ({e}). "
              f"Le programme et le catalogue demarrent vides.")
        auth_db.kv_set(prog_key, auth_db.kv_get(prog_key) or [])
        auth_db.kv_set(cat_key, auth_db.kv_get(cat_key) or [])
        return
    plan = _extract_json_object(html, "const PLAN = ")
    if plan is None:
        print("[server] Avertissement : PLAN introuvable dans app.html, demarrage a vide.")
        plan = {"programme": [], "catalogue": []}
    if auth_db.kv_get(prog_key) is None:
        auth_db.kv_set(prog_key, plan.get("programme", []))
    if auth_db.kv_get(cat_key) is None:
        auth_db.kv_set(cat_key, plan.get("catalogue", []))
    print(f"[server] Donnees initiales chargees depuis app.html : "
          f"{len(plan.get('programme', []))} lignes de programme, "
          f"{len(plan.get('catalogue', []))} categories de modeles.")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _bearer_token(handler):
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


def _current_user(handler):
    return auth_db.get_session_user(_bearer_token(handler))


def _user_json(row):
    data = auth_db._user_public(row)  # noqa: SLF001 (internal helper reused intentionally)
    data["schoolName"] = auth_db.get_school_name(row["school_id"])
    return data


def _is_admin_or_above(user_row):
    return user_row is not None and user_row["role"] in ("admin", "super_admin")


def _estimate_tokens(prompt_len_chars, expected_output_chars):
    return int((prompt_len_chars + expected_output_chars) / CHARS_PER_TOKEN_CODE)


# ---------------------------------------------------------------------------
# Generation job processing (unchanged core, now records into shared programme + usage)
# ---------------------------------------------------------------------------

def _update_programme_exercice(school_id, row_id, ex_type, ex_variante, mutate_fn):
    """Read-modify-write a single exercice inside the school's programme, identified by
    (rowId, type, variante). mutate_fn(ex_dict) mutates it in place. Returns the matched
    programme row (for logging context), or None if not found."""
    prog_key = f"programme:{school_id}"
    programme = auth_db.kv_get(prog_key, [])
    found_row = None
    for row in programme:
        if row.get("id") != row_id:
            continue
        for ex in row.get("exercices", []):
            if ex.get("type") == ex_type and ex.get("variante") == ex_variante:
                mutate_fn(ex)
                found_row = row
    if found_row:
        auth_db.kv_set(prog_key, programme)
    return found_row


def process_job(job_id):
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = "running"
        prompt    = job["prompt"]
        provider  = job.get("provider") or DEFAULT_BACKEND
        api_key   = job.get("api_key")
        model     = job.get("model")
        school_id = job.get("school_id")

    try:
        if provider == "cli":
            # Filet de securite : d'anciens clients ont pu enregistrer le placeholder "cli" (id
            # du seul item de la liste de modeles pour ce moteur cote app.html) comme si c'etait
            # un vrai nom de modele -> "claude --model cli" echoue avec code 1. On l'ignore ici
            # comme s'il n'y avait pas de modele choisi (comportement voulu : le CLI utilise son
            # modele par defaut).
            cli_model = model if model and model != "cli" else None
            html, cost = generate_local.call_claude_cli(prompt, model=cli_model or DEFAULT_MODEL)
        else:
            api_key = _resolve_api_key(provider, api_key, school_id)
            if not api_key:
                raise RuntimeError(f"Aucune cle API fournie pour le fournisseur '{provider}'.")

            resolved_model = model or generate_local.DEFAULT_MODELS.get(provider)
            html, cost = generate_local.call_provider(provider, api_key, prompt, resolved_model)

        if not html.startswith("<!DOCTYPE") and not html.startswith("<html"):
            raise RuntimeError("La reponse ne commence pas par <!DOCTYPE ou <html.")

        with jobs_lock:
            jobs[job_id].update(status="done", html_b64=generate_local.utf8_to_b64(html), cost=cost, error=None)

        row_id, ex_type, ex_variante, kind = job["row_id"], job["ex_type"], job["ex_variante"], job["kind"]
        html_b64 = generate_local.utf8_to_b64(html)

        # kind == "print" : transformation "fiche imprimable" pour l'export PDF (voir
        # buildPrintExportHtml cote app.html). rowId/exType/exVariante sont bien fournis par le
        # client pour que les verifications de droits/quota ci-dessus s'appliquent normalement,
        # mais ce resultat ne doit JAMAIS ecraser le contenu interactif canonique de l'exercice
        # -- on saute donc entierement la persistance programme pour ce kind.
        if kind != "print":
            gen_date = __import__("datetime").datetime.now().strftime("%Y-%m-%d")

            def mutate(ex):
                if kind == "modification" and ex.get("contenuB64"):
                    ex.setdefault("historique", []).append({"contenuB64": ex["contenuB64"], "genereLe": ex.get("genereLe")})
                ex["contenuB64"] = html_b64
                ex["statut"] = "genere"
                ex["genereLe"] = gen_date
                ex["demandeModification"] = ""

            row = _update_programme_exercice(school_id, row_id, ex_type, ex_variante, mutate)
            if row:
                auth_db.log_generation_event(job["user_id"], school_id, row, ex_type, ex_variante, kind)

        estimated_tokens = job.get("estimated_tokens", 0)
        auth_db.increment_usage(job["user_id"], tokens=estimated_tokens, exercises=1)

    except Exception as e:
        with jobs_lock:
            jobs[job_id].update(status="error", error=str(e))


def worker_loop():
    while True:
        job_id = job_queue.get()
        try:
            process_job(job_id)
        finally:
            job_queue.task_done()


# ---------------------------------------------------------------------------
# Publication directe Open edX (voir openedx_publish.py pour le detail du protocole)
# ---------------------------------------------------------------------------

def process_publish_job(job_id):
    with publish_jobs_lock:
        job = publish_jobs[job_id]
        job["status"] = "running"
        school_id = job["school_id"]
        opts = job["opts"]

    def log(msg):
        with publish_jobs_lock:
            publish_jobs[job_id]["log"].append(msg)

    try:
        programme = auth_db.kv_get(f"programme:{school_id}", [])
        catalogue = auth_db.kv_get(f"catalogue:{school_id}", [])
        rows = [
            r for r in programme
            if r.get("niveau") == opts["niveau"]
            and (not opts.get("matiere") or (r.get("matiere") or "Français") == opts["matiere"])
            and (not opts.get("unite") or r.get("unite") == opts["unite"])
            and (not opts.get("section") or r.get("section") == opts["section"])
        ]
        result = openedx_publish.publish_course(rows, catalogue, opts, progress_cb=log)
        with publish_jobs_lock:
            publish_jobs[job_id].update(status="done", result=result, error=None)
    except openedx_publish.PublishError as e:
        log(f"Echec : {e}")
        with publish_jobs_lock:
            publish_jobs[job_id].update(status="error", error=str(e))
    except Exception as e:
        log(f"Erreur inattendue : {e}")
        with publish_jobs_lock:
            publish_jobs[job_id].update(status="error", error=str(e))


def publish_worker_loop():
    while True:
        job_id = publish_job_queue.get()
        try:
            process_publish_job(job_id)
        finally:
            publish_job_queue.task_done()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0, pas 1.1 : force la fermeture de la connexion apres chaque reponse au lieu de la
    # garder ouverte (keep-alive). Observe en pratique : sur cet hote Windows, reutiliser une
    # connexion garde-en-vie apres une grosse reponse (ex. le chargement d'app.html, ~350 Ko)
    # peut corrompre le cadrage de la requete suivante sur la meme connexion — le serveur lit
    # alors des octets residuels comme une ligne de requete, avec une methode HTTP illisible,
    # d'ou une erreur 501 "Unsupported method" cote client (observe avec le nouvel import de
    # bibliotheque, dont le corps de requete est nettement plus gros que les autres endpoints).
    # Fermer systematiquement la connexion evite toute cette classe de bug ; le cout (une
    # nouvelle poignee de main TCP par requete) est negligeable a cette echelle d'usage.
    protocol_version = "HTTP/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _write_body(self, code, content_type, body):
        """Sends `body` (bytes), gzip-compressed above a small threshold when the client
        accepts it. Large uncompressed bodies (several hundred KB+) were observed to be
        silently truncated by something in the local network stack on Windows — gzip keeps
        every real response comfortably under that, and fetch() decompresses it transparently."""
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        encoding = None
        if accepts_gzip and len(body) > 800:
            body = gzip.compress(body, compresslevel=6)
            encoding = "gzip"
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", content_type)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # Written in small chunks with a flush after each: large single writes were observed
        # to stall indefinitely partway through on this host (see docstring above) — chunking
        # sidesteps whatever is buffering/inspecting the stream.
        chunk_size = 8192
        for offset in range(0, len(body), chunk_size):
            self.wfile.write(body[offset:offset + chunk_size])
            self.wfile.flush()

    def _json(self, code, payload):
        self._write_body(code, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw)

    def _require_auth(self):
        user = _current_user(self)
        if user is None:
            self._json(401, {"error": "Authentification requise ou session expiree."})
            return None
        return user

    def _require_admin(self):
        user = self._require_auth()
        if user is None:
            return None
        if not _is_admin_or_above(user):
            self._json(403, {"error": "Reserve aux administrateurs."})
            return None
        return user

    def _require_super_admin(self):
        user = self._require_auth()
        if user is None:
            return None
        if user["role"] != "super_admin":
            self._json(403, {"error": "Reserve au super-administrateur."})
            return None
        return user

    def _require_school_id(self, user, requested_school_id=None):
        """admin/professor : toujours leur propre ecole (requested_school_id ignore).
        super_admin : doit fournir requested_school_id explicitement -> 400 clair sinon."""
        school_id = auth_db.resolve_school_id(user, requested_school_id)
        if school_id is None:
            self._json(400, {"error": "Choisissez une ecole (schoolId requis pour un super-admin)."})
            return None
        return school_id

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/app.html"):
            self._serve_app_html()
            return

        if path == "/api/ping":
            self._json(200, {"ok": True, "backend": DEFAULT_BACKEND})
            return

        if path == "/api/auth/me":
            user = self._require_auth()
            if user is None:
                return
            self._json(200, {
                "user": _user_json(user),
                "usageToday": auth_db.get_usage_today(user["id"]),
                "usage7d": auth_db.get_usage_period(user["id"], 7),
                "usage30d": auth_db.get_usage_period(user["id"], 30),
            })
            return

        if path == "/api/models":
            if self._require_auth() is None:
                return
            self._json(200, {"models": AVAILABLE_MODELS})
            return

        if path == "/api/org-keys":
            user = self._require_admin()
            if user is None:
                return
            # admin : cles de sa propre ecole. super_admin : cles plateforme (fallback global).
            school_id = user["school_id"] if user["role"] == "admin" else None
            self._json(200, {"status": auth_db.org_key_status(school_id)})
            return

        if path == "/api/schools":
            user = self._require_super_admin()
            if user is None:
                return
            schools = auth_db.list_schools()
            for s in schools:
                s.update(auth_db.school_stats(s["id"]))
            self._json(200, {"schools": schools})
            return

        if path == "/api/users":
            user = self._require_admin()
            if user is None:
                return
            all_users = auth_db.list_users()
            if user["role"] == "admin":
                all_users = [u for u in all_users if u["role"] == "professor" and u["schoolId"] == user["school_id"]]
            self._json(200, {"users": all_users})
            return

        if path == "/api/activity":
            user = self._require_admin()
            if user is None:
                return
            qs = parse_qs(parsed.query)
            period = (qs.get("period") or ["week"])[0]
            user_id = (qs.get("userId") or [None])[0]
            user_id = int(user_id) if user_id else None
            if user["role"] == "admin":
                school_id = user["school_id"]
            else:
                raw = (qs.get("schoolId") or [None])[0]
                school_id = int(raw) if raw else None
            self._json(200, {
                "totals": auth_db.activity_totals(period, school_id),
                "breakdown": auth_db.activity_breakdown(period, user_id, school_id),
            })
            return

        if path == "/api/activity/log":
            user = self._require_admin()
            if user is None:
                return
            qs = parse_qs(parsed.query)
            period = (qs.get("period") or ["week"])[0]
            user_id = (qs.get("userId") or [None])[0]
            user_id = int(user_id) if user_id else None
            if user["role"] == "admin":
                school_id = user["school_id"]
            else:
                raw = (qs.get("schoolId") or [None])[0]
                school_id = int(raw) if raw else None
            limit = int((qs.get("limit") or ["100"])[0])
            offset = int((qs.get("offset") or ["0"])[0])
            events, has_more = auth_db.activity_log(period, user_id, school_id, limit, offset)
            self._json(200, {"events": events, "hasMore": has_more})
            return

        if path == "/api/catalogue":
            user = self._require_auth()
            if user is None:
                return
            qs = parse_qs(parsed.query)
            school_id = self._require_school_id(user, (qs.get("schoolId") or [None])[0])
            if school_id is None:
                return
            self._json(200, {"catalogue": auth_db.kv_get(f"catalogue:{school_id}", [])})
            return

        if path == "/api/programme":
            user = self._require_auth()
            if user is None:
                return
            qs = parse_qs(parsed.query)
            school_id = self._require_school_id(user, (qs.get("schoolId") or [None])[0])
            if school_id is None:
                return
            programme = auth_db.kv_get(f"programme:{school_id}", [])
            if user["role"] == "professor":
                allowed = set(json.loads(user["niveaux_assignes"]))
                programme = [r for r in programme if r.get("niveau") in allowed]
                programme = [r for r in programme if auth_db.check_sujet_allowed(user, r.get("sujet"))]
            self._json(200, {"programme": programme})
            return

        if path == "/api/generate/status":
            user = self._require_auth()
            if user is None:
                return
            job_id = (parse_qs(parsed.query).get("jobId") or [None])[0]
            with jobs_lock:
                job = jobs.get(job_id)
                if not job:
                    self._json(404, {"error": "job inconnu"})
                    return
                if job["user_id"] != user["id"] and not _is_admin_or_above(user):
                    self._json(403, {"error": "Ce job appartient a un autre utilisateur."})
                    return
                view = {"id": job_id, "status": job["status"], "cost": job.get("cost"), "error": job.get("error")}
                if job["status"] == "done":
                    view["html_b64"] = job["html_b64"]
            self._json(200, view)
            return

        if path == "/api/publish-course/status":
            admin = self._require_admin()
            if admin is None:
                return
            job_id = (parse_qs(parsed.query).get("jobId") or [None])[0]
            with publish_jobs_lock:
                job = publish_jobs.get(job_id)
                if not job:
                    self._json(404, {"error": "job inconnu"})
                    return
                view = {
                    "id": job_id, "status": job["status"], "log": list(job["log"]),
                    "result": job.get("result"), "error": job.get("error"),
                }
            self._json(200, view)
            return

        if path == "/api/vault/items":
            user = self._require_auth()
            if user is None:
                return
            qs = parse_qs(parsed.query)
            school_id = self._require_school_id(user, (qs.get("schoolId") or [None])[0])
            if school_id is None:
                return
            niveau = (qs.get("niveau") or [None])[0]
            sujet = (qs.get("sujet") or [None])[0]
            sort = (qs.get("sort") or ["recent"])[0]
            items = auth_db.list_vault_items(school_id, niveau=niveau, sujet=sujet, sort=sort, viewer_user_id=user["id"])
            self._json(200, {"items": items})
            return

        if path == "/api/vault/saves":
            user = self._require_auth()
            if user is None:
                return
            self._json(200, {"items": auth_db.list_my_vault_saves(user["id"])})
            return

        m = re.match(r"^/api/vault/items/(\d+)$", path)
        if m:
            user = self._require_auth()
            if user is None:
                return
            item = auth_db.get_vault_item(int(m.group(1)), viewer_user_id=user["id"])
            if item is None:
                self._json(404, {"error": "exercice introuvable dans le coffre"})
                return
            resolved = auth_db.resolve_school_id(user)
            if resolved is not None and item["schoolId"] != resolved:
                self._json(403, {"error": "Cet exercice n'appartient pas a votre ecole."})
                return
            self._json(200, {"item": item})
            return

        self._json(404, {"error": "route inconnue"})

    def _serve_app_html(self):
        try:
            with open(APP_HTML_PATH, "rb") as f:
                body = f.read()
        except OSError:
            self._json(500, {"error": "app.html introuvable sur le serveur"})
            return
        self._write_body(200, "text/html; charset=utf-8", body)

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        path = self.path

        if path == "/api/auth/login":
            try:
                payload = self._read_json_body()
                username = (payload.get("username") or "").strip()
                password = payload.get("password") or ""
            except Exception as e:
                self._json(400, {"error": f"payload invalide : {e}"})
                return
            row = auth_db.get_user_by_username(username)
            if row is None or not row["is_active"] or not auth_db.verify_password(password, row["password_hash"], row["password_salt"]):
                self._json(401, {"error": "Identifiants incorrects."})
                return
            token = auth_db.create_session(row["id"])
            self._json(200, {
                "token": token, "user": _user_json(row),
                "usageToday": auth_db.get_usage_today(row["id"]),
                "usage7d": auth_db.get_usage_period(row["id"], 7),
                "usage30d": auth_db.get_usage_period(row["id"], 30),
            })
            return

        if path == "/api/auth/logout":
            token = _bearer_token(self)
            if token:
                auth_db.delete_session(token)
            self._json(200, {"ok": True})
            return

        if path == "/api/users":
            admin = self._require_admin()
            if admin is None:
                return
            if admin["role"] == "super_admin":
                self._json(400, {"error": "Utilisez /api/schools pour creer un compte admin (et son ecole). "
                                           "Cet endpoint cree uniquement des professeurs, au sein d'une ecole existante."})
                return
            try:
                payload = self._read_json_body()
                user = auth_db.create_user(
                    username=payload.get("username", ""),
                    password=payload.get("password", ""),
                    role="professor",
                    niveaux_assignes=payload.get("niveauxAssignes") or [],
                    quota_daily=payload.get("quotaDaily"),
                    quota_weekly=payload.get("quotaWeekly"),
                    quota_monthly=payload.get("quotaMonthly"),
                    school_id=admin["school_id"],
                    created_by=admin["id"],
                    allowed_types=payload.get("allowedTypes"),
                    allowed_sujets=payload.get("allowedSujets"),
                    allowed_providers=payload.get("allowedProviders"),
                    can_use_own_key=payload.get("canUseOwnKey", True),
                    permissions=payload.get("permissions"),
                )
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"user": user})
            return

        if path == "/api/schools":
            user = self._require_super_admin()
            if user is None:
                return
            payload = self._read_json_body()
            try:
                result = auth_db.create_school(
                    name=payload.get("schoolName", ""),
                    admin_username=payload.get("adminUsername", ""),
                    admin_password=payload.get("adminPassword", ""),
                    created_by=user["id"],
                )
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"school": result})
            return

        if path == "/api/org-keys":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            provider = payload.get("provider")
            if provider not in auth_db.ORG_KEY_PROVIDERS:
                self._json(400, {"error": "fournisseur invalide"})
                return
            school_id = admin["school_id"] if admin["role"] == "admin" else None
            try:
                auth_db.set_org_key(provider, school_id, (payload.get("apiKey") or "").strip(), admin["id"])
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"status": auth_db.org_key_status(school_id)})
            return

        if path == "/api/catalogue/categories":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            label = (payload.get("label") or "").strip()
            if not label:
                self._json(400, {"error": "Nom de categorie requis."})
                return
            kind = payload.get("kind") or "exercice"
            cat_key_store = f"catalogue:{school_id}"
            catalogue = auth_db.kv_get(cat_key_store, [])
            base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "categorie"
            existing_keys = {c["key"] for c in catalogue}
            key, suffix = base, 2
            while key in existing_keys:
                key = f"{base}-{suffix}"
                suffix += 1
            new_cat = {"key": key, "label": label, "kind": kind, "variantes": []}
            catalogue.append(new_cat)
            auth_db.kv_set(cat_key_store, catalogue)
            self._json(200, {"catalogue": catalogue, "newKey": key})
            return

        if path == "/api/catalogue/variantes":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            cat_key_store = f"catalogue:{school_id}"
            cat_key = payload.get("catKey")
            variantes = payload.get("variantes") or []
            catalogue = auth_db.kv_get(cat_key_store, [])
            cat = next((c for c in catalogue if c["key"] == cat_key), None)
            if cat is None:
                self._json(404, {"error": "categorie inconnue"})
                return
            existing_files = {v["fichier"] for v in cat["variantes"]}
            for v in variantes:
                fichier = v.get("fichier")
                if fichier in existing_files:
                    self._json(409, {"error": f"le fichier {fichier} existe deja dans cette categorie"})
                    return
                existing_files.add(fichier)
            cat["variantes"].extend(variantes)
            auth_db.kv_set(cat_key_store, catalogue)
            self._json(200, {"catalogue": catalogue})
            return

        if path == "/api/catalogue/delete-variante":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            cat_key_store = f"catalogue:{school_id}"
            catalogue = auth_db.kv_get(cat_key_store, [])
            cat = next((c for c in catalogue if c["key"] == payload.get("catKey")), None)
            if cat is not None:
                cat["variantes"] = [v for v in cat["variantes"] if v["fichier"] != payload.get("fichier")]
                if not cat["variantes"]:
                    catalogue = [c for c in catalogue if c is not cat]
                auth_db.kv_set(cat_key_store, catalogue)
            self._json(200, {"catalogue": catalogue})
            return

        if path == "/api/catalogue/delete-categorie":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            cat_key_store = f"catalogue:{school_id}"
            catalogue = [c for c in auth_db.kv_get(cat_key_store, []) if c["key"] != payload.get("catKey")]
            auth_db.kv_set(cat_key_store, catalogue)
            self._json(200, {"catalogue": catalogue})
            return

        if path == "/api/programme/replace":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            new_programme = payload.get("programme")
            if not isinstance(new_programme, list):
                self._json(400, {"error": "programme invalide"})
                return
            auth_db.kv_set(f"programme:{school_id}", new_programme)
            self._json(200, {"programme": new_programme})
            return

        if path == "/api/programme/import":
            user = self._require_auth()
            if user is None:
                return
            if not auth_db.check_permission(user, "importProgramme"):
                self._json(403, {"error": "L'import du programme ne vous est pas autorise."})
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(user, payload.get("schoolId"))
            if school_id is None:
                return
            incoming = payload.get("rows") or []
            if not isinstance(incoming, list):
                self._json(400, {"error": "rows invalide"})
                return
            allowed = None if user["role"] != "professor" else set(json.loads(user["niveaux_assignes"]))
            accepted, skipped = [], []
            for row in incoming:
                if not isinstance(row, dict) or not row.get("id") or not row.get("niveau") or not row.get("titre"):
                    skipped.append({"id": row.get("id") if isinstance(row, dict) else None, "error": "ligne invalide"})
                    continue
                if allowed is not None and row.get("niveau") not in allowed:
                    skipped.append({"id": row["id"], "titre": row.get("titre"), "error": "niveau non assigne"})
                    continue
                if not auth_db.check_sujet_allowed(user, row.get("sujet")):
                    skipped.append({"id": row["id"], "titre": row.get("titre"), "error": "sujet non autorise"})
                    continue
                accepted.append(row)

            prog_key_store = f"programme:{school_id}"
            programme = auth_db.kv_get(prog_key_store, [])
            existing_by_id = {r["id"]: r for r in programme}
            added = updated = 0
            for row in accepted:
                prev = existing_by_id.get(row["id"])
                if prev is not None:
                    # Fusion : les metadonnees du nouveau fichier remplacent les anciennes, mais les
                    # exercices deja planifies/generes sont toujours preserves (jamais ecrases ici).
                    for field, default in (
                        ("niveau", prev.get("niveau", "")), ("matiere", prev.get("matiere", "")),
                        ("unite", prev.get("unite", "")),
                        ("theme", prev.get("theme", "")), ("section", prev.get("section", "")),
                        ("sousTheme", prev.get("sousTheme", "")), ("sujet", prev.get("sujet", "")),
                        ("titre", prev.get("titre", "")), ("description", prev.get("description", "")),
                    ):
                        prev[field] = row.get(field, default)
                    updated += 1
                else:
                    new_row = dict(row)
                    new_row["exercices"] = row.get("exercices") or []
                    programme.append(new_row)
                    existing_by_id[row["id"]] = new_row
                    added += 1
            if added or updated:
                auth_db.kv_set(prog_key_store, programme)

            visible = programme
            if allowed is not None:
                visible = [r for r in programme if r.get("niveau") in allowed]
            visible = [r for r in visible if auth_db.check_sujet_allowed(user, r.get("sujet"))]
            self._json(200, {"programme": visible, "added": added, "updated": updated, "skipped": skipped})
            return

        if path == "/api/catalogue/replace":
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            new_catalogue = payload.get("catalogue")
            if not isinstance(new_catalogue, list):
                self._json(400, {"error": "catalogue invalide"})
                return
            auth_db.kv_set(f"catalogue:{school_id}", new_catalogue)
            self._json(200, {"catalogue": new_catalogue})
            return

        if path == "/api/programme/matiere/delete":
            # Supprime toutes les lignes de programme (et leurs exercices generes) d'une matiere
            # donnee. Reserve aux admins comme /api/programme/replace : action destructive et
            # irreversible sur des donnees partagees par toute l'ecole.
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            matiere = (payload.get("matiere") or "").strip()
            if not matiere:
                self._json(400, {"error": "matiere invalide"})
                return
            prog_key_store = f"programme:{school_id}"
            programme = auth_db.kv_get(prog_key_store, [])
            row_matiere = lambda r: (r.get("matiere") or "").strip() or "Français"
            kept = [r for r in programme if row_matiere(r) != matiere]
            removed = len(programme) - len(kept)
            if removed == 0:
                self._json(404, {"error": "Aucune ligne trouvee pour cette matiere."})
                return
            auth_db.kv_set(prog_key_store, kept)
            self._json(200, {"programme": kept, "removed": removed})
            return

        if path == "/api/programme/exercices":
            user = self._require_auth()
            if user is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(user, payload.get("schoolId"))
            if school_id is None:
                return
            prog_key_store = f"programme:{school_id}"
            row_id = payload.get("rowId")
            new_exercices = payload.get("exercices") or []
            programme = auth_db.kv_get(prog_key_store, [])
            row = next((r for r in programme if r.get("id") == row_id), None)
            if row is None:
                self._json(404, {"error": "ligne de programme inconnue"})
                return
            if user["role"] == "professor" and row.get("niveau") not in json.loads(user["niveaux_assignes"]):
                self._json(403, {"error": "Ce niveau ne vous est pas assigne."})
                return
            if user["role"] == "professor" and not auth_db.check_sujet_allowed(user, row.get("sujet")):
                self._json(403, {"error": "Ce sujet ne vous est pas autorise."})
                return
            for ex in new_exercices:
                if not auth_db.check_type_allowed(user, ex.get("type")):
                    self._json(403, {"error": f"Le type d'exercice '{ex.get('type')}' ne vous est pas autorise."})
                    return
            existing = {(e.get("type"), e.get("variante")) for e in row["exercices"]}
            for ex in new_exercices:
                if (ex.get("type"), ex.get("variante")) not in existing:
                    row["exercices"].append(ex)
                    existing.add((ex.get("type"), ex.get("variante")))
            auth_db.kv_set(prog_key_store, programme)
            self._json(200, {"row": row})
            return

        if path == "/api/programme/exercices/import":
            user = self._require_auth()
            if user is None:
                return
            if not auth_db.check_permission(user, "importZip"):
                self._json(403, {"error": "L'import d'exercices ne vous est pas autorise."})
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(user, payload.get("schoolId"))
            if school_id is None:
                return
            prog_key_store = f"programme:{school_id}"
            items = payload.get("items") or []
            programme = auth_db.kv_get(prog_key_store, [])
            by_id = {r["id"]: r for r in programme}
            allowed = None if user["role"] != "professor" else set(json.loads(user["niveaux_assignes"]))
            import_date = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
            results = []
            changed = False
            for item in items:
                row_id = item.get("rowId")
                row = by_id.get(row_id)
                if row is None:
                    results.append({"rowId": row_id, "ok": False, "error": "ligne de programme inconnue"})
                    continue
                if allowed is not None and row.get("niveau") not in allowed:
                    results.append({"rowId": row_id, "ok": False, "error": "niveau non assigne"})
                    continue
                if not auth_db.check_sujet_allowed(user, row.get("sujet")):
                    results.append({"rowId": row_id, "ok": False, "error": "sujet non autorise"})
                    continue
                if not auth_db.check_type_allowed(user, item.get("type")):
                    results.append({"rowId": row_id, "ok": False, "error": "type d'exercice non autorise"})
                    continue
                ex_type = item.get("type")
                base_variante = item.get("variante")
                contenu_b64 = item.get("contenuB64")
                
                if not ex_type or not base_variante or not contenu_b64:
                    results.append({"rowId": row_id, "ok": False, "error": "type/variante/contenu manquant"})
                    continue
                
                ex_variante = base_variante
                
                # Ensure the new imported exercise doesn't overwrite an existing one
                counter = 1
                while any(e.get("type") == ex_type and e.get("variante") == ex_variante for e in row["exercices"]):
                    ex_variante = f"{base_variante}-{counter}"
                    counter += 1

                ex = {"type": ex_type, "variante": ex_variante}
                row["exercices"].append(ex)
                ex["contenuB64"] = contenu_b64
                if item.get("isPdf"):
                    ex["isPdf"] = True
                    ex["pdfBase64"] = item.get("pdfBase64")
                if item.get("isYoutube"):
                    ex["isYoutube"] = True
                    ex["youtubeId"] = item.get("youtubeId")
                ex["statut"] = "genere"
                ex["genereLe"] = import_date
                ex["demandeModification"] = ""
                changed = True
                results.append({"rowId": row_id, "type": ex_type, "variante": ex_variante, "ok": True})
            if changed:
                auth_db.kv_set(prog_key_store, programme)
            self._json(200, {"programme": programme, "results": results})
            return

        if path == "/api/programme/exercices/reset":
            user = self._require_auth()
            if user is None:
                return
            if not auth_db.check_permission(user, "reset"):
                self._json(403, {"error": "La reinitialisation d'exercices ne vous est pas autorisee."})
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(user, payload.get("schoolId"))
            if school_id is None:
                return
            prog_key_store = f"programme:{school_id}"
            items = payload.get("items") or []
            programme = auth_db.kv_get(prog_key_store, [])
            by_id = {r["id"]: r for r in programme}
            allowed = None if user["role"] != "professor" else set(json.loads(user["niveaux_assignes"]))
            results = []
            changed = False
            for item in items:
                row_id = item.get("rowId")
                row = by_id.get(row_id)
                if row is None:
                    results.append({"rowId": row_id, "ok": False, "error": "ligne de programme inconnue"})
                    continue
                if allowed is not None and row.get("niveau") not in allowed:
                    results.append({"rowId": row_id, "ok": False, "error": "niveau non assigne"})
                    continue
                ex_type = item.get("type")
                ex_variante = item.get("variante")
                ex = next((e for e in row["exercices"] if e.get("type") == ex_type and e.get("variante") == ex_variante), None)
                if ex is None:
                    results.append({"rowId": row_id, "ok": False, "error": "exercice introuvable"})
                    continue
                # Repart a zero sans perdre l'historique : le contenu genere est archive, pas
                # detruit, avant d'etre efface (meme logique que l'ecrasement lors d'un import).
                if ex.get("contenuB64"):
                    ex.setdefault("historique", []).append({"contenuB64": ex["contenuB64"], "genereLe": ex.get("genereLe")})
                ex["contenuB64"] = None
                ex["statut"] = "a_generer"
                ex["genereLe"] = None
                ex["demandeModification"] = ""
                changed = True
                results.append({"rowId": row_id, "type": ex_type, "variante": ex_variante, "ok": True})
            if changed:
                auth_db.kv_set(prog_key_store, programme)
            self._json(200, {"programme": programme, "results": results})
            return

        if path == "/api/programme/exercices/remove":
            user = self._require_auth()
            if user is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(user, payload.get("schoolId"))
            if school_id is None:
                return
            prog_key_store = f"programme:{school_id}"
            row_id = payload.get("rowId")
            index = payload.get("index")
            programme = auth_db.kv_get(prog_key_store, [])
            row = next((r for r in programme if r.get("id") == row_id), None)
            if row is None:
                self._json(404, {"error": "ligne de programme inconnue"})
                return
            if user["role"] == "professor" and row.get("niveau") not in json.loads(user["niveaux_assignes"]):
                self._json(403, {"error": "Ce niveau ne vous est pas assigne."})
                return
            if not isinstance(index, int) or not (0 <= index < len(row["exercices"])):
                self._json(400, {"error": "index invalide"})
                return
            row["exercices"].pop(index)
            auth_db.kv_set(prog_key_store, programme)
            self._json(200, {"row": row})
            return

        if path == "/api/publish-course":
            # Publication directe vers Open edX Studio (voir openedx_publish.py) : cree et
            # publie les xblocks du cours a partir des exercices deja generes, sans passer par
            # l'export .tar.gz + import manuel dans Studio. Reservee aux admins : elle utilise
            # les identifiants OPENEDX_EMAIL/PASSWORD partages du serveur (jamais exposes au
            # navigateur) pour publier du contenu reellement visible sur la plateforme.
            admin = self._require_admin()
            if admin is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(admin, payload.get("schoolId"))
            if school_id is None:
                return
            required = ["niveau", "org", "course", "run", "displayName", "language", "platformUrl"]
            missing = [k for k in required if not payload.get(k)]
            if missing:
                self._json(400, {"error": f"Champs manquants : {', '.join(missing)}"})
                return
            opts = {
                "matiere": payload.get("matiere") or "",
                "niveau": payload["niveau"],
                "unite": payload.get("unite") or "",
                "section": payload.get("section") or "",
                "org": payload["org"], "course": payload["course"], "run": payload["run"],
                "displayName": payload["displayName"], "language": payload["language"],
                "platformUrl": payload["platformUrl"],
            }
            job_id = uuid.uuid4().hex
            with publish_jobs_lock:
                publish_jobs[job_id] = {
                    "status": "queued", "school_id": school_id, "opts": opts,
                    "log": [], "result": None, "error": None,
                }
            publish_job_queue.put(job_id)
            self._json(200, {"jobId": job_id})
            return

        if path == "/api/illustrate":
            # Illustration via un moteur qui n'autorise pas d'appel direct navigateur (CORS) —
            # contrairement a Nano Banana (Gemini), appele directement depuis app.html. Synchrone
            # (pas de file d'attente) : une generation d'image prend quelques secondes, pas besoin
            # du mecanisme jobId/poll utilise pour /api/generate.
            user = self._require_auth()
            if user is None:
                return
            try:
                payload = self._read_json_body()
                prompt = payload["prompt"]
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("prompt vide")
            except Exception as e:
                self._json(400, {"error": f"payload invalide : {e}"})
                return

            provider = (payload.get("provider") or "grok").lower()
            api_key = payload.get("api_key") or None
            model = payload.get("model") or None
            school_id = user["school_id"]

            if not user["can_use_own_key"]:
                api_key = None
            api_key = _resolve_api_key(provider, api_key, school_id)
            if not api_key:
                self._json(400, {"error": f"Aucune cle API fournie pour l'illustration ({provider})."})
                return

            try:
                if provider == "grok":
                    mime_type, data_b64 = generate_local.call_grok_image(api_key, model, prompt)
                else:
                    raise ValueError(f"Moteur d'illustration inconnu : {provider}")
                self._json(200, {"mimeType": mime_type, "data": data_b64})
            except Exception as e:
                self._json(502, {"error": str(e)})
            return

        if path == "/api/generate":
            user = self._require_auth()
            if user is None:
                return
            try:
                payload = self._read_json_body()
                prompt = payload["prompt"]
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("prompt vide")
            except Exception as e:
                self._json(400, {"error": f"payload invalide : {e}"})
                return

            provider = payload.get("provider") or DEFAULT_BACKEND
            api_key  = payload.get("api_key") or None
            model    = payload.get("model") or None
            row_id   = payload.get("rowId")
            ex_type  = payload.get("exType")
            ex_variante = payload.get("exVariante")
            kind = payload.get("kind", "generation")

            if not auth_db.check_provider_allowed(user, provider):
                self._json(403, {"error": f"Le moteur '{provider}' ne vous est pas autorise."})
                return
            if not auth_db.check_type_allowed(user, ex_type):
                self._json(403, {"error": "Ce type d'exercice ne vous est pas autorise."})
                return
            if kind == "modification" and not auth_db.check_permission(user, "modify"):
                self._json(403, {"error": "La modification d'exercices ne vous est pas autorisee."})
                return
            if not user["can_use_own_key"]:
                api_key = None  # ignore toute cle perso envoyee, force la resolution ecole/plateforme/env

            school_id = user["school_id"]
            if user["role"] == "professor" and row_id and school_id is not None:
                programme = auth_db.kv_get(f"programme:{school_id}", [])
                row = next((r for r in programme if r.get("id") == row_id), None)
                if row and not auth_db.check_sujet_allowed(user, row.get("sujet")):
                    self._json(403, {"error": "Ce sujet ne vous est pas autorise."})
                    return

            estimated_tokens = _estimate_tokens(len(prompt), len(prompt) * 0.6)
            allowed, message, usage = auth_db.quota_check(user)
            if not allowed:
                self._json(429, {"error": message, "usageToday": usage})
                return

            job_id = uuid.uuid4().hex
            with jobs_lock:
                jobs[job_id] = {
                    "status": "queued", "prompt": prompt,
                    "html_b64": None, "cost": None, "error": None,
                    "provider": provider, "api_key": api_key, "model": model,
                    "user_id": user["id"], "school_id": school_id, "row_id": row_id, "ex_type": ex_type,
                    "ex_variante": ex_variante, "kind": kind, "estimated_tokens": estimated_tokens,
                }
            job_queue.put(job_id)
            self._json(200, {"jobId": job_id})
            return

        if path == "/api/vault/items":
            user = self._require_auth()
            if user is None:
                return
            payload = self._read_json_body()
            school_id = self._require_school_id(user, payload.get("schoolId"))
            if school_id is None:
                return
            row = {
                "id": payload.get("rowId"), "niveau": payload.get("niveau"), "unite": payload.get("unite"),
                "section": payload.get("section"), "sujet": payload.get("sujet"), "titre": payload.get("titre"),
            }
            ex_type = payload.get("exType")
            ex_variante = payload.get("exVariante")
            contenu_b64 = payload.get("contenuB64")
            if not all([row["niveau"], row["sujet"], row["titre"], ex_type, ex_variante, contenu_b64]):
                self._json(400, {"error": "champs manquants pour la publication"})
                return
            item = auth_db.publish_vault_item(
                school_id, user["id"], user["username"], row, ex_type, ex_variante, contenu_b64
            )
            self._json(200, {"item": item})
            return

        m = re.match(r"^/api/vault/items/(\d+)/save$", path)
        if m:
            user = self._require_auth()
            if user is None:
                return
            item_id = int(m.group(1))
            item = auth_db.get_vault_item(item_id)
            if item is None:
                self._json(404, {"error": "exercice introuvable dans le coffre"})
                return
            resolved = auth_db.resolve_school_id(user)
            if resolved is not None and item["schoolId"] != resolved:
                self._json(403, {"error": "Cet exercice n'appartient pas a votre ecole."})
                return
            auth_db.save_vault_item(item_id, user["id"])
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "route inconnue"})

    # ---------------------------------------------------------------- PATCH
    def do_PATCH(self):
        m = re.match(r"^/api/users/(\d+)$", self.path)
        if m:
            admin = self._require_admin()
            if admin is None:
                return
            target_id = int(m.group(1))
            target = auth_db.get_user_by_id(target_id)
            if target is None:
                self._json(404, {"error": "utilisateur inconnu"})
                return
            if target["role"] in ("admin", "super_admin") and admin["role"] != "super_admin":
                self._json(403, {"error": "Seul un super-admin peut modifier un compte admin."})
                return
            if admin["role"] == "admin" and target["school_id"] != admin["school_id"]:
                self._json(403, {"error": "Ce compte n'appartient pas a votre ecole."})
                return
            payload = self._read_json_body()
            if "role" in payload and payload["role"] in ("admin", "super_admin") and admin["role"] != "super_admin":
                self._json(403, {"error": "Seul un super-admin peut accorder un role admin."})
                return
            try:
                update_kwargs = dict(
                    password=payload.get("password"),
                    role=payload.get("role"),
                    niveaux_assignes=payload.get("niveauxAssignes"),
                    is_active=payload.get("isActive"),
                )
                for key, col in (
                    ("quotaDaily", "quota_daily"), ("quotaWeekly", "quota_weekly"), ("quotaMonthly", "quota_monthly"),
                    ("allowedTypes", "allowed_types"), ("allowedSujets", "allowed_sujets"), ("allowedProviders", "allowed_providers"),
                    ("canUseOwnKey", "can_use_own_key"), ("permissions", "permissions"),
                ):
                    if key in payload:
                        update_kwargs[col] = payload.get(key)
                user = auth_db.update_user(target_id, **update_kwargs)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"user": user})
            return

        m = re.match(r"^/api/schools/(\d+)$", self.path)
        if m:
            user = self._require_super_admin()
            if user is None:
                return
            payload = self._read_json_body()
            try:
                school = auth_db.update_school(int(m.group(1)), payload.get("name", ""))
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"school": school})
            return

        self._json(404, {"error": "route inconnue"})

    # --------------------------------------------------------------- DELETE
    def do_DELETE(self):
        m = re.match(r"^/api/users/(\d+)$", self.path)
        if m:
            admin = self._require_admin()
            if admin is None:
                return
            target_id = int(m.group(1))
            target = auth_db.get_user_by_id(target_id)
            if target is None:
                self._json(404, {"error": "utilisateur inconnu"})
                return
            if target["role"] in ("admin", "super_admin") and admin["role"] != "super_admin":
                self._json(403, {"error": "Seul un super-admin peut supprimer un compte admin."})
                return
            if admin["role"] == "admin" and target["school_id"] != admin["school_id"]:
                self._json(403, {"error": "Ce compte n'appartient pas a votre ecole."})
                return
            if target["role"] == "super_admin" and auth_db.count_super_admins() <= 1:
                self._json(400, {"error": "Impossible de supprimer le dernier compte super-admin."})
                return
            auth_db.delete_user(target_id)
            self._json(200, {"ok": True})
            return

        m = re.match(r"^/api/vault/items/(\d+)/save$", self.path)
        if m:
            user = self._require_auth()
            if user is None:
                return
            auth_db.unsave_vault_item(int(m.group(1)), user["id"])
            self._json(200, {"ok": True})
            return

        m = re.match(r"^/api/vault/items/(\d+)$", self.path)
        if m:
            user = self._require_auth()
            if user is None:
                return
            item_id = int(m.group(1))
            item = auth_db.get_vault_item(item_id)
            if item is None:
                self._json(404, {"error": "exercice introuvable dans le coffre"})
                return
            is_author = item["authorId"] == user["id"]
            if not is_author and not _is_admin_or_above(user):
                self._json(403, {"error": "Seul l'auteur ou un administrateur peut retirer cet exercice du coffre."})
                return
            if not is_author:
                resolved = auth_db.resolve_school_id(user)
                if resolved is not None and item["schoolId"] != resolved:
                    self._json(403, {"error": "Cet exercice n'appartient pas a votre ecole."})
                    return
            auth_db.unpublish_vault_item(item_id)
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "route inconnue"})

    def log_message(self, format, *args):
        sys.stderr.write("[server] " + (format % args) + "\n")


def main():
    global DEFAULT_BACKEND, DEFAULT_MODEL
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    import os
    port_env = int(os.environ.get("PORT", 8765))
    parser.add_argument("--port", type=int, default=port_env)
    parser.add_argument("--host", default="0.0.0.0", help="Interface d'ecoute (0.0.0.0 = accessible depuis le reseau local)")
    parser.add_argument("--backend", default="cli",
                         choices=["cli", "anthropic", "openai", "gemini", "mistral", "api"],
                         help="Fournisseur par defaut (peut etre surcharge par chaque requete)")
    parser.add_argument("--model", default=None, help="Modele par defaut")
    args = parser.parse_args()

    DEFAULT_BACKEND = "anthropic" if args.backend == "api" else args.backend
    DEFAULT_MODEL = args.model

    bootstrap = auth_db.init_db()
    seed_from_embedded_plan()

    threading.Thread(target=worker_loop, daemon=True).start()
    threading.Thread(target=publish_worker_loop, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("============================================================")
    print(f"Serveur sur http://{args.host}:{args.port}  (backend par defaut: {DEFAULT_BACKEND})")
    if args.host == "0.0.0.0":
        print("Accessible depuis les autres machines du reseau local sur http://<IP-de-ce-poste>:" + str(args.port))
    if bootstrap:
        username, password = bootstrap
        print("------------------------------------------------------------")
        print("Premier demarrage : compte super-admin cree.")
        print(f"  Identifiant : {username}")
        print(f"  Mot de passe : {password}")
        print("  Notez-le maintenant et changez-le apres votre premiere connexion — il ne sera plus jamais affiche.")
        print("------------------------------------------------------------")
    print("Laisse cette fenetre ouverte pendant que l'equipe utilise l'application.")
    print("Ctrl+C pour arreter.")
    print("============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret du serveur.")


if __name__ == "__main__":
    main()
