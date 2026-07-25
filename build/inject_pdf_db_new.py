import sqlite3
import json
import base64
import os
import sys

# Import functions from generate_local.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import generate_local

def b64_to_utf8(b64):
    return base64.b64decode(b64).decode('utf-8')

def utf8_to_b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')

def main():
    db_path = 'app.db'
    if not os.path.exists(db_path):
        print(f"Error: {db_path} not found.")
        return
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT key, value FROM kv_store WHERE key LIKE "programme:%"')
    rows = cursor.fetchall()

    modified_count = 0
    files_fixed = 0
    for key, value_json in rows:
        prog = json.loads(value_json)
        changed_prog = False
        for row in prog:
            if 'exercices' in row:
                for ex in row['exercices']:
                    if 'contenuB64' in ex:
                        html = b64_to_utf8(ex['contenuB64'])
                        # This will strip old button and add the new one
                        new_html = generate_local.inject_pdf_button(html)
                        if new_html != html:
                            ex['contenuB64'] = utf8_to_b64(new_html)
                            changed_prog = True
                            files_fixed += 1
                    
                    if 'historique' in ex:
                        for hist in ex['historique']:
                            if 'contenuB64' in hist:
                                html = b64_to_utf8(hist['contenuB64'])
                                new_html = generate_local.inject_pdf_button(html)
                                if new_html != html:
                                    hist['contenuB64'] = utf8_to_b64(new_html)
                                    changed_prog = True
        
        if changed_prog:
            cursor.execute('UPDATE kv_store SET value = ? WHERE key = ?', (json.dumps(prog), key))
            modified_count += 1

    conn.commit()
    conn.close()
    print(f'Fixed {files_fixed} HTML files for PDF button.')

if __name__ == '__main__':
    main()
