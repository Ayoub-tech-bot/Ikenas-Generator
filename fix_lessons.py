import sqlite3
import json

def fix_lessons():
    conn = sqlite3.connect('build/app.db')
    cursor = conn.cursor()
    
    keys_to_update = ['programme', 'programme:1']
    
    for key in keys_to_update:
        cursor.execute("SELECT value FROM kv_store WHERE key=?", (key,))
        row = cursor.fetchone()
        if not row:
            continue
            
        programme = json.loads(row[0])
        fixed_count = 0
        
        for item in programme:
            for ex in item.get('exercices', []):
                if ex.get('variante') == 'import_html' and 'b64' in ex:
                    ex['contenuB64'] = ex.pop('b64')
                    fixed_count += 1
                    
        if fixed_count > 0:
            cursor.execute("UPDATE kv_store SET value=? WHERE key=?", (json.dumps(programme), key))
            print(f"Fixed {fixed_count} lessons in {key}")
            
    conn.commit()
    conn.close()

if __name__ == '__main__':
    fix_lessons()
