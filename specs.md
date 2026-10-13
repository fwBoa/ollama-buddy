# Ollama Buddy — spécifications produit et comportements

Version : 1.0 — 13 octobre 2026.

Ce document définit les comportements attendus de la version livrée. Le README
explique l’installation ; les spécifications fixent les règles du produit, les
sources de données et les critères de validation. Une évolution fonctionnelle
doit mettre à jour ce document et les vérifications concernées dans le même commit.

## 1. Objectif et périmètre

Permettre à une personne utilisant Ollama Cloud de comprendre rapidement combien
de crédits il lui reste, quand ils se renouvellent et comment elle les consomme.
L’app est un utilitaire macOS de barre de menus, accompagné d’une fenêtre et d’un
mode navigateur. Elle ne génère pas de réponses, ne lit pas les conversations et
ne remplace pas la facturation Ollama.

### Principes de cohérence

- Une donnée a un emplacement principal par vue. Une répétition doit servir une
  interaction différente, jamais remplir l’écran.
- Vue d’ensemble répond à « où en suis-je aujourd’hui ? » ; Activité permet
  d’expliquer une période ; Paramètres sert à configurer l’app.
- Le montant mensuel consommé appartient à la carte des crédits. L’usage du jour
  appartient au résumé quotidien. Ces deux montants ne sont pas interchangeables.
- Les choix d’une période doivent affecter ensemble graphique, totaux, tokens et
  tableau de la vue Activité.
- Une donnée inconnue n’est jamais un zéro. Une estimation n’est jamais présentée
  comme un solde officiel. Aucun compteur par modèle n’est inventé.
- Les explications détaillées et réglages de secours sont accessibles à la demande.
- Le thème existant, la navigation à trois vues et les fonctionnalités utiles sont
  conservés. Aucun framework ni service tiers n’est nécessaire à cette évolution.

## 2. Sources et vocabulaire

| Donnée | Source | Sens exact |
|---|---|---|
| Plafond mensuel | `GET /api/balance`, `included.allowance_usd` | Crédits inclus dans l’offre réelle |
| Disponible mensuel | `included.balance_usd` | Solde restant des crédits inclus |
| Consommé mensuel | Plafond moins disponible | Crédits inclus consommés, pas la dépense totale toutes sources |
| Cycle | `included.period.from` / `until` | Bornes exactes, début inclus et fin exclue |
| Crédits supplémentaires | `purchased.balance_usd` | Solde acheté restant, distinct du plafond mensuel |
| Limites anciennes offres | `included.session` / `weekly` | Pourcentage restant et date de réinitialisation |
| Dépense quotidienne | `GET /api/usage?range=30d`, `buckets[].usage_usd` | Usage facturé pendant une journée UTC |
| Requêtes | `request_count` | Nombre d’appels enregistré dans les journées considérées |
| Entrée | `input_tokens` | Tokens d’entrée, cache inclus |
| Cache | `cached_input_tokens` | Sous-ensemble des tokens d’entrée |
| Sortie | `output_tokens` | Tokens générés |
| Offre locale de secours | API locale Ollama `/api/me` | Indication pour le plafond de secours, pas preuve de l’offre de la clé API |

Formules : `consommé = plafond − disponible` ; `part consommée = consommé / plafond`
si le plafond est strictement positif ; `tokens traités = entrée + sortie`.
Le cache n’est jamais additionné une seconde fois. Le pourcentage de crédits
consommés n’inclut pas les crédits achetés.

Les données doivent être validées : valeurs finies, non négatives, compteurs
entiers, cache inférieur ou égal à l’entrée, timestamps avec fuseau et période
valide. Une réponse invalide devient un état indisponible, pas une mesure.

## 3. Temps, cycle et conservation

- Les journées de l’API restent en **UTC**, même si le Mac est dans un autre fuseau.
  « Aujourd’hui (UTC) » désigne la date UTC courante, pas une reconstruction de la
  journée locale. L’app ne peut pas déplacer des appels individuels entre journées.
- Les dates de renouvellement sont affichées une seule fois en **heure locale**,
  avec le fuseau. Leur référence UTC exacte est disponible dans l’infobulle.
- L’activité commence à la date UTC du dernier renouvellement fourni par Balance.
  Les journées antérieures sont conservées mais ne sont pas affichées dans le cycle.
- En secours, une référence mensuelle saisie définit le cycle. Sans cycle connu,
  la fenêtre disponible des 30 derniers jours est utilisée et nommée comme telle.
- Un renouvellement en cours de journée ne permet pas de découper le premier
  relevé. Ce relevé est conservé pour l’exploration et signalé « Jour du
  renouvellement ». Ses dépenses, requêtes et tokens peuvent précéder le reset.
  L’explication complète vit dans « Comprendre les données ».
- Le solde officiel reste exact à sa propre borne, indépendamment de cette limite.
  L’estimation de secours exclut toute cette première journée partielle.
- Un cycle peut compter 28, 29, 30 ou 31 jours. « Cycle en cours » ne tronque pas
  un cycle de 31 jours. Les périodes de 7/30 jours sont intersectées avec le cycle.
- Une journée manquante n’est pas un jour sans usage. Le graphique la représente
  comme manquante ; le tableau ne crée pas de relevé fictif.
- Les journées et échantillons officiels sont conservés jusqu’à 400 jours. Un
  relevé complet n’est pas remplacé par un compartiment partiel. Les corrections
  réellement publiées par Ollama remplacent les mesures précédentes.

## 4. F01 — connexion et changement de compte

Sans clé : afficher une seule carte de connexion, un champ masqué, « Connecter »
et un lien vers la création de clé Ollama. Aucun montant, graphique vide de quota
ou demande de date de renouvellement ne doit suggérer qu’un compte est connecté.

Après enregistrement : utiliser cette clé pour Balance et Usage. Les réglages
manuels ne sont pas requis pour une offre à crédits reconnue par Balance.
La clé native reste dans le Trousseau ; en mode CLI, elle reste dans le fichier
privé de configuration. Elle n’est pas renvoyée au navigateur ni passée en argument.

Remplacer la clé change la source du compte et efface les historiques et caches
associés à l’ancienne clé. Déconnecter exige une confirmation explicite et remet
les vues à l’état de connexion. Annuler conserve le compte. Aucune de ces actions
ne révoque la clé sur Ollama ; la révocation se fait sur le site officiel.

Un échec de migration vers le Trousseau conserve la clé précédente et permet une
nouvelle tentative. Une erreur d’enregistrement ne valide pas une modification
partielle ; le fichier et le Trousseau doivent revenir à leur état précédent.

**Validation :** connexion, remplacement, annulation, déconnexion, Trousseau
verrouillé, échec d’écriture et absence de clé/email dans les réponses HTTP.

## 5. F02 — Vue d’ensemble

Ordre et rôle :

1. Un contexte court du cycle : date de début du renouvellement, sans long texte.
2. Deux mesures : **Aujourd’hui (UTC)** en dollars et **Requêtes aujourd’hui**.
3. Une carte des crédits mensuels : consommé/plafond, jauge, disponible et prochaine
   réinitialisation. Cette carte est la seule source du montant mensuel sur la vue.
4. Un aperçu du graphique du cycle, sans filtres, tableau, détail des tokens ou
   détail du jour déjà affiché en haut. Une barre ouvre son jour dans Activité.
5. Un accès clair à Activité ; « Comprendre les données » est replié par défaut.

La ligne « Pic quotidien », le cumul « Requêtes · jours disponibles », le tableau
« Derniers jours », les dates de réinitialisation répétées et les slogans de
pied de navigation sont retirés de cette vue. Le nom d’une offre déduit du compte
local n’est pas présenté comme celui du compte de la clé Cloud.

Si le relevé UTC du jour manque : afficher « … », jamais reprendre hier ni zéro.
Si la dépense du jour est réellement nulle : afficher zéro. L’aperçu garde une
échelle comparable entre jours et un libellé accessible pour chaque barre.

**Validation :** aucun tableau ni token du jour sur cette vue ; le montant mensuel
n’y apparaît qu’une fois ; cliquer une barre ouvre le bon jour dans Activité.

## 6. F03 — carte des crédits

Pour une offre mensuelle à crédits :

- Titre : « Crédits mensuels » si officiel, « Quota mensuel estimé » en secours.
- Montrer le montant consommé sur le plafond, une jauge avec part consommée,
  le solde disponible et une seule date locale de prochaine réinitialisation.
- Les crédits supplémentaires apparaissent uniquement si leur solde est positif.
  Leur absence à zéro ne signifie pas que l’utilisateur ne peut pas en acheter.
- Ne pas répéter la mention « Solde officiel » sous chaque ligne ni un compte à
  rebours à côté d’une date identique. Le statut de la source reste dans l’aide.
- La jauge est bornée visuellement à 100 %. Les données sous-jacentes ne sont pas
  modifiées pour l’affichage ; un dépassement estimé peut être présenté comme tel.
- Ne pas inviter à « configurer le cycle » lorsque Balance fournit déjà ses bornes.
  Le réglage de secours se trouve dans Paramètres.

Si plafond inclus nul : indiquer qu’il n’y a pas de crédits mensuels inclus,
montrer un solde acheté positif le cas échéant et ne pas diviser par zéro.
Pour une ancienne offre : afficher les pourcentages restants Session/Semaine,
sans inventer des dollars mensuels ni une jauge mensuelle, sous le titre « Limites d’usage ». Les réinitialisations
propres à ces limites restent associées à leurs lignes dans la carte détaillée.
Si Usage omet les coûts et tokens d’une ancienne offre, l’activité monétaire
reste indisponible au lieu d’inventer des valeurs.

**Validation :** différents plafonds réels, zéro inclus, solde acheté nul/positif,
petite consommation positive, anciennes offres et indépendance des réglages manuels.

## 7. F04 — Activité, filtres et graphique

Activité répond à « qu’est-ce qui constitue ma consommation sur cette période ? ».
Elle présente les totaux de dépenses et **Requêtes relevées**, puis le graphique,
le détail du jour sélectionné, les tokens de la période et le tableau quotidien.
Elle ne répète pas la carte de quota mensuel.

Filtres : « Cycle en cours » par défaut lorsqu’un cycle est connu ; « 7 jours »
et « 30 jours » pour une exploration plus courte, toujours à l’intérieur du cycle.
Sans cycle connu, utiliser 30 jours par défaut et ne pas proposer un cycle inventé.
Une modification de filtre actualise **tous** les totaux et le tableau, pas seulement
les barres. Le filtre d’Activité ne modifie pas l’aperçu complet de Vue d’ensemble.

Une sélection de barre affiche date UTC, coût, nombre de requêtes et compteurs du
jour dans une zone unique. Le jour sélectionné est maintenu lors des mises à jour
s’il appartient encore à la période ; sinon sélectionner le jour le plus récent.
Au clavier, Entrée/Espace activent la barre comme au pointeur. Le focus est conservé
pendant une mise à jour automatique.

Les graduations de date sont distinctes : une période de deux jours ne répète
pas le premier jour au milieu. Une journée manquante affiche un état inconnu,
une journée à zéro reste sélectionnable. Sans relevé, un seul message vide est
présenté et les filtres sont désactivés ; aucun tableau vide redondant n’est ajouté.

**Validation :** 1/2/7/30/31 jours, trou en début/milieu de cycle, jour nul,
sélection hors filtre, variation de coût seule, navigation depuis l’aperçu,
absence de débordement et équivalence des interactions clavier/pointeur.

## 8. F05 — requêtes et tokens

« Requêtes relevées » est la somme des `request_count` des relevés disponibles de
la période choisie. Ce n’est ni un nombre de jours, ni un nombre de conversations,
ni un décompte garanti à partir de la seconde exacte du renouvellement.
Le détail d’un jour et le tableau affichent ses propres appels, pas le cumul.

Les tokens d’entrée, de sortie et d’entrée en cache sont affichés séparément pour
la même période que le graphique et les totaux. Le détail du jour appartient à
Activité, pas à la Vue d’ensemble. Les valeurs compactes gardent la valeur exacte
en infobulle. La règle « cache inclus dans l’entrée » reste accessible dans l’aide.

Un compteur manquant pour un relevé rend le total correspondant indisponible,
même si les autres compteurs sont présents. Un trou de journées ne produit pas
de tokens fictifs ; le titre et l’aide parlent bien de relevés disponibles.
Les anciens totaux conservés en base ne permettent pas de reconstruire une
répartition entrée/cache/sortie ; la migration laisse ces champs inconnus.

**Validation :** cache non ajouté deux fois, compteur nul, compteur absent,
totaux du filtre, persistance après redémarrage et migration d’une ancienne base.

## 9. F06 — Paramètres et secours

Quatre sections : Connexion, Cycle, Alertes, Apparence.

La section Cycle indique que le plafond et les dates sont automatiques lorsque
Balance répond. Les champs de secours sont placés dans « Estimation de secours »,
replié par défaut. Ils restent éditables sans remplacer les données officielles.

- Plafond : nombre fini non négatif ; vide/0 suit le plan local de secours. Les
  valeurs intégrées sont Pro 60 $, Max 300 $, Team 1 000 $, sans garantie de contrat.
  Une offre inconnue ne reçoit aucun plafond inventé.
- Référence : date/heure **UTC** explicites, sauvegardée au changement. Un effacement
  désactive le calcul estimé du cycle, sans désactiver Balance.
- Les deux bornes se calculent depuis l’ancre originale : le quantième 31 revient
  en mars après un reset borné au 28/29 février. Une ancre passée est autorisée.
- Les saisies inachevées, focus et sections repliables ouvertes survivent aux
  actualisations et à la navigation. La confirmation d’enregistrement reste brève.
- Une erreur d’enregistrement est visible et n’est pas présentée comme un succès.

Les projections restent dans une section repliable de la carte. Elles exigent
24 heures de mesures comparables du même cycle et plafond ; elles sont toujours
nommées « estimatives ». Elles n’estiment pas le solde de crédits supplémentaires.

**Validation :** valeurs invalides, ancre ancienne/quantième 31/année bissextile,
champ effacé, saisie pendant un flux, échec d’enregistrement et données officielles
non remplacées par le plafond ou la date de secours.

## 10. F07 — barre de menus et panneau compact

L’icône native indique la part mensuelle consommée ; orange à 70 %, rouge à 90 %.
Sans pourcentage calculable, afficher un tiret. Le panneau reste une consultation
rapide : disponible mensuel, jauge, réinitialisation locale, dépense et requêtes
d’aujourd’hui UTC, puis « Ouvrir le tableau de bord ».

Ne pas y répéter le montant mensuel déjà contextualisé par la jauge, un tableau
de derniers jours ou les compteurs de tokens. Un solde acheté positif peut être
affiché ; une ligne à zéro est masquée. Anciennes offres et erreurs gardent leurs
états explicites. Thème et langue suivent les paramètres du tableau de bord.
Le panneau adapte sa hauteur au contenu ; son bouton ouvre la fenêtre native.

**Validation :** cohérence des nombres avec Vue d’ensemble, zéro/inconnu, langues,
thèmes, ouverture du tableau de bord et absence de contenu coupé à 300 px.

## 11. F08 — alertes natives

Option désactivée par défaut. Activable dans l’app macOS, indisponible en CLI seul.
L’autorisation macOS est demandée après activation ; un refus est expliqué dans
Paramètres et aucune alerte n’est promise si le système la bloque.

Seuils de crédits mensuels consommés : 70 %, 90 %, 100 %. Une hausse directe de
60 % à 95 % produit une seule alerte à 90 %. Le plus haut seuil atteint n’est
notifié qu’une fois par cycle/contexte : recroiser un seuil, redémarrer ou basculer
l’option ne répète pas une alerte déjà délivrée. Un nouveau cycle, compte ou
réglage de calcul constitue un nouveau contexte. Cliquer l’alerte ouvre l’app.

Les alertes utilisent Balance quand il est disponible et l’estimation de secours
sinon, avec un texte adapté. Pas d’alerte mensuelle avec ancienne offre, plafond
nul, quota indisponible ou absence de clé. L’app doit rester en fonctionnement.

**Validation :** saut de seuil, déduplication, changement de cycle, désactivation,
refus système et distinction officiel/estimé (`--test-alerts` et tests web).

## 12. F09 — actualisation et erreurs

- Cache partagé de 60 secondes par endpoint ; watcher local toutes les 2 secondes.
  SSE pour les vues, lecture native du résultat en cache toutes les 20 secondes.
  Un flux actif ne garantit pas une facturation Ollama instantanée : le fournisseur
  peut publier les dernières requêtes avec un délai.
- Actualiser explicitement invalide les caches et redemande les données. Un `429`
  respecte `Retry-After`, y compris après actualisation manuelle.
- Une panne d’Usage ne masque pas un Balance valide. Une panne de Balance permet
  uniquement une estimation nommée comme telle si les données nécessaires existent.
- Pas de chiffres d’un ancien compte après changement de clé. Les réponses sont
  sérialisées avec les changements de configuration.
- SSE se reconnecte ; le tableau de bord passe au polling toutes les 5 secondes
  si nécessaire, sans accumuler plusieurs timers.
- Le bouton d’actualisation donne un retour d’attente ; les erreurs importantes
  affichent un message et une action, sans répéter le même paragraphe dans chaque zone.

| Situation | Résultat attendu |
|---|---|
| Pas de clé | Connexion, aucun chiffre inventé |
| Clé refusée | Message de connexion et remplacement possible |
| Trousseau indisponible | Explication dans Connexion, clé précédente conservée |
| Balance valide, Usage échoue | Carte officielle conservée, activité indisponible |
| Usage valide, Balance échoue | Dépense lisible, quota seulement si secours calculable |
| Historique incomplet | Relevés disponibles, estimation du quota suspendue |
| Jour partiel de renouvellement | Note contextualisée, solde officiel indépendant |
| Relevé du jour absent | « … » pour aujourd’hui, pas la valeur d’hier |
| Relevé nul | Zéro explicite |
| Ancienne offre | Session/Semaine, pas de conversion mensuelle inventée |

## 13. F10 — formats, langue et accessibilité

- Français/anglais ; préférence automatique selon le système. Les préférences
  sont enregistrées et partagées entre fenêtre et panneau.
- Thèmes automatique/clair/sombre. Le thème automatique suit le système.
- Dollars à deux décimales pour les montants ordinaires. Un montant positif
  inférieur à 0,01 $ est affiché « < 0,01 $ », pas « 0,00 $ ».
- Pourcentages à une décimale ; une consommation positive inférieure à 0,1 %
  est affichée « < 0,1 % », y compris dans la barre de menus. Zéro reste zéro.
- Jours de l’historique en UTC ; date du renouvellement locale avec fuseau et
  référence UTC en infobulle. Les champs manuels restent explicitement UTC.
- Navigation sémantique, lien d’évitement, labels des champs, barres activables
  au clavier, focus visible sur les contrôles. Le conteneur principal ne reçoit
  pas de grand contour lors d’un changement de vue.
- Mise en page utilisable de 360 px à une grande fenêtre, sans défilement horizontal.
  Réduction des animations avec la préférence système de mouvement réduit.
- Une section masquée utilise des contrôles standards et reste accessible au clavier.

## 14. F11 — cycle de vie macOS, sécurité et distribution

App universelle Apple Silicon/Intel, macOS 12+, serveur Python 3.9+ standard.
Le lanceur utilise `/usr/bin/python3`, fourni avec les outils de ligne de commande
Xcode. Pillow et le compilateur Swift sont requis à la construction, pas comme
bibliothèques Python d’exécution de l’app. Signature ad-hoc, sans notarisation
Developer ID ; construction locale recommandée.

Fermer la fenêtre garde l’icône active. Quitter arrête uniquement un serveur
lancé par cette instance. Un serveur réutilisé doit annoncer les capacités de la
version ; sinon essayer un port local libre. Le démarrage doit laisser au helper
Trousseau son délai de 30 secondes avant de conclure à un échec. Un Mac verrouillé
ou une autorisation système en attente peut nécessiter une intervention humaine.

Raccourcis : `⌘1` fenêtre, `⌘R` recharger, `⌘⇧R` redemander les données, `⌘O`
navigateur, `⌘Q` quitter. Les liens externes s’ouvrent hors du contenu local.

HTTP sur `127.0.0.1` uniquement ; Host/Origin stricts, protection contre requêtes
intersites et DNS rebinding, rafraîchissement par POST, JSON limité et validé.
Clé envoyée seulement à Ollama en HTTPS, sans suivi de redirection authentifiée.
Configuration écrite atomiquement en `0600`, CSP avec nonce, texte dynamique
échappé, aucun secret/email dans les réponses navigateur ou captures publiques.
Ces protections ne constituent pas une isolation contre les autres programmes
exécutés sous le même utilisateur ; le bundle n’est pas sandboxé.

## 15. Hors périmètre et éléments retirés

- Pas d’historique global par modèle : l’API l’annonce à venir, la clé seule ne
  fournit pas l’historique visible sur le site. Aucun scraping de session ajouté.
- Pas de proxy d’inférence, comparaison de modèles, chat ou prompts enregistrés.
- Pas de fusion des vues : leurs usages sont distincts, même si elles partagent
  les données et le composant de graphique.
- Retrait de présentation : pic quotidien principal, tableaux dupliqués dans
  l’aperçu, phrases techniques permanentes, dates et soldes à zéro répétés.
  Ces retraits ne suppriment pas les relevés ni les capacités de calcul.

## 16. Validation et livraison

Tests serveur dans `tests/test_backend.py`, tests des parcours dans
`tests/test_frontend.cjs`, captures synthétiques via `tests/capture_ui.cjs`, contrôles
natifs via `--test-alerts`. Les scénarios doivent vérifier les résultats observables
et les cas limites décrits ici, pas uniquement la présence d’un élément HTML.

Une livraison comporte : code cohérent avec ces spécifications, tests appropriés,
inspection des rendus français/anglais et clair/sombre, README et captures actuels,
bundle universel signé et vérifié, commit français conforme et publication GitHub.
Les contrôles qui exigent une autorisation macOS ou un Mac déverrouillé doivent
être déclarés comme restant à vérifier, sans annoncer une réussite fictive.

### Sources officielles

- [Balance](https://docs.ollama.com/api/balance)
- [Usage Cloud, fenêtres et limites](https://docs.ollama.com/api/cloud-usage)
- [Métriques de tokens par réponse](https://docs.ollama.com/api/usage)
- [Offres et crédits](https://ollama.com/pricing)

Sources consultées les 12–13 octobre 2026. Une évolution du contrat API exige une
revérification de la validation, des formules et de ces spécifications.
