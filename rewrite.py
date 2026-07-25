import re

with open('build/auth_db.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Replace sqlite3 imports with sqlalchemy
code = code.replace("import sqlite3", "import sqlalchemy\nfrom sqlalchemy import create_engine, text\nfrom sqlalchemy.exc import IntegrityError\nimport os")

# Replace _connect
connect_func = """
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def _connect():
    return engine.connect()
"""
code = re.sub(r'def _connect\(\):.*?return conn', connect_func, code, flags=re.DOTALL)

print("Writing transformed code...")
with open('build/auth_db_sqla.py', 'w', encoding='utf-8') as f:
    f.write(code)
