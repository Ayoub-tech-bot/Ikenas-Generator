import re

with open('auth_db.py', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Add Postgres support imports
imports_to_add = """
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
"""
code = code.replace("import sqlite3", imports_to_add)

# 2. Replace _connect
new_connect = """
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
"""
code = re.sub(r'def _connect\(\):.*?return conn', new_connect, code, flags=re.DOTALL)

with open('auth_db.py', 'w', encoding='utf-8') as f:
    f.write(code)

print("auth_db.py patched successfully to support PostgreSQL!")
