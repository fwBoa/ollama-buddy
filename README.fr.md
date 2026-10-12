<div align="center">
  <img src="web/ollama.png" alt="Ollama Buddy" width="88">
  <h1>Ollama Buddy</h1>
  <p><em>Ta consommation Ollama Cloud sur ton Mac — cycle, estimation du quota, mises à jour en direct</em></p>

  [English](README.md) · [Interface](#interface) · [Installation](#installation) · [Cycle mensuel](#cycle-mensuel) · [Sécurité](#sécurité)
</div>

Ollama Buddy lit les dépenses quotidiennes de ton compte Cloud avec ta propre clé
API. Un serveur Python fournit un tableau de bord dans une fenêtre macOS native,
avec un résumé dans la barre de menus. Le serveur peut aussi fonctionner seul,
dans un navigateur.

- Vue d’ensemble avec dépense, requêtes, quota et quatre derniers jours.
- Vue **Activité** avec graphique interactif sur 7 ou 30 jours calendaires et
  tableau de tous les relevés de la période disponible. Les jours manquants
  sont distingués des jours à dépense nulle.
- Paramètres dédiés : connexion, cycle mensuel, alertes et apparence. Les saisies
  en cours sont conservées pendant les mises à jour en direct.
- Quota mensuel estimé, compte à rebours et projections après une journée complète
  d’échantillons.
- Notifications natives optionnelles à 70 %, 90 % et 100 % du quota estimé.
- Français/anglais et thèmes automatique/clair/sombre dans les paramètres.
- Clé de l’app native dans le Trousseau macOS, avec migration de l’ancien fichier.
- Bibliothèque standard Python 3.9+ à l’exécution, sans framework frontend.

## Interface

Captures de la refonte sur des données fictives, en français. Elles sont générées
avec le même serveur et les mêmes pages que l’app.

**Vue d’ensemble** : dépense du cycle, requêtes, quota estimé et derniers jours.
Sélectionne une barre du graphique pour consulter le relevé d’un jour.

<img src="docs/dashboard.fr.png" alt="Vue d’ensemble en français : graphique quotidien interactif et quota mensuel estimé" width="100%">

<details>
<summary>Voir l’activité, les paramètres, le thème sombre et la barre de menus</summary>

### Activité

Passe entre 7 et 30 jours calendaires et consulte tous les relevés de la période
disponible dans le tableau. Un jour manquant se distingue d’un jour à dépense nulle.

<img src="docs/activity.fr.png" alt="Vue Activité en français : graphique sur 30 jours et tableau des relevés disponibles" width="100%">

### Paramètres

Configure la connexion, le cycle, les alertes et l’apparence. La référence de
réinitialisation est saisie en UTC ; son équivalent local est affiché pour éviter
la confusion entre les fuseaux horaires.

<img src="docs/settings.fr.png" alt="Paramètres en français : connexion, plafond, référence UTC, alertes, thème et langue" width="100%">

### Thème sombre

<img src="docs/dashboard-dark.fr.png" alt="Vue d’ensemble en français avec le thème sombre" width="100%">

### Barre de menus

Consulte le quota estimé, la dépense du cycle et les derniers jours, puis ouvre le
tableau de bord depuis le panneau.

<img src="docs/menu-bar.fr.png" alt="Panneau de la barre de menus en français avec le quota et les dépenses" width="300">

</details>

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
c’est cette app qui l’a démarré ; un serveur natif compatible préexistant reste
actif. Le serveur CLI est séparé pour que l’app native utilise le Trousseau.

## Connexion et réglages

1. Crée une clé sur [ollama.com/settings/keys](https://ollama.com/settings/keys).
2. Colle-la dans le tableau de bord.
3. Dans **Paramètres**, renseigne **Réinitialisation de référence (UTC)**, avec l’heure
   si tu la connais. Utilise une date affichée par Ollama, ou l’anniversaire de ta
   souscription converti en UTC. Cette référence peut être passée ; l’app rejoue
   son quantième chaque mois.
4. Vérifie **Plafond en USD**. Une valeur saisie remplace le plafond de l’offre
   lue dans l’app Ollama locale. Vide, le champ utilise les valeurs intégrées :
   Pro 60 $, Max 300 $, Team 1 000 $. Une offre inconnue n’a pas de plafond déduit.
   Vérifie ton propre contrat, notamment pour une ancienne offre.

Les plafonds et la règle du quantième viennent de la
[FAQ tarifaire Ollama](https://www.ollama.com/pricing), vérifiée le 12 octobre 2026.
Ils peuvent évoluer ; le plafond manuel reste disponible.

Le bouton en haut à droite actualise les données. Les listes de thème et de
langue vivent dans les paramètres et suivent le système par défaut.
Les deux préférences sont enregistrées sur le serveur.

La date de réinitialisation affiche aussi l’heure UTC et son équivalent local.
Par exemple, 12 h UTC correspond à 14 h à Paris en octobre. Le calcul attend
l’heure configurée : un rechargement déjà effectué chez Ollama peut donc
précéder l’estimation locale si cette référence est incorrecte. La date indiquée
par Ollama fait foi ; le solde de crédits du site n’est pas lu par cette API.
Déconnecter le compte demande une confirmation et efface ses relevés locaux.

| Raccourci | Action |
|---|---|
| `⌘1` | Afficher la fenêtre |
| `⌘R` | Recharger le tableau de bord |
| `⌘⇧R` | Vider le cache et actualiser |
| `⌘O` | Ouvrir dans le navigateur |
| `⌘Q` | Quitter |

La barre de menus affiche un lama et le pourcentage estimé : alerte orange à 70 %,
rouge à 90 %. Une mesure indisponible affiche un tiret. Le résumé montre les trois
derniers jours. Les anciens compteurs par modèle ne sont plus affichés.

## Cycle mensuel

Selon Ollama, les crédits inclus se réinitialisent chaque mois au quantième de
la souscription d’origine, y compris pour les abonnements annuels. Le passage à
la nouvelle tarification réinitialise l’usage immédiatement, en conservant cet
anniversaire pour les cycles suivants.

L’app **ne découvre pas la date de souscription et n’observe pas les remises à zéro**.
Le timestamp UTC saisi sert d’ancre mensuelle. Les deux bornes du cycle sont
calculées depuis cette ancre originale : un 31 devient le 28/29 février, puis
retrouve le 31 en mars. Les dates anciennes sont rejouées sans limite d’un an.
Effacer la date retire le calcul du cycle. Pour une souscription le 29, 30 ou 31,
utilise la date de souscription originale : un reset ramené au 28 février ne
permet pas de retrouver le quantième d’origine.

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

Chaque lecture réussie actualise les journées dans SQLite, conservées pendant
400 jours. Une app qui a déjà observé les jours peut ainsi compléter un cycle de
31 jours lorsque la réponse suivante n’en couvre que 30. Les anciennes journées
sont actualisées, corrections comprises ; un premier compartiment partiel ne
remplace jamais une journée entière. Changer de clé efface cet historique pour
ne pas mélanger les comptes.

Une installation neuve ne peut pas récupérer un jour que l’API ne fournit plus.
Un trou dans l’historique du cycle masque le quota plutôt que de produire une
somme tronquée. Sans plafond, date ou cycle complet, la dépense reste visible :
la vue montre les journées disponibles sur les 30 derniers jours et leurs bornes.
Le cycle est affiché quand son historique est complet, même si seul son plafond
manque. Sans clé ou en cas de réponse invalide ou d’échec API, la dépense est
masquée avec une explication.

Un échantillon est enregistré au plus toutes les cinq minutes. Le rythme moyen
nécessite au moins vingt-quatre heures de mesures du cycle courant. Les projections
prolongent ce rythme ; elles ne prédisent pas les dépenses futures. Elles vivent
dans la section repliable **Projection estimative**. Modifier le plafond ou le
cycle efface les échantillons du rythme, tout en conservant les journées, pour
éviter de mélanger les mesures.

Les anciens compteurs par modèle sont des archives et ne sont plus affichés.
L’app se concentre sur les dépenses quotidiennes. Remplacer la clé **ne la révoque
pas chez Ollama** : la révocation se fait dans les paramètres du site Ollama.

Les graphiques et tableaux affichent les relevés disponibles des 30 derniers
jours UTC, indépendamment du cycle mensuel estimé. Lorsque la réinitialisation
a lieu après minuit UTC, les dépenses et requêtes du jour restent visibles et
s’actualisent. Le quota est indiqué comme indisponible jusqu’au premier jour UTC
complet, plutôt que d’afficher artificiellement 0 %. Ollama ne permet pas de
séparer le relevé quotidien à l’heure de réinitialisation.

## Notifications

Active **Notifications à 70 %, 90 % et 100 %** dans les paramètres de l’app native.
La fonction est désactivée par défaut. macOS demande l’autorisation, et la page
signale quand les Réglages Système bloquent les notifications. Aucune alerte n’est
produite sans quota calculable. L’app native doit rester ouverte.

Le seuil le plus haut atteint est notifié une seule fois par cycle. Un passage de
moins de 70 % à 95 % produit une alerte à 90 %. Repasser sous un seuil puis le
franchir, redémarrer l’app ou désactiver/réactiver les alertes ne le répète pas.
Un nouveau cycle, une nouvelle clé, un plafond ou une ancre modifiée ouvrent un
nouveau contexte d’alerte. Cliquer sur la notification ouvre le tableau de bord.
Ces alertes suivent le quota estimé, pas le solde officiel des crédits.
Elles ne sont pas disponibles avec le serveur CLI seul.

## Sécurité

- Le serveur écoute uniquement sur `127.0.0.1`, au port `11499` par défaut.
- Les requêtes doivent utiliser le véritable hôte local et son port. Les origines
  web étrangères et requêtes intersites sont refusées, contre les modifications
  de paramètres depuis un site tiers et le DNS rebinding. Actualiser exige un POST.
- Les paramètres exigent un objet JSON de taille limitée, des champs connus,
  des montants finis et positifs ou nuls, un timestamp valide et des préférences reconnues.
- L’app native conserve la clé dans le **Trousseau macOS**, via son exécutable
  signé et le framework Security. Le secret passe par l’entrée standard, jamais
  par les arguments d’une commande. Aucune dépendance tierce n’est ajoutée.
- Au lancement natif, une ancienne clé en clair est copiée dans le Trousseau avant
  d’être retirée de `config.json`. Un échec conserve le fichier et permet de
  réessayer sans perdre la clé. macOS peut demander une nouvelle autorisation
  d’accès après reconstruction de l’app signée ad-hoc.
- Le lancement CLI conserve le fichier privé (`0600`), sauf si
  `OLLAMA_BUDDY_KEYCHAIN_HELPER` désigne le nouvel exécutable natif. Le stockage
  CLI reste en clair. Le Trousseau protège le stockage au repos ; il n’isole pas
  cette app des logiciels qui tournent sous ton propre utilisateur.
- La clé est envoyée à `ollama.com` dans l’en-tête HTTPS Authorization. Elle n’est
  jamais renvoyée au tableau de bord. Seule l’offre du compte est exposée, pas son email.
- Les erreurs insérées dans le HTML sont échappées. Les pages
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
| `tests/capture_ui.cjs` | Captures des README en français et anglais sur données fictives |

L’app native conserve `config.json` et `usage.db` dans
`~/Library/Application Support/OllamaBuddy/`. Un lancement direct utilise le dossier
du projet, sauf si `OLLAMA_BUDDY_DATA` désigne un autre répertoire. Secrets, bases
et artefacts de construction sont exclus de Git.

SQLite conserve `daily_usage` et `cloud_samples`, en mode WAL. Les anciennes
bases peuvent garder des lignes/couleurs de modèles, qui n’ont plus d’affichage.
Supprimer la base perd les journées enregistrées et les échantillons du rythme ;
seul ce qui est encore disponible dans l’API peut être récupéré. Le démarrage
supprime également les anciennes tables inutilisées.

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
temporaire, des réponses Ollama et un Trousseau simulés, sans accéder aux vraies clés.

Pour régénérer les captures des README en français et anglais avec ces mêmes
dépendances :

```bash
node tests/capture_ui.cjs
```

Les captures utilisent aussi des données fictives et n’accèdent ni à ton compte
ni à son Trousseau. Le script remplace uniquement les PNG de démonstration dans
`docs/`.

Après construction, les seuils et leur déduplication sont vérifiables avec :

```bash
"Ollama Buddy.app/Contents/MacOS/OllamaBuddy" --test-alerts
```

L’accès réel au Trousseau et l’autorisation de notification sont gérés par macOS
au lancement natif et à l’activation des alertes. Reconstruis le bundle après une
modification des sources pour utiliser la nouvelle version.

[Licence MIT](LICENSE). App indépendante, sans affiliation officielle à Ollama.
