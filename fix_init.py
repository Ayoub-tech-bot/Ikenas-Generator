import re

with open('build/auth_db.py', 'r', encoding='utf-8') as f:
    code = f.read()

new_init_db = '''def init_db():
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
        else:'''

old_sqlite_init = '''conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users ('''

# Insert the new code right after `def init_db():\n    conn = _connect()\n    try:`
code = code.replace(old_sqlite_init, new_init_db + '\n            ' + old_sqlite_init)

with open('build/auth_db.py', 'w', encoding='utf-8') as f:
    f.write(code)

print("init_db fixed")
