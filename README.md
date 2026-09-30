# Ollama Buddy

Suivi local de ta consommation **Ollama Cloud** : ton quota mensuel en dollars,
la répartition par modèle, l'évolution dans le temps — le tout **en temps réel**,
dans une app macOS avec aperçu permanent dans la barre de menus.

![Aucune donnée ne sort de la machine](https://img.shields.io/badge/donn%C3%A9es-100%25%20locales-199e70)

## Lancer

**En app macOS** :

```bash
./build_app.sh --install     # construit et copie dans /Applications
```

Puis glisse `Ollama Buddy.app` dans le Dock. L'app démarre son serveur, affiche
le tableau de bord dans une fenêtre native, expose un aperçu dans la barre de
menus, et arrête son serveur quand tu quittes.

| Raccourci | Action |
|---|---|
| `⌘1` | Afficher la fenêtre |
| `⌘R` | Recharger |
| `⌘⇧R` | Relire les transcripts |
| `⌘O` | Ouvrir dans le navigateur |
| `⌘Q` | Quitter (arrête le serveur) |

Fermer la fenêtre **ne quitte pas** l'app : elle continue de vivre dans la barre
de menus.

**En ligne de commande** :

```bash
python3 ollama_buddy.py            # serveur + tableau de bord
python3 ollama_buddy.py --once     # indexe et affiche un résumé, sans serveur
```

Aucune dépendance à installer : tout est dans la bibliothèque standard de
Python 3.9+. Pour construire l'app, il faut `swiftc` (Command Line Tools) et
Pillow pour l'icône.

## La barre de menus

Une icône discrète reste en permanence dans la barre de menus : un point de
couleur (bleu → orange à 70 % → rouge à 90 %) suivi du pourcentage de quota
consommé. Un clic ouvre un aperçu compact — quota, consommation du jour, trois
principaux modèles, et un bouton pour ouvrir le tableau de bord complet.

L'aperçu est lui aussi **en direct** : il se met à jour tout seul tant qu'il
reste ouvert.

## Le quota mensuel

C'est la mesure qui compte : Ollama Pro inclut 60 $ d'usage par mois.

**Ollama n'expose pas d'API d'usage documentée** — mais la route existe.
`GET https://ollama.com/api/usage` répond `401` sans authentification, et
renvoie l'usage du compte avec une clé en `Authorization: Bearer`. Elle n'est
pas documentée et peut disparaître sans préavis.

Crée une clé sur **https://ollama.com/settings/keys**, colle-la dans l'app, et
le quota se lit en direct — plus rien à saisir.

```jsonc
{
  "activity": { "cost": "0.00000",           // inutilisable : toujours à zéro
                "period": { "starting_at": "2026-09-07T00:00:00Z" } },
  "limits": { "monthly": {
    "usage": 0.874,                          // part du plafond consommée
    "models": [ { "name": "deepseek-v4.1-flash", "request_count": 10997 } ]
  } }
}
```

Ce que l'API donne, et ce qu'elle ne donne pas :

| | |
|---|---|
| ✅ Le pourcentage du mois | `limits.monthly.usage`, exact |
| ✅ Les requêtes par modèle, **tous clients** | `limits.monthly.models` |
| ⚠️ Le montant en dollars | Le site l'affiche, l'API non : elle ne renvoie que la **part** (`0.874`). L'app multiplie par le plafond du plan — d'où `52,44 $`. `activity.cost` existe mais vaut `0.00000`. |
| ❌ La date de réinitialisation | Déduite du cycle en cours (voir ci-dessous) |
| ❌ Un historique | Aucun. L'app l'échantillonne elle-même toutes les 5 min. |

**La date de réinitialisation** se déduit de `activity.period.starting_at` : le
cycle repart le même quantième chaque mois. Sur ce compte, cycle ouvert le
07/09 → remise à zéro le 07/10, ce que confirme la page de réglages du site.

### Sans clé

L'app retombe sur des **relevés manuels** : tu colles le montant affiché sur
`ollama.com/settings` de temps en temps. Dès le deuxième relevé, elle mesure le
rythme en $/jour et projette le dépassement.

> La clé est stockée dans `config.json`, en `0600`. Elle donne accès à ton
> compte : révoque-la depuis `ollama.com/settings/keys` si besoin.

## Aller plus loin : la décomposition par outil (désactivée)

Le proxy est **désactivé par défaut** (`"proxy_port": 0`). Il sert uniquement à
savoir quelle part vient de quel outil — ce qui n'apporte rien au suivi du
quota, déjà global puisque c'est Ollama qui le mesure. L'activer suppose de
rediriger chaque client, un par un.

Si un jour la question « qui consomme quoi » te démange : mets un port libre dans
`config.json` (par exemple `11439`), puis pointe tes clients dessus.

| Client | Réglage |
|---|---|
| Claude Code | `ANTHROPIC_BASE_URL=http://127.0.0.1:11439` |
| Clients OpenAI | `OPENAI_BASE_URL=http://127.0.0.1:11439/v1` |
| Codex | `base_url = "http://127.0.0.1:11439/v1"` dans `~/.codex/config.toml` |
| Ollama CLI | `OLLAMA_HOST=http://127.0.0.1:11439 ollama run …` |

Le client est alors identifié par son `User-Agent` (Claude Code, Codex, opencode,
ChatGPT, Copilot, Cursor, Zed…), à défaut par la famille d'API
(`/v1/messages` → Anthropic, `/v1/chat/completions` → OpenAI, `/api/*` → Ollama).
L'en-tête `X-Ollama-Buddy-Client: Mon outil` force un nom.

> ⚠️ Si tu fais passer **Claude Code** par le proxy, mets aussi
> `"read_transcripts": false` dans `config.json` — sinon ses requêtes seraient
> comptées deux fois, une par le proxy et une par ses transcripts.

## Ce que ça affiche

- **Un titre qui se lit tout seul** : « 2,22 Md tokens sur 30 jours, portés à
  97,7 % par deepseek-v4.1-flash ».
- **Une courbe d'évolution en aires empilées** : heure par heure sur la journée,
  jour par jour jusqu'à 60 jours, semaine par semaine au-delà. Survol à la souris
  ou flèches `←` `→` au clavier.
- **Le quota mensuel en dollars**, avec projection de dépassement.
- **Des stat tiles** : modèles actifs, messages, moyenne par jour, écart avec la
  période précédente.
- **Une barre par modèle**, avec sa part en % et son nombre de messages.
- **Une vue tableau** dépliable : entrée / cache écriture / cache lecture / sortie.

En haut à droite, un bouton fait défiler les trois thèmes : **automatique**
(suit macOS), **clair**, **sombre**. Le réglage est conservé dans `config.json`.

## Temps réel

Le serveur surveille les transcripts toutes les 2 secondes et **pousse** les
changements via un flux SSE — pas d'interrogation périodique côté interface. Un
indicateur *En direct* pulse à chaque mise à jour et passe à l'orange si la
connexion tombe (reconnexion automatique).

Le scan est incrémental : seuls les octets ajoutés depuis le dernier passage sont
relus, ce qui rend cette fréquence indolore même sur 200 Mo de transcripts.

## D'où viennent les chiffres

Deux sources se cumulent :

1. **Les transcripts Claude Code** (`~/.claude/projects/**/*.jsonl`), qui
   enregistrent pour chaque message le modèle et les compteurs de tokens exacts.
2. **Le proxy**, s'il est activé — il voit alors passer les autres clients.

C'est la première qui donne l'historique : elle remonte à bien avant
l'installation de l'app.

Deux précautions sur les transcripts :

- **Déduplication par identifiant de message.** Un même message recopié dans
  plusieurs sessions était compté plusieurs fois — sur ce corpus, **1,72×** de
  surestimation. Corrigé.
- **Filtre cloud.** Seuls les modèles dont Ollama annonce un hébergement
  `ollama.com` (`/api/tags` → `remote_host`) sont comptés. Si le serveur Ollama
  n'est pas joignable, le filtre se désactive et un avertissement s'affiche.

### Lire les tokens correctement

La colonne **cache lecture** domine presque toujours le total : chaque requête
renvoie le contexte complet, et Claude Code le renvoie à chaque tour. Ce n'est
pas une anomalie, c'est la mécanique du cache de prompt.

## Conception

**Mise en page.** Fluide plutôt que figée : largeur maximale 1560 px, gouttières
et tailles de titre en `clamp()`, graphe pleine largeur, puis deux colonnes
(quota et clients à gauche, répartition à droite) qui se replient en une seule
sous 940 px. Les composants utilisent des **container queries** — la liste des
modèles réagit à *sa* largeur, pas à celle de la fenêtre. Résultat : plus de
colonne de 1020 px perdue au milieu d'un grand écran.

**Couleurs.** Palette catégorielle validée par calcul, dans les deux thèmes, sur
la surface réelle : bande de clarté, plancher de chroma, séparation pour les
daltonismes (ΔE ≥ 8 en OKLab) et contraste ≥ 3:1. Chaque modèle garde sa teinte
de façon **permanente** (stockée en base), jamais selon son rang. Au-delà de
8 modèles, le reste est regroupé sous « Autres ».

**Thème.** Clair et sombre sont deux palettes **choisies**, pas une inversion
automatique : les pas sombres sont validés pour la surface sombre. Le thème suit
macOS par défaut, et peut être forcé.

**Le lama.** `web/ollama.png` vient d'`ollama.com/public/ollama.png`. Il sert de
**masque CSS** dans l'en-tête (il prend donc la couleur du texte et suit les deux
thèmes sans qu'il faille deux fichiers) et d'**image gabarit** dans la barre de
menus (macOS l'inverse selon le fond). Dans la barre, le pourcentage est neutre
en temps normal et ne passe à l'orange puis au rouge qu'en approche du plafond :
une couleur permanente serait du bruit.

**Mouvement.** Courbes d'easing personnalisées, transitions sous 300 ms,
`scale(0.97)` au clic, apparition en cascade, infobulles instantanées après la
première. Les survols sont conditionnés à `@media (hover: hover)`.
`prefers-reduced-motion` est respecté : les fondus restent, les déplacements
disparaissent.

**Accessibilité** — audit Lighthouse : **100/100**. Lien d'évitement, focus
visible au clavier uniquement, `aria-pressed` sur les filtres, libellés
programmatiques, région `aria-live`, cibles ≥ 24 px, tableau de repli pour chaque
graphique, et **aucune information portée par la seule couleur** (les variations
portent une flèche et un mot). Le graphique est focusable et explorable aux flèches.

**Inspirations.** Les principes de données d'Apple (HIG *Charting data*,
*Typography*, *Layout*) : police système, hiérarchie par styles de texte, un
titre auto-informatif par graphique, divulgation progressive. Côté Dribbble, les
dashboards analytiques récents : hiérarchie KPI en tête, cartes régulières,
beaucoup d'air, micro-interactions discrètes.

## Fichiers

| Fichier | Rôle |
|---|---|
| `ollama_buddy.py` | Serveur HTTP, proxy, indexation, agrégation, flux SSE |
| `web/index.html` | Le tableau de bord |
| `web/mini.html` | L'aperçu de la barre de menus |
| `app/main.swift` | L'enveloppe macOS : fenêtre, barre de menus, cycle de vie |
| `app/make_icon.py` | Génère `AppIcon.icns` (Pillow + `iconutil`) |
| `build_app.sh` | Assemble le bundle, `--install` pour le copier dans /Applications |
| `config.json` | Quota, ports, intervalle de veille, lecture des transcripts |
| `usage.db` | Index SQLite local. Supprimable, il se reconstruit |

L'app macOS stocke ses données dans `~/Library/Application Support/OllamaBuddy/`.

## Notes

- Le serveur écoute uniquement sur `127.0.0.1`. Le proxy, s'il est activé, aussi.
- Seuls `127.0.0.1:11434` (Ollama) est interrogé, pour le filtre cloud et le nom
  du plan. Aucune donnée ne quitte la machine.
- **`11435` est déjà pris par l'app Ollama** — d'où le port `11439` par défaut.
- Une période et un client sont adressables par URL : `?range=30d&client=Codex`.
