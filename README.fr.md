<div align="center">
  <img src="web/ollama.png" alt="Ollama Buddy" width="88">
  <h1>Ollama Buddy</h1>
  <p><em>Ta consommation Ollama Cloud sur ton Mac — cycle, estimation du quota, mises à jour en direct</em></p>

  [English](README.md) · [Installation](#installation) · [Cycle mensuel](#cycle-mensuel) · [Sécurité](#sécurité)
</div>

Ollama Buddy lit les dépenses quotidiennes de ton compte Cloud avec ta propre clé
API. Un serveur Python fournit un tableau de bord dans une fenêtre macOS native,
avec un résumé dans la barre de menus. Le serveur peut aussi fonctionner seul,
dans un navigateur.

- Dépense du cycle en dollars, requêtes et sept derniers jours.
- Quota mensuel estimé, compte à rebours et projections après une journée complète
  d’échantillons.
- Français/anglais et thèmes automatique/clair/sombre, partagés entre les vues.
- Bibliothèque standard Python 3.9+ à l’exécution, sans framework frontend.

La capture ci-dessous précède les dernières modifications du cycle et de
l’historique par modèle ; les libellés et chiffres de l’app actuelle diffèrent.

<img src="docs/dashboard.png" alt="Une version antérieure du tableau de bord Ollama Buddy" width="100%">

## Installation

Pour construire l’app : macOS 12+, outils en ligne de commande Xcode (`swiftc`,
`lipo`, `iconutil`, `codesign`), Python 3.9+ et Pillow pour l’icône.

```bash
xcode-select --install
pip3 install pillow

git clone https://github.com/fwBoa/ollama-buddy.git
cd ollama-buddy
./build_app.sh
open "Ollama Buddy.app"
```

Le bundle contient les exécutables Apple Silicon et Intel. Ajoute `--install`
pour le copier dans `/Applications`. Le lanceur natif utilise `/usr/bin/python3` :
les outils Xcode sont donc aussi nécessaires à l’exécution.

L’app porte une signature ad-hoc, sans notarisation Developer ID. Gatekeeper peut
bloquer un bundle téléchargé ; la construction locale est l’installation recommandée.
Uniquement pour un bundle de confiance, la quarantaine peut être retirée avec :

```bash
xattr -dr com.apple.quarantine "/Applications/Ollama Buddy.app"
```

Pour utiliser le tableau de bord sans l’enveloppe native :

```bash
python3 ollama_buddy.py
# Options : --no-browser --port 11499
```

Fermer la fenêtre laisse l’app dans la barre de menus. `⌘Q` arrête le serveur si
c’est cette app qui l’a démarré ; un serveur lancé indépendamment reste actif.

## Connexion et réglages

1. Crée une clé sur [ollama.com/settings/keys](https://ollama.com/settings/keys).
2. Colle-la dans le tableau de bord.
3. Dans **Paramètres**, renseigne **Prochaine réinitialisation (UTC)**, avec l’heure
   si tu la connais. Utilise la date affichée par Ollama, ou l’anniversaire de ta
   souscription converti en UTC.
4. Vérifie **Plafond mensuel**. Une valeur saisie remplace le plafond de l’offre
   lue dans l’app Ollama locale. Vide, le champ utilise les valeurs intégrées :
   Pro 60 $, Max 300 $, Team 1 000 $. Une offre inconnue n’a pas de plafond déduit.
   Vérifie ton propre contrat, notamment pour une ancienne offre.

Les plafonds et la règle du quantième viennent de la
[FAQ tarifaire Ollama](https://www.ollama.com/pricing), vérifiée le 12 octobre 2026.
Ils peuvent évoluer ; le plafond manuel reste disponible.

Les boutons en haut à droite actualisent les données, changent le thème et
changent la langue. Les paramètres proposent aussi une liste de langues.
Les deux préférences sont enregistrées sur le serveur.

| Raccourci | Action |
|---|---|
| `⌘1` | Afficher la fenêtre |
| `⌘R` | Recharger le tableau de bord |
| `⌘⇧R` | Vider le cache et actualiser |
| `⌘O` | Ouvrir dans le navigateur |
| `⌘Q` | Quitter |

La barre de menus affiche un lama et le pourcentage estimé : alerte orange à 70 %,
rouge à 90 %. Une mesure indisponible affiche un tiret. Le résumé montre les trois
derniers jours et, s’il existe, un ancien relevé par modèle daté.

## Cycle mensuel

Selon Ollama, les crédits inclus se réinitialisent chaque mois au quantième de
la souscription d’origine, y compris pour les abonnements annuels. Le passage à
la nouvelle tarification réinitialise l’usage immédiatement, en conservant cet
anniversaire pour les cycles suivants.

L’app **ne découvre pas la date de souscription et n’observe pas les remises à zéro**.
Le timestamp UTC saisi sert d’ancre mensuelle. Les deux bornes du cycle sont
calculées depuis cette ancre originale : un 31 devient le 28/29 février, puis
retrouve le 31 en mars. Les dates anciennes sont rejouées sans limite d’un an.
Effacer la date retire le calcul du cycle.

L’intégration demande `https://ollama.com/api/usage?range=30d` et attend des
`buckets` portant `from`, `usage_usd`, `request_count`, ainsi que `totals`.
Cette route d’usage n’est pas documentée et sa réponse peut changer. Le code a
été adapté à ce format le 7 octobre 2026 ; il ne lit plus `limits.monthly.usage`.

**Le quota est une estimation, pas le solde officiel des crédits Ollama.**
L’app additionne les dépenses quotidiennes disponibles sur le cycle configuré,
puis les divise par le plafond. Les jours sont coupés en UTC : si le cycle démarre
en cours de journée, le premier jour partiel est entièrement exclu, car l’API ne
permet pas de le couper à l’heure du reset. Modifier l’heure peut donc changer la
somme. Les remboursements, crédits achetés, changements d’offre et différences
d’imputation ne sont pas reconstitués.

Une réponse sur trente jours peut ne pas couvrir un cycle de trente et un jours.
Si le premier jour reçu est postérieur au premier jour entier du cycle, l’app
masque le quota plutôt que de présenter une somme tronquée. Sans clé, plafond,
cycle, réponse valide ou accès à l’API, elle masque également les chiffres et
explique le motif dans le tableau de bord.

Un échantillon est enregistré au plus toutes les cinq minutes. Le rythme moyen
nécessite au moins vingt-quatre heures de mesures du cycle courant. Les projections
prolongent ce rythme ; elles ne prédisent pas les dépenses futures. Modifier le
plafond ou le cycle efface ces échantillons pour éviter de mélanger les mesures.

L’API ne fournit plus l’ancienne répartition par modèle attendue par ce projet.
Si SQLite contient un relevé antérieur, sa date et ses compteurs sont affichés.
Une base neuve n’a aucune répartition par modèle. Remplacer ou retirer la clé
efface les relevés et couleurs enregistrés pour éviter d’attribuer l’historique
d’un compte à un autre. Remplacer la clé **ne la révoque pas chez Ollama** :
la révocation se fait dans les paramètres du site Ollama.

## Sécurité

- Le serveur écoute uniquement sur `127.0.0.1`, au port `11499` par défaut.
- Les requêtes doivent utiliser le véritable hôte local et son port. Les origines
  web étrangères et requêtes intersites sont refusées, contre les modifications
  de paramètres depuis un site tiers et le DNS rebinding. Actualiser exige un POST.
- Les paramètres exigent un objet JSON de taille limitée, des champs connus,
  des montants finis et positifs ou nuls, un timestamp valide et des préférences reconnues.
- La clé est enregistrée atomiquement dans `config.json`, en `0600` dès la création.
  Elle reste **en clair**, sans Trousseau macOS. Un logiciel lancé sous ton utilisateur
  peut la lire : ce mécanisme n’isole pas d’un processus local malveillant.
- La clé est envoyée à `ollama.com` dans l’en-tête HTTPS Authorization. Elle n’est
  jamais renvoyée au tableau de bord. Seule l’offre du compte est exposée, pas son email.
- Les noms de modèles et erreurs insérés dans le HTML sont échappés. Les pages
  portent une politique de scripts avec nonce, interdisent l’intégration dans une
  frame et limitent leurs requêtes réseau au serveur local.

Ces protections visent les attaques provenant de sites web. Les programmes locaux
peuvent toujours accéder à l’API HTTP. Le bundle natif n’est ni sandboxé ni notarisé.

## Développement et données

| Fichier | Rôle |
|---|---|
| `ollama_buddy.py` | API Cloud/compte, calcul du cycle, SQLite, HTTP et SSE |
| `web/index.html` | Tableau de bord, paramètres et traductions |
| `web/mini.html` | Résumé de la barre de menus |
| `app/main.swift` | Cycle de vie natif, fenêtres, menus et navigation |
| `app/make_icon.py` | Génération d’icône avec Pillow/iconutil |
| `build_app.sh` | Construction du bundle universel |
| `config.example.json` | Configuration ; plafond nul pour suivre l’offre |
| `tests/test_backend.py` | Tests du cycle, validation, cache, stockage et HTTP |
| `tests/test_frontend.cjs` | Tests navigateur sur données fictives ; nécessite Playwright |

L’app native conserve `config.json` et `usage.db` dans
`~/Library/Application Support/OllamaBuddy/`. Un lancement direct utilise le dossier
du projet, sauf si `OLLAMA_BUDDY_DATA` désigne un autre répertoire. Secrets, bases
et artefacts de construction sont exclus de Git.

SQLite conserve `cloud_samples` et `model_color`, en mode WAL. Supprimer la base
perd l’historique du rythme et les anciens relevés par modèle ; seuls de nouveaux
échantillons globaux sont reconstruits. Le démarrage supprime également les anciennes
tables qui ne sont plus utilisées par cette version.

Les vues web reçoivent des événements SSE, avec maintien de connexion et reconnexion.
Le tableau de bord interroge toutes les cinq secondes si EventSource est absent.
Une veille passe toutes les deux secondes ; le cache partagé limite les appels Cloud
à environ un par minute, sauf actualisation explicite. Un passage à chaque minute
met à jour le cycle et le compte à rebours même sans nouvelle dépense. La barre de
menus native relit séparément le résultat en cache toutes les vingt secondes.

Les tests du serveur n’exigent aucune dépendance ni compte Ollama réel :

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Les tests navigateur se lancent avec `node tests/test_frontend.cjs` lorsque Playwright
et son navigateur Chromium sont disponibles. Ils utilisent un dossier de données
temporaire et des réponses Ollama simulées. Reconstruis le bundle après une modification
des sources pour utiliser la nouvelle version.

Licence MIT. App indépendante, sans affiliation officielle à Ollama.
