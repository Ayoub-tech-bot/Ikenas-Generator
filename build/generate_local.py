#!/usr/bin/env python3
"""
Traite un plan.json exporte par app.html :
- exercices statut == 'a_generer'                 -> generation complete (promptTemplate)
- exercices avec demandeModification non vide      -> regeneration/modification (repart du HTML deja genere)

Fournisseurs pris en charge via --backend :
  cli        : CLI `claude` local (par defaut) — usage compte dans l'abonnement Claude Code
  anthropic  : API Anthropic (sdk python, ANTHROPIC_API_KEY)
  openai     : API OpenAI (openai sdk, OPENAI_API_KEY)
  gemini     : API Google Gemini (google-generativeai sdk, GOOGLE_API_KEY)
  mistral    : API Mistral (mistralai sdk, MISTRAL_API_KEY)
  grok       : API xAI Grok (compatible OpenAI sdk, base_url api.x.ai, XAI_API_KEY)
  kimi       : API Moonshot AI Kimi (compatible OpenAI sdk, base_url api.moonshot.ai, MOONSHOT_API_KEY)

Usage:
  python build/generate_local.py plan.json --check-only
  python build/generate_local.py plan.json --backend anthropic --model claude-opus-4-5
  python build/generate_local.py plan.json --backend openai    --model gpt-4o
  python build/generate_local.py plan.json --backend gemini    --model gemini-2.5-flash
  python build/generate_local.py plan.json --backend mistral   --model mistral-large-latest
  python build/generate_local.py plan.json --backend grok      --model grok-4-1-fast
  python build/generate_local.py plan.json --backend kimi      --model kimi-k2.5
"""
import argparse
import base64
import datetime
import json
import shutil
import subprocess
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Plusieurs sdks (openai, anthropic, mistralai) reposent sur httpx, qui verifie les certificats TLS
# avec sa propre liste figee (certifi) plutot que le magasin de certificats de l'OS. Sur un poste
# avec un antivirus/proxy qui inspecte le HTTPS (root CA installee et fiable pour Windows mais
# absente de certifi), cela echoue systematiquement avec "Connection error." / CERTIFICATE_VERIFY_
# FAILED, meme avec une cle API valide -- confirme en test reel sur ce projet (backend Grok). Best-
# effort : si truststore n'est pas installe, on continue sans (au pire, meme comportement qu'avant).
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

DEFAULT_API_MODEL = "claude-opus-4-5"
MAX_TOKENS = 16000
CLI_TIMEOUT_SECONDS = 600

# Default models per provider
DEFAULT_MODELS = {
    "cli":       None,
    "anthropic": "claude-opus-4-5",
    "openai":    "gpt-4o",
    "gemini":    "gemini-2.5-flash",
    "mistral":   "mistral-large-latest",
    "grok":      "grok-4-1-fast",
    "kimi":      "kimi-k2.5",
}


def b64_to_utf8(b64):
    return base64.b64decode(b64).decode("utf-8")


def utf8_to_b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def label_for_type(catalogue, type_key):
    for cat in catalogue:
        if cat.get("key") == type_key:
            return cat.get("label", type_key)
    return type_key


def find_variante_html(catalogue, type_key, fichier):
    for cat in catalogue:
        if cat.get("key") != type_key:
            continue
        for v in cat.get("variantes", []):
            if v.get("fichier") == fichier:
                return b64_to_utf8(v["b64"])
    return ""


def autres_sujets_section(programme, row):
    """Autres points de programme (sujet + titre) planifies dans la meme section (meme
    niveau/unite/section), hors la ligne courante. Reproduit exactement
    autresSujetsSectionJs() d'app.html."""
    siblings = [
        r for r in programme
        if r.get("id") != row.get("id")
        and r.get("niveau") == row.get("niveau")
        and r.get("unite") == row.get("unite")
        and r.get("section") == row.get("section")
    ]
    if not siblings:
        return "Aucun autre sujet planifié dans cette section pour le moment."
    return "\n".join(f"{s.get('sujet', '')} — {s.get('titre', '')}" for s in siblings)


def render_prompt_full(template, row, ex, reference_html, catalogue, programme=()):
    values = {
        "niveau": row.get("niveau", ""),
        "unite": row.get("unite", ""),
        "theme": row.get("theme", ""),
        "section": row.get("section", ""),
        "sous_theme": row.get("sousTheme", ""),
        "sujet": row.get("sujet", ""),
        "titre": row.get("titre", ""),
        "objectif": row.get("description", "") or "",
        "type_exercice": label_for_type(catalogue, ex.get("type")),
        "modele_reference": reference_html,
        "autres_sujets_section": autres_sujets_section(programme, row),
    }
    prompt = template or ""
    for key, value in values.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    return prompt


def has_monexercice(html):
    return "MonExercice" in html


def has_jschannel(html):
    return "Channel.build" in html and "jschannel.js" in html


def inject_pdf_button(html):
    if 'id="pdf-download-btn"' in html and 'window.open' in html:
        return html
    
    # Remove old button if exists
    import re
    html = re.sub(r'<div id="pdf-download-btn".*?</div>', '', html, flags=re.DOTALL)
    
    button_html = """
    <div id="pdf-download-btn" style="text-align: center; margin: 30px 0;">
        <button onclick="var w=window.open(window.location.href.split('?')[0]+'?print=1', '_blank');" style="background-color: #2c3e50; color: white; padding: 10px 20px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; display: inline-flex; align-items: center; gap: 8px;">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
            Télécharger en PDF / Imprimer
        </button>
        <script>
            if (window.location.search.indexOf('print=1') !== -1) {
                window.onload = function() { window.print(); }
            }
        </script>
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


def inject_resize_script(html):
    if 'type: "iframeResize"' in html or "type: 'iframeResize'" in html:
        return html
    
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
  if (window.ResizeObserver) {
      new ResizeObserver(sendHeight).observe(document.body);
  }
</script>
    """
    if '</body>' in html:
        return html.replace('</body>', script_html + '\n</body>')
    return html + '\n' + script_html


def extract_html(text):
    """Strip markdown code fences if the model wrapped the HTML in ```html ... ```."""
    import re
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        
    # Ensure UTF-8 charset is present for Open edX
    if not re.search(r'<meta\s+charset=[\'"]?utf-8[\'"]?\s*/?>', text, re.IGNORECASE):
        head_match = re.search(r'<head.*?>', text, re.IGNORECASE)
        if head_match:
            idx = head_match.end()
            text = text[:idx] + '\n<meta charset="utf-8">' + text[idx:]
        else:
            html_match = re.search(r'<html.*?>', text, re.IGNORECASE)
            if html_match:
                idx = html_match.end()
                text = text[:idx] + '\n<head>\n<meta charset="utf-8">\n</head>' + text[idx:]
            else:
                text = '<meta charset="utf-8">\n' + text
                
    return inject_resize_script(inject_pdf_button(text))


# ---------------------------------------------------------------------------
# Backend : Claude CLI
# ---------------------------------------------------------------------------

def call_claude_cli(prompt, model=None, timeout=CLI_TIMEOUT_SECONDS):
    """Backend par defaut : passe par le CLI `claude` deja authentifie sur cette machine."""
    import shutil
    cli_path = shutil.which("claude")
    if not cli_path:
        raise RuntimeError("La commande 'claude' (CLI) est introuvable dans le PATH. Veuillez l'installer ou utiliser un autre moteur dans les paramètres.")
    
    cmd = [cli_path, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
        
    import os
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        shell=(os.name == 'nt')  # shell=True is often required on Windows for .cmd/.bat scripts
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI a quitte avec le code {result.returncode} : {result.stderr.strip()[:300]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"reponse du CLI illisible en JSON : {e} ; brut : {result.stdout[:300]}")
    if payload.get("is_error"):
        raise RuntimeError(f"le CLI a signale une erreur : {str(payload.get('result'))[:300]}")
    html = extract_html(payload.get("result", ""))
    cost = payload.get("total_cost_usd")
    return html, cost


# ---------------------------------------------------------------------------
# Backend : Anthropic API
# ---------------------------------------------------------------------------

def call_claude_api(client, prompt, model):
    """Appel direct a l'API Anthropic (facture au token)."""
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()
    text = "".join(b.text for b in message.content if b.type == "text")
    return extract_html(text), None


def call_anthropic_api(api_key, prompt, model):
    """Wrapper with key injection (used by server.py per-request)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    return call_claude_api(client, prompt, model)


# ---------------------------------------------------------------------------
# Backend : OpenAI API
# ---------------------------------------------------------------------------

def call_openai_api(api_key, prompt, model):
    """Appel direct a l'API OpenAI."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("pip install openai est requis pour le backend OpenAI")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content or ""
    return extract_html(text), None


# ---------------------------------------------------------------------------
# Backend : Google Gemini API
# ---------------------------------------------------------------------------

def call_gemini_api(api_key, prompt, model):
    """Appel direct a l'API Google Gemini."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("pip install google-generativeai est requis pour le backend Gemini")
    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(max_output_tokens=MAX_TOKENS),
    )
    text = response.text or ""
    return extract_html(text), None


# ---------------------------------------------------------------------------
# Backend : Mistral AI API
# ---------------------------------------------------------------------------

def call_mistral_api(api_key, prompt, model):
    """Appel direct a l'API Mistral AI."""
    try:
        from mistralai import Mistral
    except ImportError:
        raise RuntimeError("pip install mistralai est requis pour le backend Mistral")
    client = Mistral(api_key=api_key)
    response = client.chat.complete(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content or ""
    return extract_html(text), None


# ---------------------------------------------------------------------------
# Backend : xAI Grok API (compatible OpenAI sdk, base_url different)
# ---------------------------------------------------------------------------

def call_grok_api(api_key, prompt, model):
    """Appel direct a l'API xAI Grok — meme sdk `openai`, juste un base_url different
    (l'API Grok est documentee comme compatible avec le sdk/format OpenAI)."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("pip install openai est requis pour le backend Grok (meme sdk, base_url different)")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content or ""
    return extract_html(text), None


def call_grok_image(api_key, model, prompt):
    """Generation d'image via l'API Grok (endpoint /v1/images/generations, modele grok-2-image) —
    utilise en illustration alternative a Nano Banana. Retourne (mime_type, b64_data)."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("pip install openai est requis pour l'illustration Grok (meme sdk, base_url different)")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    response = client.images.generate(model=model or "grok-2-image", prompt=prompt, response_format="b64_json")
    image = response.data[0]
    b64_json = getattr(image, "b64_json", None)
    if b64_json:
        return "image/jpeg", b64_json
    # Repli : certaines versions de l'API ne renvoient qu'une url meme si b64_json est demande —
    # on la telecharge nous-memes cote serveur (aucun souci de CORS ici, contrairement au navigateur).
    url = getattr(image, "url", None)
    if not url:
        raise RuntimeError("Aucune image renvoyee par Grok (ni b64_json, ni url).")
    with urllib.request.urlopen(url, timeout=60) as resp:
        raw = resp.read()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
    return content_type, base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# Backend : Moonshot AI Kimi API (compatible OpenAI sdk, base_url different)
# ---------------------------------------------------------------------------

def call_kimi_api(api_key, prompt, model):
    """Appel direct a l'API Moonshot AI (Kimi) — meme sdk `openai`, juste un base_url different."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("pip install openai est requis pour le backend Kimi (meme sdk, base_url different)")
    client = OpenAI(api_key=api_key, base_url="https://api.moonshot.ai/v1")
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content or ""
    return extract_html(text), None


# ---------------------------------------------------------------------------
# Unified dispatcher (used by server.py)
# ---------------------------------------------------------------------------

def call_provider(provider, api_key, prompt, model):
    """Route a generation request to the correct provider."""
    provider = (provider or "anthropic").lower()
    model = model or DEFAULT_MODELS.get(provider) or DEFAULT_API_MODEL

    if provider == "anthropic":
        return call_anthropic_api(api_key, prompt, model)
    elif provider == "openai":
        return call_openai_api(api_key, prompt, model)
    elif provider == "gemini":
        return call_gemini_api(api_key, prompt, model)
    elif provider == "mistral":
        return call_mistral_api(api_key, prompt, model)
    elif provider == "grok":
        return call_grok_api(api_key, prompt, model)
    elif provider == "kimi":
        return call_kimi_api(api_key, prompt, model)
    else:
        raise ValueError(f"Fournisseur inconnu : {provider}")


# ---------------------------------------------------------------------------
# CLI batch mode helpers
# ---------------------------------------------------------------------------

def collect_pending(plan):
    pending = []
    for row in plan.get("programme", []):
        for ex in row.get("exercices", []):
            if ex.get("statut") == "a_generer":
                pending.append((row, ex, "generation"))
            elif ex.get("statut") == "genere" and ex.get("demandeModification", "").strip():
                pending.append((row, ex, "modification"))
    return pending


def report_check(plan):
    catalogue = plan.get("catalogue", [])
    total_generated = 0
    gradable = 0
    legacy = 0
    pending_gen = 0
    pending_mod = 0
    rows_gradable = []
    rows_legacy = []
    for row in plan.get("programme", []):
        for ex in row.get("exercices", []):
            label = f"{row.get('niveau')} / {row.get('unite')} / {row.get('sujet')} / {row.get('titre')} -- {label_for_type(catalogue, ex.get('type'))}"
            if ex.get("statut") == "a_generer":
                pending_gen += 1
            if ex.get("statut") == "genere" and ex.get("demandeModification", "").strip():
                pending_mod += 1
            if ex.get("statut") == "genere" and ex.get("contenuB64"):
                total_generated += 1
                html = b64_to_utf8(ex["contenuB64"])
                mx = has_monexercice(html)
                jc = has_jschannel(html)
                if mx and jc:
                    gradable += 1
                    rows_gradable.append(label)
                else:
                    legacy += 1
                    marker = []
                    if not mx:
                        marker.append("pas de MonExercice")
                    if not jc:
                        marker.append("pas de jschannel")
                    rows_legacy.append(f"{label}  [{', '.join(marker)}]")

    print(f"Total exercices generes            : {total_generated}")
    print(f"  - grade (MonExercice + jschannel) : {gradable}")
    print(f"  - legacy (html simple)            : {legacy}")
    print(f"En attente de generation (a_generer)      : {pending_gen}")
    print(f"En attente de modification (demandeModif) : {pending_mod}")
    if rows_legacy:
        print("\n--- Legacy (pas encore compatibles jsinput) ---")
        for r in rows_legacy:
            print(" -", r)
    if rows_gradable:
        print("\n--- Deja compatibles jsinput ---")
        for r in rows_gradable:
            print(" -", r)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", help="Chemin vers plan.json exporte depuis app.html")
    parser.add_argument("--out", help="Fichier de sortie (defaut : ecrase le fichier d'entree, avec backup .bak-<timestamp>)")
    parser.add_argument("--backend", choices=["cli", "anthropic", "openai", "gemini", "mistral", "grok", "kimi", "api"],
                         default="cli",
                         help="Fournisseur IA a utiliser (defaut: cli)")
    parser.add_argument("--model", default=None, help="Modele a utiliser (defaut selon le fournisseur choisi)")
    parser.add_argument("--api-key", default=None, help="Cle API (sinon lue depuis la variable d'environnement)")
    parser.add_argument("--check-only", action="store_true", help="Rapport uniquement, pas de generation")
    parser.add_argument("--dry-run", action="store_true", help="Affiche ce qui serait traite, sans generer")
    parser.add_argument("--limit", type=int, default=None, help="Limiter a N exercices (test)")
    args = parser.parse_args()

    # Backward compat: --backend api -> anthropic
    if args.backend == "api":
        args.backend = "anthropic"

    with open(args.plan, "r", encoding="utf-8") as f:
        plan = json.load(f)

    if args.check_only:
        report_check(plan)
        return

    pending = collect_pending(plan)
    if args.limit is not None:
        pending = pending[: args.limit]

    if not pending:
        print("Rien a generer ou modifier.")
        return

    print(f"Backend : {args.backend}")
    print(f"{len(pending)} exercice(s) a traiter :")
    for row, ex, kind in pending:
        print(f"  [{kind}] {row.get('titre')} -- {ex.get('type')}")

    if args.dry_run:
        return

    catalogue = plan.get("catalogue", [])
    template = plan.get("promptTemplate", "")
    today = datetime.date.today().isoformat()
    n_ok, n_fail = 0, 0

    for row, ex, kind in pending:
        label = f"{row.get('titre')} -- {ex.get('type')}"
        try:
            if kind == "generation":
                reference_html = find_variante_html(catalogue, ex.get("type"), ex.get("variante"))
                prompt = render_prompt_full(template, row, ex, reference_html, catalogue, plan.get("programme", []))
            else:
                existing_html = b64_to_utf8(ex["contenuB64"])
                demande = ex["demandeModification"].strip()
                prompt = f"{demande}\n\n---\n\nFichier HTML actuel a modifier :\n\n{existing_html}"

            print(f"-> {label} ({kind})...", end=" ", flush=True)

            if args.backend == "cli":
                new_html, cost = call_claude_cli(prompt, model=args.model)
            else:
                api_key = args.api_key  # may be None (SDK reads from env)
                model = args.model or DEFAULT_MODELS.get(args.backend)
                new_html, cost = call_provider(args.backend, api_key, prompt, model)

            if not new_html.startswith("<!DOCTYPE") and not new_html.startswith("<html"):
                print("ECHEC (reponse ne commence pas par <!DOCTYPE/<html)")
                n_fail += 1
                continue

            if kind == "modification" and ex.get("contenuB64"):
                ex.setdefault("historique", []).append(
                    {"contenuB64": ex["contenuB64"], "genereLe": ex.get("genereLe")}
                )

            ex["contenuB64"] = utf8_to_b64(new_html)
            ex["statut"] = "genere"
            ex["genereLe"] = today
            ex["demandeModification"] = ""

            mx, jc = has_monexercice(new_html), has_jschannel(new_html)
            status = "OK" if (mx and jc) else f"OK mais incomplet (MonExercice={mx}, jschannel={jc})"
            print(status)
            n_ok += 1
        except Exception as e:
            print(f"ECHEC ({e})")
            n_fail += 1

    print(f"\n{n_ok} reussi(s), {n_fail} echec(s).")

    out_path = args.out or args.plan
    if not args.out:
        backup = f"{args.plan}.bak-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy(args.plan, backup)
        print(f"Sauvegarde de l'original : {backup}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"Ecrit : {out_path}")


if __name__ == "__main__":
    main()
