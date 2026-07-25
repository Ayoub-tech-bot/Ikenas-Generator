import pandas as pd
import os
import glob
import re
import unicodedata

df = pd.read_excel('Programme complet 2e-3e-4e-5e-6e AEP (import generateur-cours).xlsx')
df.columns = ['Niveau', 'Unite', 'Theme', 'Section', 'Sous-theme', 'Sujet', 'Titre', 'Description']
df_u1 = df[df['Unite'] == 1]

files = glob.glob('Lessons/*.html')
mapping = []

def normalize_text(text):
    if not isinstance(text, str): return ''
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = text.lower()
    text = re.sub(r'[^a-z0-9]', '', text)
    return text

for file_path in files:
    filename = os.path.basename(file_path)
    # Parse filename e.g. 2eme-s1-conjugaison-le-present-de-l-indicatif-des-verbes-etre-et-avo.html
    m = re.match(r'(\d)eme-s(\d)-(.*?)-(.*?)\.html', filename)
    if m:
        niveau_num = m.group(1)
        section_num = m.group(2)
        sujet_str = m.group(3)
        titre_str = m.group(4)
        
        niveau_query_norm = normalize_text(f'{niveau_num}eme')
        section_query_norm = normalize_text(f'Section {section_num}')
        
        best_match = None
        for idx, row in df_u1.iterrows():
            if normalize_text(row['Niveau']) == niveau_query_norm and normalize_text(row['Section']) == section_query_norm:
                if normalize_text(sujet_str) in normalize_text(row['Sujet']) or normalize_text(row['Sujet']) in normalize_text(sujet_str):
                    best_match = row
                    break
        
        if best_match is not None:
            mapping.append(f"| {filename} | {best_match['Niveau']} | {best_match['Section']} | {best_match['Sujet']} | {best_match['Titre']} |")
        else:
            mapping.append(f"| {filename} | NOT FOUND | | | |")
    else:
        mapping.append(f"| {filename} | PARSE ERROR | | | |")

with open('mapping_preview.md', 'w', encoding='utf-8') as f:
    f.write('| Fichier HTML | Niveau | Section | Sujet | Titre |\n')
    f.write('| --- | --- | --- | --- | --- |\n')
    f.write('\n'.join(mapping))
