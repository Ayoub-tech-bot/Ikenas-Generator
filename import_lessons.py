import sqlite3
import json
import os
import glob
import re
import unicodedata
import base64
import uuid
import datetime

def normalize_text(text):
    if not isinstance(text, str): return ''
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = text.lower()
    text = re.sub(r'[^a-z0-9]', '', text)
    return text

def import_lessons():
    conn = sqlite3.connect('build/app.db')
    cursor = conn.cursor()
    
    keys_to_update = ['programme', 'programme:1']
    
    for key in keys_to_update:
        cursor.execute("SELECT value FROM kv_store WHERE key=?", (key,))
        row = cursor.fetchone()
        if not row:
            continue
            
        programme = json.loads(row[0])
        print(f"Loaded {len(programme)} items from {key}")
        
        files = glob.glob('Lessons/*.html')
        inserted_count = 0
        
        for file_path in files:
            filename = os.path.basename(file_path)
            m = re.match(r'(\d)eme-s(\d)-(.*?)-(.*?)\.html', filename)
            if not m:
                print(f"Could not parse filename: {filename}")
                continue
                
            niveau_num = m.group(1)
            section_num = m.group(2)
            sujet_str = m.group(3)
            
            niveau_query_norm = normalize_text(f'{niveau_num}eme')
            section_query_norm = normalize_text(f'Section {section_num}')
            
            # Find matching row in programme
            best_match = None
            for item in programme:
                if normalize_text(item.get('niveau')) == niveau_query_norm and \
                   normalize_text(item.get('unite')) == 'unite1' and \
                   normalize_text(item.get('section')) == section_query_norm:
                   
                    if normalize_text(sujet_str) in normalize_text(item.get('sujet')) or \
                       normalize_text(item.get('sujet')) in normalize_text(sujet_str):
                        best_match = item
                        break
                        
            if not best_match:
                print(f"NOT FOUND for {filename}")
                continue
                
            # Check if this exact file is already inserted
            with open(file_path, 'rb') as f:
                content_bytes = f.read()
                content_b64 = base64.b64encode(content_bytes).decode('ascii')
                
            already_exists = False
            for ex in best_match.get('exercices', []):
                if ex.get('type') == 'lecon' and ex.get('b64') == content_b64:
                    already_exists = True
                    break
                    
            if already_exists:
                continue
                
            new_ex = {
                "id": str(uuid.uuid4()),
                "type": "lecon",
                "variante": "import_html",
                "statut": "genere",
                "b64": content_b64,
                "genereLe": datetime.datetime.now().strftime("%Y-%m-%d"),
                "kind": "lecon"
            }
            
            if "exercices" not in best_match:
                best_match["exercices"] = []
                
            best_match["exercices"].append(new_ex)
            inserted_count += 1
            
        if inserted_count > 0:
            cursor.execute("UPDATE kv_store SET value=? WHERE key=?", (json.dumps(programme), key))
            print(f"Saved {inserted_count} lessons to {key}")
            
    conn.commit()
    conn.close()

if __name__ == '__main__':
    import_lessons()
