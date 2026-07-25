import sqlite3
import json
import base64
import os

def b64_to_utf8(b64):
    return base64.b64decode(b64).decode('utf-8')

def utf8_to_b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')

def inject_resize_script(html):
    # Remove old script if exists
    import re
    html = re.sub(r'<script>\s*// Auto-resize iframe for Open edX.*?</script>', '', html, flags=re.DOTALL)
    
    script_html = """
<script>
  // Auto-resize iframe for Open edX
  function sendHeight() {
      // Temporarily override min-height to get actual content height
      var bodyMin = document.body.style.minHeight;
      var htmlMin = document.documentElement.style.minHeight;
      document.body.style.setProperty('min-height', '0', 'important');
      document.documentElement.style.setProperty('min-height', '0', 'important');
      
      var h = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
      
      // Restore
      document.body.style.minHeight = bodyMin;
      document.documentElement.style.minHeight = htmlMin;
      
      window.parent.postMessage({type: 'iframeResize', height: h + 'px'}, '*');
  }
  window.addEventListener('load', sendHeight);
  // Optional: observe DOM changes if the exercise is highly dynamic
  if (window.ResizeObserver) {
      new ResizeObserver(sendHeight).observe(document.body);
  }
</script>
    """
    if '</body>' in html:
        return html.replace('</body>', script_html + '\n</body>')
    return html + '\n' + script_html

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
                        new_html = inject_resize_script(html)
                        if new_html != html:
                            ex['contenuB64'] = utf8_to_b64(new_html)
                            changed_prog = True
                            files_fixed += 1
                    
                    if 'historique' in ex:
                        for hist in ex['historique']:
                            if 'contenuB64' in hist:
                                html = b64_to_utf8(hist['contenuB64'])
                                new_html = inject_resize_script(html)
                                if new_html != html:
                                    hist['contenuB64'] = utf8_to_b64(new_html)
                                    changed_prog = True
        
        if changed_prog:
            cursor.execute('UPDATE kv_store SET value = ? WHERE key = ?', (json.dumps(prog), key))
            modified_count += 1

    conn.commit()
    conn.close()
    print(f'Fixed {files_fixed} HTML files for height resizing.')

if __name__ == '__main__':
    main()
