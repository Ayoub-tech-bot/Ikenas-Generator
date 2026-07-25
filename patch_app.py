import io

def patch_file():
    with open('app.html', 'r', encoding='utf-8') as f:
        content = f.read()

    # Chunk 1
    target1 = """    <div class="modal-footer" style="justify-content:space-between;">
      <span id="libImportHtmlSummary" style="font-size:13px; color:var(--muted);">0 fichier(s) prêt(s) à importer.</span>
      <button id="libImportHtmlConfirmBtn" class="btn-cfg-primary" disabled>Valider l'importation</button>
    </div>"""
    
    replace1 = """    <div class="modal-footer" style="justify-content:space-between;">
      <div style="display:flex; align-items:center; gap:16px;">
        <span id="libImportHtmlSummary" style="font-size:13px; color:var(--muted);">0 fichier(s) prêt(s) à importer.</span>
        <select id="libImportHtmlTypeSelect" style="padding:6px; border-radius:4px; border:1px solid var(--border);">
          <option value="lecon">Importer comme Leçons</option>
          <option value="import_externe">Importer comme Exercices</option>
        </select>
      </div>
      <button id="libImportHtmlConfirmBtn" class="btn-cfg-primary" disabled>Valider l'importation</button>
    </div>"""

    # Chunk 2
    target2 = """async function parseImportHtmlFiles(files) {
  const parsed = [];
  for (const file of files) {
    const text = await file.text();
    const trimmed = text.trim().toLowerCase();
    const item = {
      _uid: ++libImportUidCounter, path: file.name,
      niveau: null, unite: null, section: null, sujet: null, rowId: null,
    };
    if (!trimmed.startsWith('<!doctype') && !trimmed.startsWith('<html')) {
      item.parseError = "ne ressemble pas à un exercice HTML valide";
    } else {
      item.html = text;
    }
    parsed.push(item);
  }
  return parsed;
}"""

    replace2 = """async function parseImportHtmlFiles(files) {
  const parsed = [];
  for (const file of files) {
    const text = await file.text();
    const trimmed = text.trim().toLowerCase();
    const item = {
      _uid: ++libImportUidCounter, path: file.name,
      niveau: null, unite: null, section: null, sujet: null, rowId: null,
    };
    if (!trimmed.startsWith('<!doctype') && !trimmed.startsWith('<html')) {
      item.parseError = "ne ressemble pas à un exercice HTML valide";
    } else {
      item.html = text;
      
      const m = file.name.match(/(\\d)eme-s(\\d)-(.*?)-.*?\\.html?/i);
      if (m && state.programme) {
        const niveauNorm = slugifyJs(`${m[1]}eme`);
        const sectionNorm = slugifyJs(`Section ${m[2]}`);
        const sujetStr = slugifyJs(m[3]);
        
        const match = state.programme.find(r => 
          slugifyJs(r.niveau) === niveauNorm && 
          slugifyJs(r.section) === sectionNorm && 
          (slugifyJs(r.sujet).includes(sujetStr) || sujetStr.includes(slugifyJs(r.sujet)))
        );
        if (match) {
          item.niveau = match.niveau;
          item.unite = match.unite;
          item.section = match.section;
          item.sujet = match.sujet;
          item.rowId = match.id;
        }
      }
    }
    parsed.push(item);
  }
  return parsed;
}"""

    # Chunk 3
    target3 = """document.getElementById('libImportHtmlConfirmBtn').addEventListener('click', async () => {
  const items = htmlImportItems.filter(htmlImportItemIsValid).map(item => ({
    rowId: item.rowId, type: 'import_externe', variante: slugifyJs(item.path.replace(/\.html?$/i, '')),
    contenuB64: utf8ToB64(item.html),
  }));
  if (items.length === 0) return;"""

    replace3 = """document.getElementById('libImportHtmlConfirmBtn').addEventListener('click', async () => {
  const typeSelect = document.getElementById('libImportHtmlTypeSelect');
  const importType = typeSelect ? typeSelect.value : 'import_externe';
  const items = htmlImportItems.filter(htmlImportItemIsValid).map(item => ({
    rowId: item.rowId, type: importType, variante: slugifyJs(item.path.replace(/\.html?$/i, '')),
    contenuB64: utf8ToB64(item.html),
  }));
  if (items.length === 0) return;"""

    content = content.replace(target1, replace1)
    content = content.replace(target2, replace2)
    content = content.replace(target3, replace3)
    
    with open('app.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched app.html successfully.")

if __name__ == "__main__":
    patch_file()
