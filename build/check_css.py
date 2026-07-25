import sqlite3, json, base64

conn = sqlite3.connect('app.db')
c = conn.cursor()
c.execute('SELECT value FROM kv_store WHERE key LIKE "programme:%" LIMIT 1')
row = c.fetchone()[0]
prog = json.loads(row)
for r in prog:
    if 'exercices' in r:
        for ex in r['exercices']:
            if 'contenuB64' in ex:
                html = base64.b64decode(ex['contenuB64']).decode('utf-8')
                print(html[:1500])
                print("\n\n----- END HTML HEAD -----\n\n")
                exit()
