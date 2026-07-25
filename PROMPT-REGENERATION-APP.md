# Prompt de régénération — Générateur d'exercices Salma (`app.html`)

Ce document décrit intégralement l'application `app.html` (générateur/planificateur d'exercices scolaires) telle qu'elle existe aujourd'hui, pour permettre de la reconstruire à l'identique sur une autre machine (nouvelle session Claude Code sans accès à l'historique de conversation).

**Meilleure méthode** : copier directement le fichier `app.html` (un seul fichier autonome, ~1,4 Mo) — c'est plus fiable qu'une reconstruction par IA. Ce document sert pour deux cas : (a) documenter précisément ce que fait l'app pour pouvoir la faire évoluer ailleurs, (b) si le fichier n'est pas disponible et qu'il faut la reconstruire à partir de zéro via un prompt. **Dans le cas (b), fournir aussi le fichier `app.html` actuel comme référence si possible** — ce prompt seul ne garantit pas un rendu visuel pixel-perfect, seulement les mêmes données/comportements/algorithmes.

---

## 1. Vue d'ensemble

Un seul fichier HTML auto-suffisant (CSS + JS inline, aucun serveur requis, ouverture directe dans le navigateur via `file://`). Deux onglets :

- **Planification** : parcourir le programme scolaire, planifier quels exercices générer pour chaque point du programme.
- **Bibliothèque** : parcourir/visualiser/exporter les exercices déjà générés.

### Dépendances externes (CDN, nécessitent internet au moment de l'usage — le reste de l'app fonctionne hors-ligne)

```html
<script src="https://cdn.sheetjs.com/xlsx-0.20.2/package/dist/xlsx.full.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.min.js"></script>
```
- `xlsx` (SheetJS) : import du programme scolaire depuis un `.xlsx`.
- `jszip` : export des cours générés en dossiers `.zip`.
- `pako` : compression gzip pour l'export Open edX `.tar.gz`.

### Persistance

```js
function loadFromLocalStorage() {
  const raw = localStorage.getItem('generateur-cours-plan');
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}
function saveToLocalStorage() {
  localStorage.setItem('generateur-cours-plan', JSON.stringify(state));
}
// RÈGLE CRITIQUE : un plan déjà sauvegardé en localStorage gagne TOUJOURS sur le
// plan embarqué dans le fichier, même après rechargement de page.
let state = loadFromLocalStorage() || structuredClone(PLAN);
```
Bouton **« Recharger depuis le fichier »** → `state = structuredClone(PLAN)` (avec `confirm()` d'avertissement) pour revenir explicitement au contenu embarqué et écraser le localStorage.

---

## 2. Modèle de données (`state`)

```js
state = {
  version: 1,
  programme: [ /* voir 2.1 */ ],
  catalogue: [ /* voir 2.2 */ ],
  promptTemplate: "…", // voir section 5
}
```

### 2.1 `programme[]` — une ligne par point de programme

```js
{
  id: "4eme_unite-1_section-1_grammaire_classes-de-mots-mots-variables-et-mots-invariables",
  niveau: "4ème",              // "2ème" | "3ème" | "4ème" | "5ème" | "6ème" (pas de suffixe "AEP" dans le texte)
  matiere: "Français",         // Matière scolaire — texte libre, "Français" par défaut si absent (DEFAULT_MATIERE,
                                // rétrocompatibilité avec tout le programme existant avant l'introduction des matières).
                                // N'entre dans le calcul de `id` (assignProgrammeIds) que si différent de "Français".
  unite: "Unité 1",
  theme: "La civilisation marocaine",       // thème de l'unité
  section: "Section 1",
  sousTheme: "",                             // sous-thème de la section (peut être vide)
  sujet: "Grammaire",           // Grammaire | Conjugaison | Lecture | Lexique | Orthographe | Production écrite | Oral
  titre: "classes de mots / mots variables et mots  invariables",
  description: "Identifier et utiliser les règles grammaticales de base.",  // = objectif pédagogique
  exercices: [
    {
      type: "qsm",                // clé de state.catalogue[].key
      variante: "QSM1.html",       // = variantes[].fichier du modèle utilisé
      statut: "genere" | "a_generer",
      contenuB64: "<base64 du HTML complet généré>",   // présent seulement si statut === 'genere'
      genereLe: "2026-07-11",
      historique: [ { contenuB64: "...", ... } ],  // versions précédentes (régénérations)
      demandeModification: "texte de la demande en attente"  // optionnel
    }
  ]
}
```

**`id`** = `[niveau, unite, section, sujet, titre].map(slugifyJs).join('_')`, avec suffixe `-2`, `-3`... en cas de collision. Voir `slugifyJs` en section 6.

### 2.2 `catalogue[]` — bibliothèque de modèles HTML réutilisables

```js
[
  {
    key: "choix_unique",              // identifiant stable, utilisé comme exercices[].type
    label: "Choix unique / QCM",      // libellé affiché
    variantes: [
      { fichier: "choix-audio.html", titre: "Exercice 1", b64: "<base64 du HTML complet du modèle>" },
      // ...
    ],
  },
  // 8 catégories dans la version actuelle : choix_unique, coloration, drag_drop,
  // saisie, jeux, mots_caches, qsm, vrai_faux (38 variantes au total)
]
```
Chaque variante est un fichier HTML autonome (CSS/JS inline, éventuellement des imports Google Fonts via CDN) servant de **référence de style et de mécanique interactive** pour la génération IA — pas le contenu final.

---

## 3. Onglet Planification

- Arbre dépliable **Niveau → Unité (thème) → Section (sous-thème) → Titre**, recherche texte libre sur niveau/unité/thème/section/sous-thème/sujet/titre/description.
- Sélection d'un titre → panneau de détail (toutes les métadonnées + liste des exercices déjà planifiés : type, variante, statut avec badge, bouton « Voir le prompt » qui ouvre un aperçu du prompt exact qui serait envoyé, bouton « Retirer »).
- **« + Ajouter un exercice »** — désactivé si `description` est vide (objectif manquant → génération impossible) → ouvre le modal catalogue.
- Statut visuel par titre dans l'arbre : aucun exercice / à générer / généré (dérivé de `exercices[].statut`).

### 3.1 Modal catalogue (« Ajouter un exercice »)

- Colonne gauche : catégories dépliables (`catalogue[].label`) → variantes à cocher, avec suppression individuelle (`✕`) et suppression de catégorie entière (`🗑`, avec confirmation).
- Colonne droite : aperçu live de la variante sélectionnée via `iframe.srcdoc = b64ToUtf8(variante.b64)`.
- Bouton **« + Nouveau modèle »** : formulaire inline pour ajouter un ou plusieurs fichiers `.html` à une catégorie existante ou une nouvelle catégorie.
  - Lecture via `FileReader.readAsText()` (PAS `File.text()` — source de bugs incompatibles selon navigateurs/environnements).
  - Tous les fichiers sont lus et encodés **avant** toute modification de `state.catalogue`, pour ne jamais laisser une catégorie orpheline si un fichier échoue à se lire.
  - Titre par défaut = nom de fichier « prettifié » (`.html` retiré, `-`/`_` → espaces, 1ère lettre capitalisée) ; éditable seulement si un seul fichier est sélectionné à la fois.
  - Nom de fichier dédupliqué dans la catégorie (`nom-2.html`, `nom-3.html`...).
- Cocher plusieurs variantes → **« Ajouter N exercices à ce titre »** (pousse `{type, variante, statut:'a_generer'}` dans `row.exercices`, sans dupliquer si déjà présent).

---

## 4. Onglet Bibliothèque

- Arbre **Niveau → Unité (thème) → Section (sous-thème) → Sujet → Titre**, filtré à `exercices[].statut === 'genere'` uniquement (fonction `groupGenerated`).
- Deux filtres déroulants **Niveau** / **Sujet** (en cascade : la liste des sujets se limite au niveau sélectionné) + recherche texte, en plus des cases à cocher par exercice pour actions groupées.
- Clic sur un exercice → visionneuse (`iframe.srcdoc`), sélecteur de version si plusieurs générations successives, bouton Télécharger / Ouvrir dans un onglet / PDF (pour les types papier-friendly : `choix_unique, vrai_faux, mots_caches, coloration, saisie, qsm`).
- **« ✏️ Modifier les exercices sélectionnés »** et **« 🔄 Régénérer un exercice »** : composent une demande de modification texte, appliquée par Claude au prochain lancement de `build/generate_local.py` (repart du HTML déjà généré, applique uniquement le changement demandé).
- **« 📦 Exporter en dossiers (.zip) »** : si des exercices sont cochés → export uniquement ceux-là ; sinon export scope = filtres actifs (niveau/sujet/recherche) ou tout. Arborescence dans le zip : `Niveau/Unité/Section/Sujet/Titre — Type.html`.
- **« 🎓 Exporter vers Open edX (.tar.gz) »** : voir section 7.

---

## 5. Génération des exercices — le prompt

La génération réelle du contenu **ne se fait pas dans `app.html`** (pas d'appel API intégré) — elle se fait via un script externe (`build/generate_local.py`, hors périmètre de ce document) qui lit `plan.json` exporté et appelle Claude pour chaque exercice à générer, en substituant les variables ci-dessous dans `state.promptTemplate` (fonction `renderPromptJs`, reproduite aussi côté navigateur pour prévisualisation) :

Variables : `{{niveau}} {{unite}} {{theme}} {{section}} {{sous_theme}} {{sujet}} {{titre}} {{objectif}} {{type_exercice}} {{modele_reference}}` (le dernier = HTML complet du modèle choisi, décodé).

**Texte actuel complet du prompt** (`state.promptTemplate`) :

```
Tu es un générateur de contenu pédagogique pour l'enseignement primaire/collège en français. On te donne un modèle HTML de référence (structure, style visuel, mécanique interactive) et les informations d'un point de programme scolaire. Ta tâche : produire un NOUVEAU fichier HTML complet et autonome, qui réutilise exactement la même structure visuelle et la même mécanique interactive que le modèle de référence, mais avec un contenu pédagogique entièrement nouveau et adapté au niveau, au thème et à l'objectif donnés. Réponds UNIQUEMENT avec le code HTML complet du fichier (commençant par <!DOCTYPE html>), sans aucune explication, sans balises markdown, sans commentaire avant ou après.

Modèle HTML de référence :

{{modele_reference}}

---

Génère un nouvel exercice avec ces informations :
- Niveau : {{niveau}}
- Unité / Thème général : {{unite}} — {{theme}}
- Section / Sous-thème : {{section}} — {{sous_theme}}
- Sujet : {{sujet}}
- Titre : {{titre}}
- Objectif pédagogique : {{objectif}}

Type d'exercice : {{type_exercice}}.

Contraintes techniques supplémentaires, obligatoires (intégration Open edX) :
- Calcule en continu un score de progression, un nombre flottant entre 0.0 et 1.0 (proportion de bonnes réponses ou d'étapes complétées).
- Expose sur `window` un objet `MonExercice` avec exactement ces trois fonctions :
  - `MonExercice.getGrade()` : retourne le score actuel (flottant entre 0.0 et 1.0).
  - `MonExercice.getState()` : retourne une chaîne JSON représentant l'état actuel de l'exercice (réponses données, score...), pour pouvoir le restaurer plus tard.
  - `MonExercice.setState(stateJson)` : reçoit une chaîne JSON produite par un getState() précédent et restaure l'exercice dans cet état ; si stateJson est vide ou absent, démarre normalement.
- À chaque changement de hauteur du contenu (chargement initial, ajout de contenu, etc.), envoie ce message au parent :
  `window.parent.postMessage({ type: 'iframeResize', height: document.body.scrollHeight }, '*');`
  (l'exercice sera affiché dans une iframe et ce message permet de l'ajuster automatiquement à la bonne hauteur).
- Ces éléments techniques ne doivent jamais être visibles ni mentionnés dans l'interface visuelle de l'exercice : ils sont uniquement fonctionnels.
```

> Ce contrat technique (`window.MonExercice`) est une **convention déjà utilisée ailleurs** dans le cours Open edX cible (confirmé par l'utilisateur) — ne pas renommer. Le nom exact `MonExercice` (pas `monExercice`, pas `MyExercice`) doit être respecté à la lettre. Voir aussi `openedx-jsinput-prompt.md` (sur le Bureau de l'utilisateur au moment de la rédaction) pour une spec plus détaillée encore côté contenu (convention de nommage des questions par leur texte exact, import de `jschannel.js`, `ANSWERS`/`DRAG_ANSWERS`, `ResizeObserver`) — à consulter si le prompt ci-dessus doit être affiné davantage.

**Important** : les exercices générés **avant** l'ajout de ce contrat n'exposent pas `window.MonExercice` — l'export Open edX les détecte et les traite différemment (voir 7.3).

---

## 6. Import du programme (`.xlsx`)

Colonnes attendues, une ligne par point de programme (fonction `rowsFromSheet`) :

```
Niveau | Matière | Unité | Thème | Section | Sous-thème | Sujet | Titre | Description
```

- `Niveau` est optionnel **si chaque feuille du classeur porte le nom du niveau** (le nom de l'onglet sert de fallback).
- `Matière` est optionnelle — vide ou absente = `"Français"` (`DEFAULT_MATIERE`), pour que tout le programme importé avant l'introduction des matières reste sans changement (mêmes ids : le segment matière n'entre dans le calcul de l'id, voir `assignProgrammeIds`, que lorsque la matière n'est *pas* le Français).
- Une ligne sans `Niveau` (ni fallback) ou sans `Titre` est ignorée silencieusement.
- Toutes les feuilles du classeur sont concaténées, les ids sont attribués sur l'ensemble combiné (`assignProgrammeIds`, dédup par suffixe `-2`, `-3`...).

### Fusion — union, pas remplacement (côté serveur, pas côté client)

La fusion n'est **pas** une fonction JS côté client (contrairement à une version antérieure de ce document) — elle vit dans le handler `POST /api/programme/import` de `build/server.py` (section "Fusion", ~ligne 802) :

```python
# pour chaque ligne acceptée (permissions/niveau/sujet vérifiés) :
#   - id déjà connu -> les métadonnées du nouveau fichier remplacent les anciennes
#     (niveau, matiere, unite, theme, section, sousTheme, sujet, titre, description),
#     mais row["exercices"] (déjà planifiés/générés) n'est JAMAIS écrasé ici ;
#   - id inconnu -> nouvelle ligne ajoutée telle quelle ;
#   - toute ligne existante absente du nouvel import (autre niveau/matière non couvert
#     par le fichier) reste intacte dans le programme stocké.
```
⚠️ Une implémentation naïve qui remplacerait entièrement le programme stocké par le nouveau fichier importé est un bug déjà rencontré : importer un fichier ne couvrant qu'un niveau (ou qu'une matière) effacerait silencieusement tout le reste déjà présent. Toujours implémenter la version union ci-dessus.

---

## 7. Export Open edX (`.tar.gz`, format OLX)

Bouton dans l'onglet Bibliothèque, ouvre un modal de configuration :
- **Niveau à exporter** (liste = niveaux ayant ≥1 exercice généré)
- **Organisation (org)**, **Code du cours (course)**, **Session (run)**, **Nom affiché**, **Langue**, **URL de la plateforme** — tous éditables à chaque export (valeurs par défaut : `ikenas` / `C_Fr0{chiffre du niveau}` / `2025_S1` / `Français {niveau}` / `fr` / `https://plateforme.ikenas.com`), car l'utilisateur veut pouvoir préciser le nom exact du cours cible à chaque fois plutôt qu'une convention figée.
- ⚠️ Avertissement affiché dans le modal : **l'import Studio remplace tout le contenu du cours ciblé**, il ne fusionne pas — recommander un `run` différent pour tester sans toucher un cours existant.

### 7.1 Mapping structure programme → structure Open edX (OLX)

```
chapter    = Unité      (display_name = row.theme, ordonné par le numéro dans "Unité N")
sequential = Sujet      (display_name = row.sujet, ordre canonique :
                          Grammaire, Conjugaison, Lecture, Lexique, Orthographe,
                          Production écrite, Oral, puis alphabétique pour le reste)
vertical   = Titre      (display_name = row.titre, un vertical par row ayant
                          ≥1 exercice généré, ordonné par numéro de "Section N")
problem/html = exercice (un par exercice généré ; voir 7.3 pour le choix du type)
```
Seuls les titres ayant au moins un exercice au statut `genere` sont inclus (unités/sujets vides omis).

### 7.2 Identifiants stables (réimport = mise à jour, pas duplication)

```js
async function stableId(str) {
  const bytes = new TextEncoder().encode(str);
  const hashBuf = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hashBuf)).map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}
// chapter:    stableId(`chapter|${org}|${course}|${run}|${unite}`)
// sequential: stableId(`sequential|${org}|${course}|${run}|${unite}|${sujet}`)
// vertical:   stableId(`vertical|${row.id}`)
// html/problem: stableId(`html|${row.id}|${ex.type}|${ex.variante}|${i}`)  (ou "problem|..." — préfixe différent selon branche)
```
`crypto.subtle` fonctionne depuis `file://` dans Chromium (contexte considéré sécurisé) — vérifié.

### 7.3 Détection gradable vs legacy, par exercice

```js
function isGradableExercice(decodedHtml) {
  return decodedHtml.includes('MonExercice');   // heuristique simple mais suffisante
}
```
- **Gradable** (contient `MonExercice`) → xblock **`problem`** noté (voir 7.4). Le HTML décodé part dans `course/static/{nom}.html` (asset du cours), et `course/problem/{id}.xml` contient le wrapper CAPA. Le vertical référence `<problem url_name="{id}"/>`.
- **Legacy** (pas de contrat) → composant **`html`** simple, non noté, comme avant : `course/html/{id}.xml` (`<html filename="{id}" display_name="…" editor="raw"/>`) + `course/html/{id}.html` (le HTML décodé tel quel). Le vertical référence `<html url_name="{id}"/>`.
- Après génération, l'utilisateur est averti du nombre d'exercices notés vs legacy, pour savoir lesquels regénérer.

### 7.4 Template exact du xblock `problem` (CAPA + jsinput)

```xml
<problem display_name="{DISPLAY_NAME}">
<script type="text/javascript">
(function(){
window.addEventListener("message", function(e){
if(!e.data) return;
if(e.data.type == "iframeResize") {
document.querySelectorAll("iframe").forEach(function(f){
if(f.contentWindow === e.source) {
f.style.height = e.data.height;
}
});
}
});
})();
</script>

<script type="loncapa/python">
<![CDATA[
import json

def check_function(e, ans):
    try:
        r = json.loads(ans)
        ans_data = r.get("answer", "{}")
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
        status_word = "Validé" if is_ok else "Échec"
        dynamic_msg = "Score: {}% ({}/{}) {}".format(percentage, score, total, status_word)
        return {"ok": is_ok, "grade_decimal": g, "msg": dynamic_msg}
    except Exception as ex:
        return {"ok": False, "grade_decimal": 0.0, "msg": "Erreur Python interne: " + str(ex)}
]]>
</script>

<customresponse cfn="check_function">
  <jsinput
    gradefn="MonExercice.getGrade"
    get_statefn="MonExercice.getState"
    set_statefn="MonExercice.setState"
    initial_state='{"answers": {}}'
    width="100%"
    height="530"
    html_file="{HTML_FILE_URL}"
  />
</customresponse>
</problem>
```
`{DISPLAY_NAME}` = `${row.titre} — ${labelForType(ex.type)}` (échappement XML standard : `& < > " '`).

`{HTML_FILE_URL}` construit ainsi :
```js
const htmlFileUrl = `${platformUrl.replace(/\/+$/, '')}/asset-v1:${org}+${course}+${run}+type@asset+block@${assetName}.html`;
```
Exemple réel validé : `https://plateforme.ikenas.com/asset-v1:ikenas+C_Fr02+2025_S1+type@asset+block@Exercices_de_Lecture___QSM.html`

`assetName` (nom de fichier de l'asset, sans extension) — un caractère non alphanumérique-unicode → un underscore, **sans fusionner les underscores consécutifs** (reproduit fidèlement la convention observée dans le cours Open edX existant : `"Quelle est la fonction de GN dans les phrases suivantes ?"` → `Quelle_est_la_fonction_de_GN_dans_les_phrases_suivantes__` — la double underscore finale vient de l'espace + du `?`) :
```js
function assetFilenameFromText(text) {
  return String(text || '').replace(/[^\p{L}\p{N}]/gu, '_');
}
```
Dédupliqué globalement sur tout le package (`nom_2`, `nom_3`...) si collision.

⚠️ **Piège HTML rencontré et à éviter** : ce template JS contient littéralement `</script>` dans une chaîne (template literal). Comme tout ceci est lui-même écrit à l'intérieur d'une balise `<script>` du fichier `app.html`, le parseur HTML (pas le parseur JS) referme prématurément la balise `<script>` englobante au premier `</script>` littéral rencontré, où qu'il soit. **Toujours échapper en `<\/script>`** dans le code source de `app.html` (le `\` est ignoré par le moteur JS, la chaîne produite au final reste bien `</script>`) — vérifié : `` `a<\/script>b` === 'a</script>b' `` en JS.

### 7.5 Reste du package OLX (squelette générique, identique à chaque export)

```
course/course.xml                          <course url_name="{run}" org="{org}" course="{course}"/>
course/course/{run}.xml                    <course display_name="…" language="…" start="2024-01-01T00:00:00Z">
                                              <chapter url_name="…"/>*
                                              <wiki slug="{org}.{course}.{run}"/>
                                            </course>
course/about/*.html                        placeholders par défaut de Studio (vides sauf overview.html générique)
course/assets/assets.xml                   <assets/>
course/policies/assets.json                {}   (Studio indexe course/static/ automatiquement à l'import)
course/policies/{run}/grading_policy.json  barème par défaut de Studio (Homework/Lab/Midterm/Final, Pass=0.5)
course/policies/{run}/policy.json          { "course/{run}": { display_name, language, start, tabs } }
course/info/updates.html                   <ol></ol>
course/chapter/{id}.xml, course/sequential/{id}.xml, course/vertical/{id}.xml   voir 7.1/7.2
course/html/{id}.xml + .html               exercices legacy (7.3)
course/problem/{id}.xml                    exercices notés (7.4)
course/static/{nom}.html                   contenu HTML brut des exercices notés (asset du cours)
```

### 7.6 Empaquetage TAR + GZIP (aucune lib externe pour le TAR, `pako` seulement pour le gzip)

Format **USTAR** (blocs de 512 octets), implémentation maison testée (round-trip vérifié avec `tar`/`gunzip` système) :

```js
function tarOctal(num, len) { return num.toString(8).padStart(len - 1, '0') + '\0'; }
function tarWriteStr(buf, offset, str, maxLen) {
  buf.set(new TextEncoder().encode(str).slice(0, maxLen), offset);
}
function buildTarHeader(path, size, typeflag, mtime) {
  if (new TextEncoder().encode(path).length > 100) throw new Error('Chemin trop long (>100 octets): ' + path);
  const header = new Uint8Array(512);
  tarWriteStr(header, 0, path, 100);                                    // name
  tarWriteStr(header, 100, tarOctal(typeflag === '5' ? 0o755 : 0o644, 8), 8); // mode
  tarWriteStr(header, 108, tarOctal(0, 8), 8);                          // uid
  tarWriteStr(header, 116, tarOctal(0, 8), 8);                          // gid
  tarWriteStr(header, 124, tarOctal(size, 12), 12);                     // size
  tarWriteStr(header, 136, tarOctal(mtime, 12), 12);                    // mtime
  for (let i = 148; i < 156; i++) header[i] = 0x20;                     // chksum = espaces pendant le calcul
  header[156] = typeflag.charCodeAt(0);                                 // '0' = fichier, '5' = dossier
  tarWriteStr(header, 257, 'ustar', 6);
  tarWriteStr(header, 263, '00', 2);
  tarWriteStr(header, 265, 'root', 32);                                 // uname
  tarWriteStr(header, 297, 'root', 32);                                 // gname
  let sum = 0; for (let i = 0; i < 512; i++) sum += header[i];
  tarWriteStr(header, 148, sum.toString(8).padStart(6, '0') + '\0 ', 8);
  return header;
}
// buildTar(entries) : header + contenu (padding zéro au multiple de 512 suivant) par entrée,
// puis deux blocs de 512 zéros en fin d'archive. entries = [{path, content}] ou [{path, isDir:true}].
// gz = pako.gzip(buildTar(entries)); téléchargé en Blob({type:'application/gzip'}).
```
Chemins toujours < 100 octets dans cette app (pas de dossier basé sur un display_name variable, uniquement des segments fixes + ids de 32 caractères hex) → pas besoin de gérer le champ `prefix` USTAR pour les chemins longs.

---

## 8. Détails techniques transverses

```js
// Slugification (ids de programme, filenames dédupliqués, nom de fichier .zip/.tar.gz)
function slugifyJs(text) {
  let t = (text || '').toString().normalize('NFKD');
  t = t.replace(/[^\x00-\x7F]/g, '');   // équivalent de Python encode('ascii','ignore') — enlève tout accent/diacritique
  t = t.toLowerCase();
  t = t.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return t || 'x';
}

// Encodage/décodage UTF-8 <-> base64 (contenu HTML des exercices/modèles)
function b64ToUtf8(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}
function utf8ToB64(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = ''; bytes.forEach(b => binary += String.fromCharCode(b));
  return btoa(binary);
}

// Échappement XML (attributs) — utilisé partout dans l'export Open edX
function xmlEscape(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}
```

- **`FileReader.readAsText()` > `File.text()`** pour l'import de fichiers `.html` (le second a échoué dans un environnement de test headless/sandboxé — pas garanti fiable partout).
- Toujours lire/encoder **avant** de muter `state` en cas d'opération multi-fichiers, pour ne jamais laisser un état partiellement appliqué si une étape échoue.
- Toute portion de code JS générée dynamiquement et contenant du texte HTML doit échapper ses `</script>` littéraux en `<\/script>` si elle est elle-même émise depuis un `<script>` du fichier hôte.

---

## 9. Historique — pourquoi ces choix

- App construite à l'origine à partir d'une spec (`docs/specs/2026-07-10-generateur-exercices-design.md`) pour Salma : programme scolaire (219 lignes, 3 niveaux) + bibliothèque de 38 modèles HTML.
- Étendue ensuite (session postérieure) : programme complet à 5 niveaux (2ème→6ème, 363 lignes), gestion CRUD des modèles dans l'UI (avant : uniquement embarqués au build), export dossiers `.zip`, export Open edX complet avec détection automatique gradable/legacy.
- Le fix `mergeProgramme` (union au lieu de remplacement) corrige un bug réel rencontré : un import de programme partiel effaçait les autres niveaux déjà présents.
