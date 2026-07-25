import sqlite3
import json
import base64
import os

def b64_to_utf8(b64):
    return base64.b64decode(b64).decode('utf-8')

def utf8_to_b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')

def inject_pdf_button(html):
    if 'id="pdf-download-btn"' in html or 'window.print()' in html:
        return html
    button_html = """
    <div id="pdf-download-btn" style="text-align: center; margin: 30px 0;">
        <button onclick="window.print()" style="background-color: #2c3e50; color: white; padding: 10px 20px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; display: inline-flex; align-items: center; gap: 8px;">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
            Télécharger en PDF / Imprimer
        </button>
        <style>
            @media print {
                #pdf-download-btn {
                    display: none !important;
                }
            }
        </style>
    </div>
    """
    if '</body>' in html:
        return html.replace('</body>', button_html + '\n</body>')
    return html + '\n' + button_html

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
    for key, value_json in rows:
        prog = json.loads(value_json)
        changed_prog = False
        for row in prog:
            if 'exercices' in row:
                for ex in row['exercices']:
                    if 'contenuB64' in ex:
                        html = b64_to_utf8(ex['contenuB64'])
                        new_html = inject_pdf_button(html)
                        if new_html != html:
                            ex['contenuB64'] = utf8_to_b64(new_html)
                            changed_prog = True
                    
                    if 'historique' in ex:
                        for hist in ex['historique']:
                            if 'contenuB64' in hist:
                                html = b64_to_utf8(hist['contenuB64'])
                                new_html = inject_pdf_button(html)
                                if new_html != html:
                                    hist['contenuB64'] = utf8_to_b64(new_html)
                                    changed_prog = True
        
        if changed_prog:
            cursor.execute('UPDATE kv_store SET value = ? WHERE key = ?', (json.dumps(prog), key))
            modified_count += 1

    conn.commit()
    conn.close()
    print(f'Modified {modified_count} programme entries in the database.')

if __name__ == '__main__':
    main()
