#!/usr/bin/env python3
"""
Couche donnees pour le systeme de comptes / roles / quotas de server.py.

Roles :
  super_admin : gere les comptes admin (+ tout ce qu'un admin peut faire)
  admin       : gere les comptes professeur (creation, quota, niveaux assignes,
                mot de passe), gere le catalogue de modeles HTML, generation illimitee
  professor   : genere des exercices uniquement pour ses niveaux assignes, dans la
                limite de son quota journalier (tokens ou nombre d'exercices)

Une connexion SQLite courte est ouverte par operation (pas de connexion partagee
entre threads) ; le mode WAL est active pour un comportement concurrent correct
sous ThreadingHTTPServer.
"""
import base64
import hashlib
import json
import os
import re
import secrets

import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras

import time
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")
SESSION_TTL_HOURS = 12
PBKDF2_ITERATIONS = 200_000

ROLES = ("super_admin", "admin", "professor")
QUOTA_TYPES = ("unlimited", "tokens", "count")



class DBConnection:
    def __init__(self):
        self.is_pg = IS_POSTGRES
        if self.is_pg:
            url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            self.conn = psycopg2.connect(url)
            self.conn.autocommit = False
        else:
            self.conn = sqlite3.connect(DB_PATH, timeout=10)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA foreign_keys=ON;")

    def execute(self, query, args=None):
        if self.is_pg:
            # PostgreSQL specific syntax conversions
            query = query.replace('?', '%s')
            query = query.replace('AUTOINCREMENT', 'SERIAL')
            query = query.replace('INSERT OR IGNORE', 'INSERT')
            query = query.replace('excluded.value', 'EXCLUDED.value')
            
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # Simulate lastrowid for PostgreSQL by appending RETURNING id to INSERTs
            is_insert = query.strip().upper().startswith("INSERT")
            if is_insert and "RETURNING" not in query.upper() and not "kv_store" in query.lower() and not "sessions" in query.lower() and not "usage_daily" in query.lower():
                query += " RETURNING id"
                
            try:
                if args:
                    cur.execute(query, args)
                else:
                    cur.execute(query)
                    
                if is_insert and cur.description:
                    row = cur.fetchone()
                    if row:
                        cur.lastrowid = row['id']
            except psycopg2.errors.UniqueViolation as e:
                if 'INSERT OR IGNORE' in query:
                    self.conn.rollback()
                    return cur
                self.conn.rollback()
                raise sqlite3.IntegrityError(str(e))
            return cur
        else:
            if args:
                return self.conn.execute(query, args)
            return self.conn.execute(query)

    def executescript(self, script):
        if self.is_pg:
            script = script.replace('AUTOINCREMENT', 'SERIAL')
            cur = self.conn.cursor()
            cur.execute(script)
        else:
            self.conn.executescript(script)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def _connect():
    return DBConnection()



def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password, password_hash, salt):
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)


def init_db():
    """Create tables if missing and bootstrap a super_admin if the users table is empty.
    Returns (username, password) of the bootstrap account if one was just created, else None.
    """
    conn = _connect()
    try:
        def init_db():
    conn = _connect()
    try:
        if IS_POSTGRES:
            # PostgreSQL specific schema
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('super_admin','admin','professor')),
                    niveaux_assignes TEXT NOT NULL DEFAULT '[]',
                    quota_type TEXT NOT NULL DEFAULT 'unlimited' CHECK(quota_type IN ('unlimited','tokens','count')),
                    quota_value INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    created_by INTEGER,
                    quota_daily INTEGER,
                    quota_weekly INTEGER,
                    quota_monthly INTEGER,
                    school_id INTEGER,
                    allowed_types TEXT,
                    allowed_sujets TEXT,
                    allowed_providers TEXT,
                    can_use_own_key INTEGER NOT NULL DEFAULT 1,
                    permissions TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    exercises_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                );
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS org_api_keys (
                    provider TEXT NOT NULL CHECK(provider IN ('anthropic','openai','gemini','mistral')),
                    school_id INTEGER,
                    api_key TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_org_api_keys_scoped ON org_api_keys(provider, COALESCE(school_id, -1));
                CREATE TABLE IF NOT EXISTS generation_events (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    row_id TEXT NOT NULL,
                    niveau TEXT NOT NULL,
                    unite TEXT NOT NULL,
                    section TEXT NOT NULL,
                    sujet TEXT NOT NULL,
                    titre TEXT NOT NULL,
                    ex_type TEXT NOT NULL,
                    ex_variante TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    school_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_gen_events_user_date ON generation_events(user_id, created_at);
                CREATE TABLE IF NOT EXISTS schools (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by INTEGER
                );
                CREATE TABLE IF NOT EXISTS vault_items (
                    id SERIAL PRIMARY KEY,
                    school_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    row_id TEXT,
                    niveau TEXT NOT NULL,
                    unite TEXT NOT NULL,
                    section TEXT NOT NULL,
                    sujet TEXT NOT NULL,
                    titre TEXT NOT NULL,
                    ex_type TEXT NOT NULL,
                    ex_variante TEXT NOT NULL,
                    contenu_b64 TEXT NOT NULL,
                    preview_text TEXT NOT NULL,
                    save_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    unpublished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_vault_items_school ON vault_items(school_id, unpublished_at, created_at);
                CREATE TABLE IF NOT EXISTS vault_saves (
                    id SERIAL PRIMARY KEY,
                    vault_item_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(vault_item_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_vault_saves_user ON vault_saves(user_id, created_at);
                """
            )
            conn.commit()
            
            # Check super_admin
            cur = conn.execute("SELECT COUNT(*) AS n FROM users")
            row = cur.fetchone()
            if row and row['n'] == 0:
                import secrets
                username = "admin"
                password = secrets.token_urlsafe(9)
                pw_hash, salt = hash_password(password)
                conn.execute(
                    "INSERT INTO users (username, password_hash, password_salt, role, created_at) VALUES (%s, %s, %s, 'super_admin', %s)",
                    (username, pw_hash, salt, _now_iso())
                )
                conn.commit()
                return username, password
            return None
        else:
            conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('super_admin','admin','professor')),
                niveaux_assignes TEXT NOT NULL DEFAULT '[]',
                quota_type TEXT NOT NULL DEFAULT 'unlimited' CHECK(quota_type IN ('unlimited','tokens','count')),
                quota_value INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS usage_daily (
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                exercises_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date)
            );

            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS org_api_keys (
                provider TEXT PRIMARY KEY CHECK(provider IN ('anthropic','openai','gemini','mistral')),
                api_key TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS generation_events (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                row_id TEXT NOT NULL,
                niveau TEXT NOT NULL,
                unite TEXT NOT NULL,
                section TEXT NOT NULL,
                sujet TEXT NOT NULL,
                titre TEXT NOT NULL,
                ex_type TEXT NOT NULL,
                ex_variante TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_gen_events_user_date ON generation_events(user_id, created_at);

            CREATE TABLE IF NOT EXISTS schools (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS vault_items (
                id SERIAL PRIMARY KEY,
                school_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                row_id TEXT,
                niveau TEXT NOT NULL,
                unite TEXT NOT NULL,
                section TEXT NOT NULL,
                sujet TEXT NOT NULL,
                titre TEXT NOT NULL,
                ex_type TEXT NOT NULL,
                ex_variante TEXT NOT NULL,
                contenu_b64 TEXT NOT NULL,
                preview_text TEXT NOT NULL,
                save_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                unpublished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_vault_items_school ON vault_items(school_id, unpublished_at, created_at);

            CREATE TABLE IF NOT EXISTS vault_saves (
                id SERIAL PRIMARY KEY,
                vault_item_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(vault_item_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_vault_saves_user ON vault_saves(user_id, created_at);
            """
        )
        conn.commit()

        # --- Migration additive : plafonds jour/semaine/mois (remplace quota_type/quota_value) ---
        # ALTER TABLE ADD COLUMN n'a pas d'equivalent "IF NOT EXISTS" en SQLite -> idempotent via try/except.
        for ddl in (
            "ALTER TABLE users ADD COLUMN quota_daily INTEGER",
            "ALTER TABLE users ADD COLUMN quota_weekly INTEGER",
            "ALTER TABLE users ADD COLUMN quota_monthly INTEGER",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # colonne deja presente (redemarrage ulterieur)
        conn.commit()

        # Backfill best-effort, une seule fois par utilisateur (marque : les 3 colonnes encore NULL).
        # quota_type == 'count'  -> recopie dans quota_daily (equivalent direct, meme semantique journaliere).
        # quota_type == 'tokens' -> aucun equivalent propre en nombre d'exercices, devient illimite.
        to_migrate = conn.execute(
            "SELECT id, quota_type, quota_value FROM users "
            "WHERE quota_daily IS NULL AND quota_weekly IS NULL AND quota_monthly IS NULL"
        ).fetchall()
        for r in to_migrate:
            daily = r["quota_value"] if r["quota_type"] == "count" and r["quota_value"] else None
            conn.execute("UPDATE users SET quota_daily = ? WHERE id = ?", (daily, r["id"]))
        conn.commit()

        # --- Migration additive : ecoles multi-tenant ---
        for ddl in (
            "ALTER TABLE users ADD COLUMN school_id INTEGER",
            "ALTER TABLE org_api_keys ADD COLUMN school_id INTEGER",
            "ALTER TABLE generation_events ADD COLUMN school_id INTEGER",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.commit()
        # org_api_keys avait PRIMARY KEY (provider) seul ; avec school_id nullable en plus,
        # SQLite ne peut pas etendre une PRIMARY KEY existante via ALTER TABLE -> reconstruction
        # de la table (une seule fois, detectee par l'absence de l'index unique attendu).
        has_scoped_pk = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='index' AND name='idx_org_api_keys_scoped'"
        ).fetchone()["n"]
        if not has_scoped_pk:
            conn.executescript(
                """
                CREATE TABLE org_api_keys_new (
                    provider TEXT NOT NULL CHECK(provider IN ('anthropic','openai','gemini','mistral')),
                    school_id INTEGER,
                    api_key TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by INTEGER
                );
                INSERT INTO org_api_keys_new (provider, school_id, api_key, updated_at, updated_by)
                    SELECT provider, school_id, api_key, updated_at, updated_by FROM org_api_keys;
                DROP TABLE org_api_keys;
                ALTER TABLE org_api_keys_new RENAME TO org_api_keys;
                CREATE UNIQUE INDEX idx_org_api_keys_scoped ON org_api_keys(provider, IFNULL(school_id, -1));
                """
            )
            conn.commit()

        # Backfill unique (une seule fois : declenche seulement si aucune ecole n'existe encore
        # mais qu'il y a deja des comptes admin/professor -> deploiement pre-multi-tenant).
        # Cree une ecole par defaut et y rattache tout ce qui existe deja, sans rien perdre :
        # comptes admin/professor, programme, catalogue (ex-cles globales de kv_store).
        school_count = conn.execute("SELECT COUNT(*) AS n FROM schools").fetchone()["n"]
        needs_backfill = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role IN ('admin','professor') AND school_id IS NULL"
        ).fetchone()["n"]
        if school_count == 0 and needs_backfill > 0:
            cur = conn.execute(
                "INSERT INTO schools (name, created_at, created_by) VALUES (?, ?, NULL)",
                ("École par défaut", _now_iso()),
            )
            default_school_id = cur.lastrowid
            conn.execute(
                "UPDATE users SET school_id = ? WHERE role IN ('admin','professor') AND school_id IS NULL",
                (default_school_id,),
            )
            conn.execute(
                "UPDATE generation_events SET school_id = ? WHERE school_id IS NULL",
                (default_school_id,),
            )
            for key in ("programme", "catalogue"):
                existing = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
                if existing is not None:
                    conn.execute(
                        "INSERT INTO kv_store (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (f"{key}:{default_school_id}", existing["value"]),
                    )
            conn.commit()

        # --- Migration additive : permissions granulaires par professeur ---
        for ddl in (
            "ALTER TABLE users ADD COLUMN allowed_types TEXT",
            "ALTER TABLE users ADD COLUMN allowed_sujets TEXT",
            "ALTER TABLE users ADD COLUMN allowed_providers TEXT",
            "ALTER TABLE users ADD COLUMN can_use_own_key INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE users ADD COLUMN permissions TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.commit()

        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        if row["n"] == 0:
            username = "admin"
            password = secrets.token_urlsafe(9)
            pw_hash, salt = hash_password(password)
            conn.execute(
                "INSERT INTO users (username, password_hash, password_salt, role, created_at) VALUES (?, ?, ?, 'super_admin', ?)",
                (username, pw_hash, salt, _now_iso()),
            )
            conn.commit()
            return username, password
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

# Actions de plateforme qu'un admin peut autoriser/interdire par professeur. Toutes vraies par
# defaut : un professeur existant avant ce chantier (permissions NULL en base) garde exactement
# son comportement actuel, rien ne se ferme silencieusement pour lui.
DEFAULT_PERMISSIONS = {
    "importZip": True, "importProgramme": True, "reset": True,
    "exportZip": True, "exportOlx": True, "modify": True,
}


def _json_list_or_none(raw):
    """NULL en base => aucune restriction (illimite). Liste vide => explicitement rien
    d'autorise. Distinction critique, ne jamais confondre les deux."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _permissions_from_row(row):
    raw = row["permissions"]
    merged = dict(DEFAULT_PERMISSIONS)
    if raw:
        try:
            merged.update(json.loads(raw))
        except (TypeError, ValueError):
            pass
    return merged


def _allowed_list(row, col):
    raw = row[col]
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def check_type_allowed(user_row, ex_type):
    """admin/super_admin toujours autorises ; professeur restreint uniquement si
    allowed_types n'est pas NULL (NULL = tous types autorises)."""
    if user_row["role"] != "professor":
        return True
    allowed = _allowed_list(user_row, "allowed_types")
    return allowed is None or ex_type in allowed


def check_sujet_allowed(user_row, sujet):
    if user_row["role"] != "professor":
        return True
    allowed = _allowed_list(user_row, "allowed_sujets")
    return allowed is None or sujet in allowed


def check_provider_allowed(user_row, provider):
    if user_row["role"] != "professor":
        return True
    allowed = _allowed_list(user_row, "allowed_providers")
    return allowed is None or provider in allowed


def check_permission(user_row, action):
    """action : une des cles de DEFAULT_PERMISSIONS (importZip, importProgramme, reset,
    exportZip, exportOlx, modify)."""
    if user_row["role"] != "professor":
        return True
    return _permissions_from_row(user_row).get(action, True)


def _user_public(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "schoolId": row["school_id"],
        "niveauxAssignes": json.loads(row["niveaux_assignes"]),
        "quotaDaily": row["quota_daily"],
        "quotaWeekly": row["quota_weekly"],
        "quotaMonthly": row["quota_monthly"],
        "allowedTypes": _json_list_or_none(row["allowed_types"]),
        "allowedSujets": _json_list_or_none(row["allowed_sujets"]),
        "allowedProviders": _json_list_or_none(row["allowed_providers"]),
        "canUseOwnKey": bool(row["can_use_own_key"]),
        "permissions": _permissions_from_row(row),
        "isActive": bool(row["is_active"]),
        "createdAt": row["created_at"],
    }


def resolve_school_id(user_row, requested_school_id=None):
    """Determine which school's data a request should operate on.
    admin/professor : toujours leur propre ecole (un schoolId envoye par le client est
    ignore, pour ne jamais permettre de cibler une autre ecole que la sienne).
    super_admin : doit choisir explicitement via requested_school_id (aucune ecole
    "par defaut" pour lui, il n'appartient a aucune) ; retourne None si non fourni."""
    if user_row["role"] in ("admin", "professor"):
        return user_row["school_id"]
    if requested_school_id:
        return int(requested_school_id)
    return None


def get_user_by_username(username):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return row
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def list_users():
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
        return [_user_public(r) for r in rows]
    finally:
        conn.close()


def _norm_quota(value):
    """0/None/'' -> None (illimite pour cette periode) ; sinon int positif."""
    if value in (None, "", 0, "0"):
        return None
    return int(value)


def _store_json_list(value):
    """None => NULL en base (illimite). Liste (meme vide) => stockee telle quelle
    (vide = explicitement rien d'autorise, different de illimite)."""
    if value is None:
        return None
    return json.dumps(value)


def create_user(username, password, role, niveaux_assignes=None,
                 quota_daily=None, quota_weekly=None, quota_monthly=None,
                 school_id=None, created_by=None,
                 allowed_types=None, allowed_sujets=None, allowed_providers=None,
                 can_use_own_key=True, permissions=None):
    if role not in ROLES:
        raise ValueError(f"role invalide : {role}")
    if role in ("admin", "professor") and not school_id:
        raise ValueError("une ecole est requise pour un compte admin ou professeur")
    if role == "super_admin":
        school_id = None
    if not username or not username.strip():
        raise ValueError("nom d'utilisateur requis")
    if not password or len(password) < 6:
        raise ValueError("mot de passe trop court (6 caracteres minimum)")
    pw_hash, salt = hash_password(password)
    conn = _connect()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, password_salt, role, niveaux_assignes, quota_daily, quota_weekly, quota_monthly, school_id, "
                "allowed_types, allowed_sujets, allowed_providers, can_use_own_key, permissions, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username.strip(), pw_hash, salt, role, json.dumps(niveaux_assignes or []),
                 _norm_quota(quota_daily), _norm_quota(quota_weekly), _norm_quota(quota_monthly),
                 school_id, _store_json_list(allowed_types), _store_json_list(allowed_sujets), _store_json_list(allowed_providers),
                 1 if can_use_own_key else 0, json.dumps(permissions) if permissions is not None else None,
                 _now_iso(), created_by),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"le nom d'utilisateur « {username} » est deja pris")
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _user_public(row)
    finally:
        conn.close()


def update_user(user_id, **fields):
    """fields may include: password (plain), role, niveaux_assignes (list), quota_daily,
    quota_weekly, quota_monthly, is_active, allowed_types, allowed_sujets, allowed_providers,
    can_use_own_key, permissions. Only provided keys are updated."""
    sets, params = [], []
    if "password" in fields and fields["password"]:
        if len(fields["password"]) < 6:
            raise ValueError("mot de passe trop court (6 caracteres minimum)")
        pw_hash, salt = hash_password(fields["password"])
        sets += ["password_hash = ?", "password_salt = ?"]
        params += [pw_hash, salt]
    if "role" in fields and fields["role"] is not None:
        if fields["role"] not in ROLES:
            raise ValueError(f"role invalide : {fields['role']}")
        sets.append("role = ?")
        params.append(fields["role"])
    if "niveaux_assignes" in fields and fields["niveaux_assignes"] is not None:
        sets.append("niveaux_assignes = ?")
        params.append(json.dumps(fields["niveaux_assignes"]))
    for col in ("quota_daily", "quota_weekly", "quota_monthly"):
        if col in fields:
            sets.append(f"{col} = ?")
            params.append(_norm_quota(fields[col]))
    for col in ("allowed_types", "allowed_sujets", "allowed_providers"):
        if col in fields:
            sets.append(f"{col} = ?")
            params.append(_store_json_list(fields[col]))
    if "can_use_own_key" in fields and fields["can_use_own_key"] is not None:
        sets.append("can_use_own_key = ?")
        params.append(1 if fields["can_use_own_key"] else 0)
    if "permissions" in fields and fields["permissions"] is not None:
        sets.append("permissions = ?")
        params.append(json.dumps(fields["permissions"]))
    if "is_active" in fields and fields["is_active"] is not None:
        sets.append("is_active = ?")
        params.append(1 if fields["is_active"] else 0)

    if not sets:
        row = get_user_by_id(user_id)
        return _user_public(row)

    conn = _connect()
    try:
        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_public(row)
    finally:
        conn.close()


def delete_user(user_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def count_super_admins():
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'super_admin' AND is_active = 1").fetchone()
        return row["n"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ecoles (multi-tenant) — un admin gere toujours exactement une ecole
# ---------------------------------------------------------------------------

def list_schools():
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, name, created_at FROM schools ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"], "createdAt": r["created_at"]} for r in rows]
    finally:
        conn.close()


def get_school(school_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT id, name, created_at FROM schools WHERE id = ?", (school_id,)).fetchone()
        return {"id": row["id"], "name": row["name"], "createdAt": row["created_at"]} if row else None
    finally:
        conn.close()


def get_school_name(school_id):
    """Nom seul, pour l'affichage dans l'en-tete d'un admin/professeur (evite un aller-retour
    complet via get_school pour ce cas d'usage frequent)."""
    if school_id is None:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT name FROM schools WHERE id = ?", (school_id,)).fetchone()
        return row["name"] if row else None
    finally:
        conn.close()


def create_school(name, admin_username, admin_password, created_by):
    if not name or not name.strip():
        raise ValueError("nom d'ecole requis")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO schools (name, created_at, created_by) VALUES (?, ?, ?)",
            (name.strip(), _now_iso(), created_by),
        )
        school_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    try:
        admin_user = create_user(
            username=admin_username, password=admin_password, role="admin",
            school_id=school_id, created_by=created_by,
        )
    except ValueError:
        # Ne pas laisser une ecole orpheline (sans admin) si la creation du compte echoue
        # (nom deja pris, mot de passe trop court...).
        conn2 = _connect()
        try:
            conn2.execute("DELETE FROM schools WHERE id = ?", (school_id,))
            conn2.commit()
        finally:
            conn2.close()
        raise
    return {"id": school_id, "name": name.strip(), "admin": admin_user}


def update_school(school_id, name):
    if not name or not name.strip():
        raise ValueError("nom d'ecole requis")
    conn = _connect()
    try:
        conn.execute("UPDATE schools SET name = ? WHERE id = ?", (name.strip(), school_id))
        conn.commit()
        return get_school(school_id)
    finally:
        conn.close()


def school_stats(school_id):
    conn = _connect()
    try:
        prof_count = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE school_id = ? AND role = 'professor' AND is_active = 1",
            (school_id,),
        ).fetchone()["n"]
        since = _period_since("week")
        total_7d = conn.execute(
            "SELECT COUNT(*) AS n FROM generation_events WHERE school_id = ? AND created_at >= ?",
            (school_id, since),
        ).fetchone()["n"]
        return {"professorCount": prof_count, "generations7d": total_7d}
    finally:
        conn.close()


def get_oldest_school_id():
    """Id de la toute premiere ecole jamais creee (= l'ecole par defaut issue de la migration
    sur un deploiement pre-multi-tenant, ou la toute premiere ecole reelle sur une installation
    neuve). Utilise UNIQUEMENT pour l'amorçage depuis le plan embarque (seed_from_embedded_plan
    dans server.py) — jamais pour l'affichage, ou list_schools() (trie par nom) doit etre utilise."""
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM schools ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id):
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_session_user(token):
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires_at > ?",
            (token, _now_iso()),
        ).fetchone()
        if row is None or not row["is_active"]:
            return None
        return row
    finally:
        conn.close()


def delete_session(token):
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Usage / quotas
# ---------------------------------------------------------------------------

def get_usage_today(user_id):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT tokens_used, exercises_count FROM usage_daily WHERE user_id = ? AND date = ?",
            (user_id, _today()),
        ).fetchone()
        if row is None:
            return {"tokensUsed": 0, "exercisesCount": 0}
        return {"tokensUsed": row["tokens_used"], "exercisesCount": row["exercises_count"]}
    finally:
        conn.close()


def increment_usage(user_id, tokens=0, exercises=0):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO usage_daily (user_id, date, tokens_used, exercises_count) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET tokens_used = tokens_used + excluded.tokens_used, "
            "exercises_count = exercises_count + excluded.exercises_count",
            (user_id, _today(), tokens, exercises),
        )
        conn.commit()
    finally:
        conn.close()


def get_usage_period(user_id, days):
    """Somme glissante de exercises_count sur les `days` derniers jours (aujourd'hui inclus).
    Fenetre glissante plutot que semaine ISO / mois calendaire : evite l'effet de bord ou le
    quota se reinitialiserait completement le 1er du mois meme apres une rafale la veille."""
    conn = _connect()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COALESCE(SUM(exercises_count), 0) AS n FROM usage_daily WHERE user_id = ? AND date >= ?",
            (user_id, since),
        ).fetchone()
        return row["n"]
    finally:
        conn.close()


def quota_check(user_row):
    """Returns (allowed: bool, message: str|None, usage: dict) for the given user before
    a generation is queued. Admin/super_admin always allowed. Tests 3 plafonds cumulables
    (jour, 7 jours glissants, 30 jours glissants) ; le premier depasse bloque."""
    if user_row["role"] in ("super_admin", "admin"):
        return True, None, get_usage_today(user_row["id"])
    usage = get_usage_today(user_row["id"])
    checks = (
        ("quota_daily", 1, "aujourd'hui"),
        ("quota_weekly", 7, "sur les 7 derniers jours"),
        ("quota_monthly", 30, "sur les 30 derniers jours"),
    )
    for col, days, label in checks:
        cap = user_row[col]
        if not cap:
            continue
        used = usage["exercisesCount"] if days == 1 else get_usage_period(user_row["id"], days)
        if used >= cap:
            return False, f"Quota atteint : {used}/{cap} exercices generes {label}.", usage
    return True, None, usage


# ---------------------------------------------------------------------------
# Key/value store (catalogue, programme)
# ---------------------------------------------------------------------------

def kv_get(key, default=None):
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])
    finally:
        conn.close()


def kv_set(key, value):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cles API "ecole" (organisation), une par fournisseur, gerees par le super-admin.
# Table dediee (pas kv_store) pour ne jamais risquer qu'une cle transite par un des
# endpoints generiques /api/catalogue ou /api/programme. La valeur ne doit jamais
# etre renvoyee a un client autre que via un usage strictement serveur (process_job).
# ---------------------------------------------------------------------------

ORG_KEY_PROVIDERS = ("anthropic", "openai", "gemini", "mistral", "grok", "kimi")


def org_key_status(school_id):
    """Retourne {provider: bool} pour l'ecole donnee (school_id=None => cles plateforme,
    gerees par le super-admin) — jamais la valeur de la cle elle-meme."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT provider FROM org_api_keys WHERE school_id IS ?", (school_id,)).fetchall()
        configured = {r["provider"] for r in rows}
        return {p: (p in configured) for p in ORG_KEY_PROVIDERS}
    finally:
        conn.close()


def get_org_key(provider, school_id):
    """Usage serveur uniquement (process_job) — ne jamais exposer le retour de cette fonction
    dans une reponse HTTP. school_id=None => cle plateforme (fallback ultime avant env var)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT api_key FROM org_api_keys WHERE provider = ? AND school_id IS ?",
            (provider, school_id),
        ).fetchone()
        return row["api_key"] if row else None
    finally:
        conn.close()


def set_org_key(provider, school_id, api_key, updated_by):
    if provider not in ORG_KEY_PROVIDERS:
        raise ValueError(f"fournisseur invalide : {provider}")
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT rowid FROM org_api_keys WHERE provider = ? AND school_id IS ?",
            (provider, school_id),
        ).fetchone()
        if api_key:
            if existing:
                conn.execute(
                    "UPDATE org_api_keys SET api_key = ?, updated_at = ?, updated_by = ? WHERE rowid = ?",
                    (api_key, _now_iso(), updated_by, existing["rowid"]),
                )
            else:
                conn.execute(
                    "INSERT INTO org_api_keys (provider, school_id, api_key, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
                    (provider, school_id, api_key, _now_iso(), updated_by),
                )
        else:
            conn.execute("DELETE FROM org_api_keys WHERE provider = ? AND school_id IS ?", (provider, school_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Suivi d'activite (qui a genere quoi, pour l'ecran Activite reserve admin+)
# ---------------------------------------------------------------------------

def _period_since(period):
    """'day'|'week'|'month' -> borne inferieure ISO pour created_at (fenetre glissante,
    meme semantique que get_usage_period) ; 'all'/None -> pas de borne."""
    days = {"day": 1, "week": 7, "month": 30}.get(period)
    if days is None:
        return None
    since_dt = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return since_dt.isoformat()


def log_generation_event(user_id, school_id, row, ex_type, ex_variante, kind):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO generation_events (user_id, school_id, row_id, niveau, unite, section, sujet, titre, ex_type, ex_variante, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, school_id, row.get("id", ""), row.get("niveau", ""), row.get("unite", ""), row.get("section", ""),
             row.get("sujet", ""), row.get("titre", ""), ex_type, ex_variante, kind, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def activity_totals(period=None, school_id=None):
    """school_id=None (super_admin uniquement) => agrege toutes les ecoles, avec le nom de
    l'ecole de chaque professeur dans le resultat."""
    since = _period_since(period)
    conn = _connect()
    try:
        query = ("SELECT ge.user_id AS user_id, u.username AS username, ge.school_id AS school_id, "
                  "s.name AS school_name, COUNT(*) AS total "
                  "FROM generation_events ge JOIN users u ON u.id = ge.user_id "
                  "LEFT JOIN schools s ON s.id = ge.school_id ")
        clauses, params = [], []
        if since:
            clauses.append("ge.created_at >= ?")
            params.append(since)
        if school_id:
            clauses.append("ge.school_id = ?")
            params.append(school_id)
        if clauses:
            query += "WHERE " + " AND ".join(clauses) + " "
        query += "GROUP BY ge.user_id, u.username, ge.school_id, s.name ORDER BY total DESC"
        rows = conn.execute(query, params).fetchall()
        return [{"userId": r["user_id"], "username": r["username"], "schoolId": r["school_id"],
                  "schoolName": r["school_name"], "total": r["total"]} for r in rows]
    finally:
        conn.close()


def activity_breakdown(period=None, user_id=None, school_id=None):
    since = _period_since(period)
    conn = _connect()
    try:
        query = ("SELECT ge.user_id AS user_id, u.username AS username, ge.school_id AS school_id, "
                  "s.name AS school_name, ge.niveau AS niveau, ge.section AS section, ge.sujet AS sujet, COUNT(*) AS count "
                  "FROM generation_events ge JOIN users u ON u.id = ge.user_id "
                  "LEFT JOIN schools s ON s.id = ge.school_id ")
        clauses, params = [], []
        if since:
            clauses.append("ge.created_at >= ?")
            params.append(since)
        if user_id:
            clauses.append("ge.user_id = ?")
            params.append(user_id)
        if school_id:
            clauses.append("ge.school_id = ?")
            params.append(school_id)
        if clauses:
            query += "WHERE " + " AND ".join(clauses) + " "
        query += "GROUP BY ge.user_id, u.username, ge.school_id, s.name, ge.niveau, ge.section, ge.sujet ORDER BY u.username, ge.niveau, ge.section, ge.sujet"
        rows = conn.execute(query, params).fetchall()
        return [{"userId": r["user_id"], "username": r["username"], "schoolId": r["school_id"],
                  "schoolName": r["school_name"], "niveau": r["niveau"],
                  "section": r["section"], "sujet": r["sujet"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def activity_log(period=None, user_id=None, school_id=None, limit=100, offset=0):
    since = _period_since(period)
    conn = _connect()
    try:
        clauses, params = [], []
        if since:
            clauses.append("ge.created_at >= ?")
            params.append(since)
        if user_id:
            clauses.append("ge.user_id = ?")
            params.append(user_id)
        if school_id:
            clauses.append("ge.school_id = ?")
            params.append(school_id)
        where = ("WHERE " + " AND ".join(clauses) + " ") if clauses else ""
        rows = conn.execute(
            "SELECT ge.id, ge.user_id, u.username, ge.school_id, s.name AS school_name, ge.niveau, ge.unite, "
            "ge.section, ge.sujet, ge.titre, ge.ex_type, ge.ex_variante, ge.kind, ge.created_at "
            "FROM generation_events ge JOIN users u ON u.id = ge.user_id "
            f"LEFT JOIN schools s ON s.id = ge.school_id {where}"
            "ORDER BY ge.created_at DESC LIMIT ? OFFSET ?",
            params + [limit + 1, offset],
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [{
            "id": r["id"], "userId": r["user_id"], "username": r["username"], "schoolId": r["school_id"],
            "schoolName": r["school_name"], "niveau": r["niveau"],
            "unite": r["unite"], "section": r["section"], "sujet": r["sujet"], "titre": r["titre"],
            "exType": r["ex_type"], "exVariante": r["ex_variante"], "kind": r["kind"], "createdAt": r["created_at"],
        } for r in rows]
        return events, has_more
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Coffre partage (Shared Vault) : publication volontaire par un professeur de ses
# meilleurs exercices generes, visibles par toute son ecole (decouple des niveaux/
# sujets assignes qui filtrent le programme). contenu_b64 est une copie figee au
# moment de la publication : independante de la ligne de programme d'origine.
# ---------------------------------------------------------------------------

def _vault_preview_text(contenu_b64, limit=220):
    try:
        html = base64.b64decode(contenu_b64).decode("utf-8", errors="ignore")
    except (ValueError, TypeError):
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _vault_item_dict(row, contenu=False):
    d = {
        "id": row["id"], "schoolId": row["school_id"], "authorId": row["author_id"],
        "authorName": row["author_name"], "rowId": row["row_id"], "niveau": row["niveau"],
        "unite": row["unite"], "section": row["section"], "sujet": row["sujet"], "titre": row["titre"],
        "exType": row["ex_type"], "exVariante": row["ex_variante"], "previewText": row["preview_text"],
        "saveCount": row["save_count"], "createdAt": row["created_at"],
        "unpublished": row["unpublished_at"] is not None,
    }
    if contenu:
        d["contenuB64"] = row["contenu_b64"]
    return d


def publish_vault_item(school_id, author_id, author_name, row, ex_type, ex_variante, contenu_b64):
    preview = _vault_preview_text(contenu_b64)
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO vault_items (school_id, author_id, author_name, row_id, niveau, unite, section, sujet, "
            "titre, ex_type, ex_variante, contenu_b64, preview_text, save_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (school_id, author_id, author_name, row.get("id", ""), row.get("niveau", ""), row.get("unite", ""),
             row.get("section", ""), row.get("sujet", ""), row.get("titre", ""), ex_type, ex_variante,
             contenu_b64, preview, _now_iso()),
        )
        conn.commit()
        new_row = conn.execute("SELECT * FROM vault_items WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _vault_item_dict(new_row)
    finally:
        conn.close()


def list_vault_items(school_id, niveau=None, sujet=None, sort="recent", viewer_user_id=None):
    conn = _connect()
    try:
        clauses = ["vi.school_id = ?", "vi.unpublished_at IS NULL"]
        params = [school_id]
        if niveau:
            clauses.append("vi.niveau = ?")
            params.append(niveau)
        if sujet:
            clauses.append("vi.sujet = ?")
            params.append(sujet)
        order = "vi.save_count DESC, vi.created_at DESC" if sort == "popular" else "vi.created_at DESC"
        query = (
            "SELECT vi.*, EXISTS(SELECT 1 FROM vault_saves vs WHERE vs.vault_item_id = vi.id AND vs.user_id = ?) "
            "AS saved_by_me FROM vault_items vi WHERE " + " AND ".join(clauses) + " ORDER BY " + order
        )
        rows = conn.execute(query, [viewer_user_id or -1] + params).fetchall()
        items = []
        for r in rows:
            d = _vault_item_dict(r)
            d["savedByMe"] = bool(r["saved_by_me"])
            items.append(d)
        return items
    finally:
        conn.close()


def get_vault_item(item_id, viewer_user_id=None):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM vault_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        d = _vault_item_dict(row, contenu=True)
        if viewer_user_id:
            saved = conn.execute(
                "SELECT 1 FROM vault_saves WHERE vault_item_id = ? AND user_id = ?", (item_id, viewer_user_id)
            ).fetchone()
            d["savedByMe"] = saved is not None
        return d
    finally:
        conn.close()


def unpublish_vault_item(item_id):
    conn = _connect()
    try:
        conn.execute("UPDATE vault_items SET unpublished_at = ? WHERE id = ?", (_now_iso(), item_id))
        conn.commit()
    finally:
        conn.close()


def save_vault_item(item_id, user_id):
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO vault_saves (vault_item_id, user_id, created_at) VALUES (?, ?, ?)",
            (item_id, user_id, _now_iso()),
        )
        if cur.rowcount:
            conn.execute("UPDATE vault_items SET save_count = save_count + 1 WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


def unsave_vault_item(item_id, user_id):
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM vault_saves WHERE vault_item_id = ? AND user_id = ?", (item_id, user_id)
        )
        if cur.rowcount:
            conn.execute("UPDATE vault_items SET save_count = MAX(0, save_count - 1) WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


def list_my_vault_saves(user_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT vi.* FROM vault_saves vs JOIN vault_items vi ON vi.id = vs.vault_item_id "
            "WHERE vs.user_id = ? ORDER BY vs.created_at DESC",
            (user_id,),
        ).fetchall()
        return [_vault_item_dict(r) for r in rows]
    finally:
        conn.close()
