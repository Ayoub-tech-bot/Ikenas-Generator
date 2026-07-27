#!/usr/bin/env python3
"""
Publication directe des exercices generes vers Open edX Studio, sans passer par l'export
.tar.gz + import manuel dans Studio.

Portage fidele de `server (1).js` -- un prototype Node.js deja verifie fonctionnel sur une
autre machine contre cette meme plateforme Ikenas. Toute la logique ci-dessous (ordre des
requetes, en-tetes, JWT, forme exacte des payloads) suit ce fichier de reference le plus
precisement possible plutot que la documentation generale d'edx-platform, qui laisse plusieurs
details (JWT notamment) sous-entendus.

  1. Authentification par session (cookies + jeton CSRF Django, PLUS un JWT extrait de cookies
     Open edX splittes) : l'endpoint Studio qui cree du contenu (/xblock/) est une vue DRF qui,
     sur cette plateforme, exige ce JWT en plus du cookie de session -- son absence pure et
     simple (ce que faisait une premiere version de ce module) se traduit par un 403 nu.
       a. GET  {LMS}/                      -> cookie csrftoken initial
       b. POST {LMS}/login_ajax            -> connexion (email/mdp + jeton CSRF en en-tete)
       c. GET  {STUDIO}/                   -> bascule la session LMS vers le sous-domaine Studio
       d. GET  {STUDIO}/course/{courseId}  -> privileges sur le cours + jeton CSRF Studio final
       e. Extraction du JWT : cookies edx-jwt-cookie-header-payload + edx-jwt-cookie-signature,
          concatenes par un point -> en-tete Authorization: JWT <...> sur tous les appels suivants.
  2. Creation en cascade des xblocks (chapter > sequential > vertical > problem/html) via
     POST {STUDIO}/xblock/ (creation) puis POST {STUDIO}/xblock/{locator} (contenu : {data,
     metadata: {}}).
  3. Upload des exercices notes comme "assets" du cours (fichiers HTML bruts), en
     multipart/form-data vers {STUDIO}/assets/{courseId}/.
  4. Injection du XML <problem>/jsinput qui pointe vers l'URL de l'asset uploade (meme gabarit
     que dans server (1).js / last.xml de reference).
  5. Publication (make_public) de chaque chapitre, qui se propage a tous ses enfants.

Transport HTTP : curl_cffi (impersonation TLS/HTTP2/JA3), pas urllib. Meme flux logique que
server (1).js (fetch Node natif, sans impersonation) reproduit ci-dessous a l'identique --
en-tetes, JWT, Referer, forme des payloads -- mais toujours bloque en 403 nu (Server: Caddy)
avec urllib meme une fois cette logique parfaitement alignee sur le prototype de reference.
Le fait que le prototype Node.js fonctionne (sur une autre machine ordinaire, meme reseau public,
pas de VPN/hebergement special) alors qu'un portage Python logiquement identique echoue de facon
identique pointe vers l'empreinte TLS elle-meme : le module ssl/urllib de la stdlib Python a une
signature JA3 tres largement utilisee par des scrapers/bots et donc frequemment bloquee par les
WAF/CDN modernes, alors que la pile TLS de Node (utilisee par le prototype) ne l'est pas. curl_cffi
reproduit une empreinte TLS non suspecte (Chrome) plutot que celle, tres reconnaissable, de Python.
"""
import json
import mimetypes
import os
import re

import certifi
from curl_cffi import requests as cf_requests

# curl_cffi embarque son propre libcurl, qui ne lit pas automatiquement le magasin de
# certificats de Windows -- verify=certifi.where() est passe explicitement a la construction de
# chaque session (voir StudioSession.__init__) plutot que de compter sur la resolution
# automatique par variable d'environnement.
IMPERSONATE = "chrome124"


class PublishError(Exception):
    """Erreur de publication destinee a etre affichee telle quelle a l'admin."""


# ---------------------------------------------------------------------------
# Configuration (.env optionnel a cote de ce fichier, ou variables d'environnement)
# ---------------------------------------------------------------------------

def _load_dotenv(path):
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def get_config():
    lms_url = os.environ.get("OPENEDX_LMS_URL", "").rstrip("/")
    email = os.environ.get("OPENEDX_EMAIL", "")
    password = os.environ.get("OPENEDX_PASSWORD", "")
    if not (lms_url and email and password):
        raise PublishError(
            "Publication directe non configuree sur ce serveur : definissez OPENEDX_LMS_URL, "
            "OPENEDX_EMAIL et OPENEDX_PASSWORD (variables d'environnement, ou fichier "
            "build/.env -- memes noms que le prototype Node.js)."
        )
    # Meme convention que server (1).js : Studio = LMS avec "studio." insere avant le domaine.
    studio_url = os.environ.get("OPENEDX_STUDIO_URL", "").rstrip("/")
    if not studio_url:
        studio_url = re.sub(r"://", "://studio.", lms_url, count=1)
    return lms_url, studio_url, email, password


# ---------------------------------------------------------------------------
# Session HTTP (cookies + CSRF + JWT) -- fidele a server (1).js
# ---------------------------------------------------------------------------

class StudioSession:
    """Client HTTP a etat (cookies persistants, JWT extrait apres connexion, empreinte TLS/HTTP2
    de Chrome via curl_cffi) reproduisant le comportement de fetchFollow()/getStudioAuth() dans
    server (1).js, plus une empreinte TLS non suspecte (voir docstring du module)."""

    def __init__(self):
        # verify=certifi.where() explicite : curl_cffi ne lit pas le magasin de certificats de
        # Windows par defaut, et un chemin explicite evite toute ambiguite d'ordre d'import/env.
        self.session = cf_requests.Session(impersonate=IMPERSONATE, verify=certifi.where())
        self.jwt = None
        self.last_headers = {}  # derniere reponse -- indice diagnostique (WAF/CDN vs Django)

    def cookie(self, name):
        return self.session.cookies.get(name)

    def csrftoken(self):
        return self.cookie("csrftoken")

    def _finish(self, resp):
        self.last_headers = dict(resp.headers)
        return resp.status_code, resp.content

    def server_hint(self):
        """Indices tires des en-tetes de la derniere reponse pour distinguer un refus applicatif
        (Django/edx-platform) d'un blocage en amont (WAF, CDN, reverse-proxy) : les seconds
        renvoient generiquement 'HTTP 403' sans aucun detail exploitable dans le corps."""
        interesting = [h for h in ("Server", "Via", "CF-RAY", "X-Amzn-RequestId", "X-Cache")
                       if h in self.last_headers]
        return ", ".join(f"{h}={self.last_headers[h]}" for h in interesting) or None

    def get(self, url, referer=None):
        headers = {"Referer": referer} if referer else {}
        resp = self.session.get(url, headers=headers, timeout=30)
        return self._finish(resp)

    def post_form(self, url, fields, referer=None):
        headers = {}
        if referer:
            headers["Referer"] = referer
        token = self.csrftoken()
        if token:
            headers["X-CSRFToken"] = token
        resp = self.session.post(url, data=fields, headers=headers, timeout=30)
        return self._finish(resp)

    def _studio_headers(self, referer):
        # Reproduit exactement studioPost() de server (1).js : CSRF + JWT (si obtenu) + AJAX.
        headers = {
            "Accept": "application/json",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }
        token = self.csrftoken()
        if token:
            headers["X-CSRFToken"] = token
        if self.jwt:
            headers["Authorization"] = f"JWT {self.jwt}"
        return headers

    def studio_post_json(self, url, payload, referer):
        resp = self.session.post(url, json=payload, headers=self._studio_headers(referer), timeout=30)
        return self._finish(resp)

    def studio_post_multipart_file(self, url, field_name, filename, content_bytes, content_type, referer):
        import curl_cffi
        mp = curl_cffi.CurlMime()
        mp.addpart(
            name=field_name, 
            filename=filename, 
            content_type=content_type or "application/octet-stream", 
            data=content_bytes
        )
        resp = self.session.post(url, multipart=mp, headers=self._studio_headers(referer), timeout=30)
        return self._finish(resp)


def _body_excerpt(body, max_len=300):
    """Extrait un message d'erreur lisible d'une reponse Studio/Django : le <title> si c'est
    une page d'erreur HTML, sinon le texte brut tronque. Sans ca, toutes les erreurs
    remontaient comme un simple 'HTTP 403' sans dire pourquoi -- inexploitable pour diagnostiquer
    a distance (CSRF, permissions, format de locator...) sans acces aux logs du serveur."""
    text = body.decode("utf-8", "replace").strip()
    m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if m:
        text = m.group(1).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _fail(session, prefix, status, body):
    hint = session.server_hint()
    detail = _body_excerpt(body)
    suffix = f" [{hint}]" if hint else ""
    raise PublishError(f"{prefix} (HTTP {status}) -- {detail}{suffix}")


def login(session, lms_url, studio_url, email, password, course_id):
    """Reproduit getStudioAuth() de server (1).js pas a pas, Referer par etape inclus."""
    status, body = session.get(f"{lms_url}/")
    if status >= 400:
        _fail(session, f"Impossible de joindre le LMS ({lms_url})", status, body)
    if not session.csrftoken():
        raise PublishError("Le LMS n'a pas renvoye de cookie csrftoken -- verifiez OPENEDX_LMS_URL.")

    status, body = session.post_form(f"{lms_url}/login_ajax", {"email": email, "password": password},
                                      referer=f"{lms_url}/login")
    if status >= 400:
        _fail(session, "Connexion refusee par le LMS (identifiants OPENEDX_EMAIL/OPENEDX_PASSWORD "
                        "invalides ou compte bloque ?)", status, body)
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise PublishError(f"Connexion refusee par le LMS : {parsed.get('value') or parsed}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass  # Reponse non-JSON : on se fie au status HTTP ci-dessus.

    status, body = session.get(f"{studio_url}/", referer=f"{lms_url}/dashboard")
    if status >= 400:
        _fail(session, f"Bascule LMS -> Studio echouee -- verifiez OPENEDX_STUDIO_URL ({studio_url})",
              status, body)

    status, body = session.get(f"{studio_url}/course/{course_id}", referer=f"{studio_url}/")
    if status >= 400:
        _fail(session, f"Acces refuse au cours '{course_id}' dans Studio (le compte OPENEDX_EMAIL "
                        f"a-t-il les droits Staff/Admin sur ce cours, et ce cours existe-t-il deja "
                        f"dans Studio ?)", status, body)

    # JWT (authentification DRF) : reconstruit a partir des deux cookies edx-jwt-cookie-*, comme
    # server (1).js. Silencieusement absent si la plateforme n'utilise pas ce mecanisme -- les
    # appels suivants fonctionnent alors sans en-tete Authorization, cookie de session seul.
    jwt_payload = session.cookie("edx-jwt-cookie-header-payload")
    jwt_sig = session.cookie("edx-jwt-cookie-signature")
    if jwt_payload and jwt_sig:
        session.jwt = f"{jwt_payload}.{jwt_sig}"

    if not session.csrftoken():
        raise PublishError("Aucun jeton CSRF Studio obtenu apres le handshake -- publication impossible.")
    return session.csrftoken()


# ---------------------------------------------------------------------------
# Creation de contenu (xblocks, assets)
# ---------------------------------------------------------------------------

# Global cache to bypass LMS API delays across requests
LOCAL_BLOCK_CACHE = {}

def fetch_course_blocks(session, lms_url, course_id, email):
    import urllib.parse
    encoded_course_id = urllib.parse.quote_plus(course_id)
    username = email.split('@')[0] if email else 'stage_2'
    
    url = f"{lms_url}/api/courses/v1/blocks/?course_id={encoded_course_id}&username={username}&depth=all&all_blocks=true&return_type=dict&requested_fields=children"
    
    status, body = session.get(url)
    if status != 200:
        raise PublishError(f"Failed to fetch course structure: [{status}] {_body_excerpt(body)}")
        
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise PublishError(f"Response not JSON (status {status}): {_body_excerpt(body)}")
        
    blocks = data.get("blocks", {})
    
    # Merge global cache into fetched blocks
    for loc, block in LOCAL_BLOCK_CACHE.items():
        if loc not in blocks:
            blocks[loc] = block
        else:
            existing_children = set(blocks[loc].get('children', []))
            for child in block.get('children', []):
                if child not in existing_children:
                    blocks[loc].setdefault('children', []).append(child)
                    
    return blocks

def find_children_by_name(blocks_by_id, parent_locator, category, display_name):
    parent = blocks_by_id.get(parent_locator)
    if not parent:
        return []
        
    target_name = display_name.strip().lower()
    matches = []
    
    for child_id in parent.get('children', []):
        child = blocks_by_id.get(child_id)
        if not child:
            continue
            
        child_type = child.get('type')
        child_name = child.get('display_name', '').strip().lower()
        
        if child_type == category and child_name == target_name:
            matches.append(child_id)
            
    return matches

def find_or_create_block(session, studio_url, course_id, blocks_by_id, parent_locator, category, display_name, data=None):
    existing_ids = find_children_by_name(blocks_by_id, parent_locator, category, display_name)
    
    html_file_url = None
    if data and category == 'problem':
        m = re.search(r'html_file="([^"]+)"', data)
        if m:
            html_file_url = m.group(1)

    for existing_id in existing_ids:
        if not html_file_url:
            return existing_id
            
        try:
            status, body = session.get(f"{studio_url}/xblock/{existing_id}")
            if status == 200:
                block_data = json.loads(body.decode("utf-8", "replace")).get('data', '')
                if html_file_url in block_data:
                    return existing_id
        except Exception:
            pass

    referer = f"{studio_url}/course/{course_id}"
    status, body = session.studio_post_json(f"{studio_url}/xblock/", {
        "parent_locator": parent_locator,
        "category": category,
        "display_name": display_name,
    }, referer)
    if status >= 400:
        _fail(session, f"Creation du bloc '{display_name}' ({category})", status, body)
    try:
        locator = json.loads(body.decode("utf-8", "replace"))["locator"]
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
        raise PublishError(f"Reponse inattendue de Studio a la creation de '{display_name}' : {e} "
                            f"-- {_body_excerpt(body)}")

    if data is not None:
        status, body2 = session.studio_post_json(f"{studio_url}/xblock/{locator}",
                                                   {"data": data, "metadata": {}}, referer)
        if status >= 400:
            _fail(session, f"Enregistrement du contenu de '{display_name}'", status, body2)

    if parent_locator in blocks_by_id:
        blocks_by_id[parent_locator].setdefault('children', []).append(locator)
    else:
        blocks_by_id[parent_locator] = {'children': [locator]}
        
    if parent_locator not in LOCAL_BLOCK_CACHE:
        LOCAL_BLOCK_CACHE[parent_locator] = blocks_by_id.get(parent_locator, {'children': []}).copy()
    else:
        LOCAL_BLOCK_CACHE[parent_locator].setdefault('children', []).append(locator)
        
    new_block = {
        'id': locator,
        'display_name': display_name,
        'type': category,
        'children': []
    }
    blocks_by_id[locator] = new_block
    LOCAL_BLOCK_CACHE[locator] = new_block
            
    return locator

def reorder_children(session, studio_url, course_id, parent_locator, desired_child_order, blocks_by_id):
    parent = blocks_by_id.get(parent_locator)
    if not parent: return
    existing_children = parent.get('children', [])
    final_order = list(desired_child_order)
    for c in existing_children:
        if c not in final_order: final_order.append(c)
    if final_order != existing_children:
        referer = f"{studio_url}/course/{course_id}"
        try:
            status, body = session.studio_post_json(f"{studio_url}/xblock/{parent_locator}", {"children": final_order}, referer)
            if status < 400:
                parent['children'] = final_order
                if parent_locator in LOCAL_BLOCK_CACHE: 
                    LOCAL_BLOCK_CACHE[parent_locator]['children'] = final_order
        except Exception:
            pass


def publish_block(session, studio_url, course_id, locator):
    referer = f"{studio_url}/course/{course_id}"
    status, body = session.studio_post_json(f"{studio_url}/xblock/{locator}", {"publish": "make_public"}, referer)
    if status >= 400:
        _fail(session, f"Publication du bloc {locator}", status, body)


def upload_asset(session, studio_url, course_id, filename, content_bytes):
    referer = f"{studio_url}/course/{course_id}"
    content_type = mimetypes.guess_type(filename)[0] or "text/html"
    if content_type == "text/html":
        content_type = "text/html; charset=utf-8"
    status, body = session.studio_post_multipart_file(
        f"{studio_url}/assets/{course_id}/", "file", filename, content_bytes, content_type, referer
    )
    if status >= 400:
        _fail(session, f"Upload de l'asset '{filename}'", status, body)
    return body


# ---------------------------------------------------------------------------
# Gabarit du <problem> JSInput -- identique a server (1).js / last.xml
# ---------------------------------------------------------------------------

def xml_escape(value):
    return (
        str(value or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def build_problem_xml(display_name, html_file_url):
    return f'''<problem display_name="{xml_escape(display_name)}">
<script type="text/javascript">
(function(){{
window.addEventListener("message", function(e){{
if(!e.data) return;
if(e.data.type == "iframeResize") {{
document.querySelectorAll("iframe").forEach(function(f){{
if(f.contentWindow === e.source) {{
f.style.height = e.data.height;
}}
}});
}}
}});
}})();
</script>

<script type="loncapa/python">
<![CDATA[
import json

def check_function(e, ans):
    try:
        r = json.loads(ans)
        ans_data = r.get("answer", "{{}}")
        if isinstance(ans_data, str):
            try:
                payload = json.loads(ans_data)
            except Exception:
                payload = ans_data
        else:
            payload = ans_data
        if isinstance(payload, dict):
            g = float(payload.get("grade", 0.0))
            score = payload.get("score", 0)
            total = payload.get("total", 0)
        else:
            g = float(payload)
            score = int(g * 100)
            total = 100
        g = max(0.0, min(1.0, g))
        is_ok = False
        if g - 0.5 > 0:
            is_ok = True
        percentage = int(round(g * 100))
        status_word = "Valide" if is_ok else "Echec"
        dynamic_msg = "Score: {{}}% ({{}}/{{}}) {{}}".format(percentage, score, total, status_word)
        return {{"ok": is_ok, "grade_decimal": g, "msg": dynamic_msg}}
    except Exception as ex:
        return {{"ok": False, "grade_decimal": 0.0, "msg": "Erreur Python interne: " + str(ex)}}
]]>
</script>

<customresponse cfn="check_function">
  <jsinput
    gradefn="MonExercice.getGrade"
    get_statefn="MonExercice.getState"
    set_statefn="MonExercice.setState"
    initial_state='{{"answers": {{}}}}'
    width="100%"
    height="530"
    html_file="{xml_escape(html_file_url)}"
  />
</customresponse>
</problem>'''


def is_gradable_exercice(decoded_html):
    return "MonExercice" in decoded_html


def label_for_type(catalogue, type_key):
    """Port de labelForType() (app.html) : libelle humain d'un type d'exercice du catalogue,
    utilise pour les noms affiches dans Studio -- sans ca, les blocs s'appelleraient par leur
    cle technique (ex. 'texte_a_trous') plutot que leur libelle ('Texte a trous')."""
    if type_key == "import_externe":
        return "Exercice importe"
    for cat in catalogue or []:
        if cat.get("key") == type_key:
            return cat.get("label") or type_key
    return type_key


# ---------------------------------------------------------------------------
# Arbre de cours (Unite > Section > Titre > exercices) -- meme regroupement que
# buildOlxPackage()/groupProgramme() cote app.html, porte en Python car le contenu
# (HTML des exercices, credentials Studio) ne doit jamais transiter que cote serveur.
# ---------------------------------------------------------------------------

OLX_SUJET_ORDER = ["Grammaire", "Conjugaison", "Lecture", "Lexique", "Orthographe", "orthographe",
                    "Production ecrite", "production ecrite", "Oral"]


def _numero(text):
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 999


def _sujet_rang(sujet):
    try:
        return OLX_SUJET_ORDER.index(sujet)
    except ValueError:
        return len(OLX_SUJET_ORDER)


def build_course_tree(rows):
    """rows : lignes de programme deja filtrees (matiere/niveau/unite/section) et ne
    contenant que celles avec au moins un exercice 'genere'. Retourne une liste ordonnee de
    chapitres, chacun avec ses sequentiels puis verticaux, meme structure que buildOlxPackage."""
    par_unite = {}
    for row in rows:
        gen = [ex for ex in row.get("exercices", []) if ex.get("statut") == "genere"]
        if not gen:
            continue
        u = row.get("unite", "")
        par_unite.setdefault(u, {"theme": row.get("theme", ""), "sections": {}})
        sec = row.get("section", "")
        par_unite[u]["sections"].setdefault(sec, {"sousTheme": row.get("sousTheme", ""), "items": []})
        par_unite[u]["sections"][sec]["items"].append((row, gen))

    chapters = []
    for unite in sorted(par_unite.keys(), key=_numero):
        udata = par_unite[unite]
        sequentials = []
        for section in sorted(udata["sections"].keys(), key=_numero):
            sdata = udata["sections"][section]
            items = sorted(sdata["items"], key=lambda item: _sujet_rang(item[0].get("sujet", "")))
            verticals = []
            for row, gen in items:
                verticals.append({
                    "displayName": f"{row.get('sujet', '')} — {row.get('titre', '')}",
                    "row": row,
                    "exercices": gen,
                })
            seq_label = f"{section} — {sdata['sousTheme']}" if sdata["sousTheme"] else section
            sequentials.append({"displayName": seq_label, "verticals": verticals})
        chapters.append({"displayName": udata["theme"] or unite, "sequentials": sequentials})
    return chapters


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def publish_course(rows, catalogue, opts, progress_cb=lambda msg: None):
    """rows : lignes de programme deja filtrees par matiere/niveau/unite/section (voir
    server.py process_publish_job). catalogue : liste {key, label} pour les libelles de type
    d'exercice. opts : {org, course, run, displayName, language, platformUrl}. Retourne un
    resume {chapters, sequentials, verticals, gradable, html}."""
    import generate_local  # colocalise, evite un import circulaire au chargement du module

    lms_url, studio_url, email, password = get_config()
    org, course, run = opts["org"], opts["course"], opts["run"]
    course_id = f"course-v1:{org}+{course}+{run}"
    # parent_locator attendu par /xblock/ pour un enfant direct du cours : la usage-key du bloc
    # racine "course" (block-v1:...), PAS la course-key (course-v1:...) utilisee pour les URLs
    # /course/<id> -- voir root = `block-v1:${resolvedId.replace('course-v1:', '')}+type@course+block@course`
    # dans server (1).js.
    course_block_locator = f"block-v1:{org}+{course}+{run}+type@course+block@course"
    platform_url = opts["platformUrl"].rstrip("/")

    tree = build_course_tree(rows)
    if not tree:
        raise PublishError("Aucun exercice genere trouve pour ce perimetre -- rien a publier.")

    progress_cb(f"Connexion a {lms_url}...")
    session = StudioSession()
    login(session, lms_url, studio_url, email, password, course_id)
    progress_cb(f"Session Studio etablie{' (JWT obtenu)' if session.jwt else ''}.")

    progress_cb("Recuperation de l'arborescence du cours...")
    blocks_by_id = fetch_course_blocks(session, lms_url, course_id, email)

    used_asset_names = set()
    counts = {"chapters": 0, "sequentials": 0, "verticals": 0, "gradable": 0, "html": 0}
    
    ordered_ch_locs = []

    for chapter in tree:
        progress_cb(f"Chapitre « {chapter['displayName']} »...")
        chapter_locator = find_or_create_block(session, studio_url, course_id, blocks_by_id, course_block_locator, "chapter", chapter["displayName"])
        if chapter_locator: ordered_ch_locs.append(chapter_locator)
        counts["chapters"] += 1

        ordered_sec_locs = []
        for sequential in chapter["sequentials"]:
            seq_locator = find_or_create_block(session, studio_url, course_id, blocks_by_id, chapter_locator, "sequential", sequential["displayName"])
            if seq_locator: ordered_sec_locs.append(seq_locator)
            counts["sequentials"] += 1

            ordered_unit_locs = []
            for vertical in sequential["verticals"]:
                vert_locator = find_or_create_block(session, studio_url, course_id, blocks_by_id, seq_locator, "vertical", vertical["displayName"])
                if vert_locator: ordered_unit_locs.append(vert_locator)
                counts["verticals"] += 1
                row = vertical["row"]

                ordered_prob_locs = []
                
                temp_exercices = []
                for ex in vertical["exercices"]:
                    temp_exercices.append(ex)

                def sort_key(ex):
                    kind = ex.get("kind", "exercice")
                    if kind == "lecon": return 1
                    if kind == "video": return 2
                    return 3
                sorted_exercices = sorted(temp_exercices, key=sort_key)
                
                for i, ex in enumerate(sorted_exercices):
                    is_pdf = ex.get("isPdf", False)
                    if not is_pdf:
                        decoded = generate_local.b64_to_utf8(ex["contenuB64"])
                    
                    display_name_ex = f"{row.get('titre', '')} — {label_for_type(catalogue, ex.get('type', ''))}"

                    if ex.get("isYoutube") and ex.get("youtubeId"):
                        video_xml = f'<video display_name="{display_name_ex}" youtube="1.00:{ex["youtubeId"]}" />'
                        prob_loc = find_or_create_block(session, studio_url, course_id, blocks_by_id, vert_locator, "video", display_name_ex, data=video_xml)
                        if prob_loc: ordered_prob_locs.append(prob_loc)
                        if "video" not in counts: counts["video"] = 0
                        counts["video"] += 1
                        continue

                    base_name = re.sub(r"[^\w]", "_", display_name_ex, flags=re.UNICODE)[:70]
                    import time
                    timestamp = int(time.time())
                    
                    if is_pdf:
                        asset_name, suffix = f"{base_name}_pdf_{timestamp}", 2
                        while asset_name in used_asset_names:
                            asset_name = f"{base_name}_pdf_{timestamp}_{suffix}"
                            suffix += 1
                        used_asset_names.add(asset_name)
                        filename = f"{asset_name}.pdf"

                        progress_cb(f"  Upload asset PDF : {filename}")
                        import base64
                        pdf_bytes = base64.b64decode(ex["pdfBase64"])
                        upload_asset(session, studio_url, course_id, filename, pdf_bytes)
                        pdf_file_url = f"{platform_url}/asset-v1:{org}+{course}+{run}+type@asset+block@{filename}"
                        
                        html_iframe_data = f'<iframe src="{pdf_file_url}" width="100%" height="800" frameborder="0"></iframe>'
                        prob_loc = find_or_create_block(session, studio_url, course_id, blocks_by_id, vert_locator, "html", display_name_ex, data=html_iframe_data)
                        if prob_loc: ordered_prob_locs.append(prob_loc)
                        counts["html"] += 1
                        continue

                    asset_name, suffix = f"{base_name}_{timestamp}", 2
                    while asset_name in used_asset_names:
                        asset_name = f"{base_name}_{timestamp}_{suffix}"
                        suffix += 1
                    used_asset_names.add(asset_name)
                    filename = f"{asset_name}.html"

                    progress_cb(f"  Upload asset : {filename}")
                    html_bytes = decoded.encode("utf-8")
                    if not html_bytes.startswith(b'\xef\xbb\xbf'):
                        html_bytes = b'\xef\xbb\xbf' + html_bytes
                    upload_asset(session, studio_url, course_id, filename, html_bytes)
                    html_file_url = f"{platform_url}/asset-v1:{org}+{course}+{run}+type@asset+block@{filename}"

                    if is_gradable_exercice(decoded):
                        xml_data = build_problem_xml(display_name_ex, html_file_url)
                        prob_loc = find_or_create_block(session, studio_url, course_id, blocks_by_id, vert_locator, "problem", display_name_ex, data=xml_data)
                        if prob_loc: ordered_prob_locs.append(prob_loc)
                        counts["gradable"] += 1
                    else:
                        html_iframe_data = f'<iframe id="lesson_iframe_{timestamp}" src="{html_file_url}" width="100%" height="530" frameborder="0" scrolling="no" style="overflow: hidden;"></iframe><script>window.addEventListener("message", function(e){{if(e.data && e.data.type === "iframeResize") {{document.querySelectorAll("iframe").forEach(function(f){{if(f.contentWindow === e.source) {{f.style.height = e.data.height;}}}});}}}});</script>'
                        prob_loc = find_or_create_block(session, studio_url, course_id, blocks_by_id, vert_locator, "html", display_name_ex, data=html_iframe_data)
                        if prob_loc: ordered_prob_locs.append(prob_loc)
                        counts["html"] += 1
                reorder_children(session, studio_url, course_id, vert_locator, ordered_prob_locs, blocks_by_id)
            reorder_children(session, studio_url, course_id, seq_locator, ordered_unit_locs, blocks_by_id)

        progress_cb(f"Publication du chapitre « {chapter['displayName']} »...")
        reorder_children(session, studio_url, course_id, chapter_locator, ordered_sec_locs, blocks_by_id)
        publish_block(session, studio_url, course_id, chapter_locator)

    reorder_children(session, studio_url, course_id, course_block_locator, ordered_ch_locs, blocks_by_id)
    progress_cb("Publication terminee.")
    return counts
