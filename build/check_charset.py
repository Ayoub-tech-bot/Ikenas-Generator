import sqlite3
import json
import base64

def b64_to_utf8(b64):
    return base64.b64decode(b64).decode('utf-8')

conn = sqlite3.connect('app.db')
cursor = conn.cursor()
cursor.execute('SELECT key, value FROM kv_store WHERE key LIKE "programme:%"')
rows = cursor.fetchall()

found = 0
total = 0
for key, value_json in rows:
    prog = json.loads(value_json)
    for row in prog:
        if 'exercices' in row:
            for ex in row['exercices']:
                if 'contenuB64' in ex:
                    total += 1
                    html = b64_to_utf8(ex['contenuB64']).lower()
                    if '<meta charset=' in html or '<meta charset="utf-8' in html:
                        found += 1

print(f'Found {found} out of {total} HTML files with meta charset')
