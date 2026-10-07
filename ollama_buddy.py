#!/usr/bin/env python3
"""Ollama Buddy - suivi de la consommation des modeles Ollama Cloud.

Lit le quota mensuel et la repartition par modele sur ollama.com/api/usage,
avec la cle API de l'utilisateur, et sert un tableau de bord sur 127.0.0.1.
Sans cle, aucune mesure n'existe : l'app n'affiche alors aucun chiffre.

Rien ne quitte la machine : les mesures restent en local (SQLite + HTTP local),
et seuls la cle et la requete d'usage partent vers ollama.com.
"""

from __future__ import annotations

import argparse
import calendar
import http.client
import json
import mimetypes
import os
import queue
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"

# L'app macOS embarque ce script dans son bundle (lecture seule en pratique) :
# l'index et la config vivent alors dans ~/Library/Application Support.
DATA_DIR = Path(os.environ.get("OLLAMA_BUDDY_DATA") or APP_DIR)
DB_PATH = DATA_DIR / "usage.db"
CONFIG_PATH = DATA_DIR / "config.json"

OLLAMA_API = "http://127.0.0.1:11434"
OLLAMA_CLOUD = "https://ollama.com"

# Un modele au-dela de ce slot est regroupe dans "Other" (la 9e serie n'a
# jamais de teinte inventee).
MAX_SLOTS = 8

# ollama.com/api/usage est interroge au plus une fois par minute : l'interface
# se rafraichit toutes les 2 secondes, on ne va pas suivre ce rythme.
CLOUD_TTL_SECONDS = 60

# La fenetre demandee a ollama.com. Le 07/10/2026, /api/usage a change de
# contrat : il ne renvoie plus la part du quota ni la repartition par modele,
# mais une consommation par heure ou par jour, en dollars. Les plages
# acceptees sont 24h, 7d et 30d ; trente jours couvrent un cycle entier, quel
# que soit son quantieme.
CLOUD_RANGE = "30d"

# Les compteurs de l'API sont des cumuls : un point toutes les cinq minutes
# suffit a reconstruire de l'horaire comme du journalier, sans noyer la base.
CLOUD_SAMPLE_SECONDS = 300


DAY_MS = 86_400_000

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "monthly_quota": 0,        # plafond mensuel en dollars ($60 chez Ollama Pro)
    "quota_reset_at": 0,       # prochaine reinitialisation, en ms epoch
    # Cle API ollama.com (https://ollama.com/settings/keys). Sans elle, aucun
    # chiffre de quota n'est publie : ollama.com est la seule source.
    "ollama_api_key": "",
    "port": 11499,
    # "auto" suit macOS, "light" et "dark" forcent le theme.
    "theme": "auto",
    # "auto" suit la langue du systeme, "fr" et "en" forcent. Le serveur ne
    # choisit pas a la place des clients : il ne connait ni la langue du
    # navigateur ni celle du systeme. Chacun resout "auto" de son cote.
    "lang": "auto",
    # Intervalle de veille. Le cache interne limite les appels a ollama.com a
    # une par minute, quelle que soit cette valeur.
    "watch_seconds": 2,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[config] illisible, valeurs par defaut ({exc})", file=sys.stderr)
    return cfg


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", "utf-8")
    # Le fichier peut contenir une cle API ollama.com : lisible par le seul
    # proprietaire, jamais par le reste de la machine.
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Ollama Cloud
# --------------------------------------------------------------------------


def ollama_account() -> dict | None:
    """Compte ollama.com (plan, email) via l'API locale."""
    try:
        req = urllib.request.Request(f"{OLLAMA_API}/api/me", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


# Dollars d'usage inclus par mois, par offre Ollama Cloud. Sert uniquement de
# valeur de depart : des qu'un plafond est saisi, c'est le sien qui compte.
# Ollama a deja change ses tarifs, donc cette table peut vieillir — le reglage
# manuel reste la sortie, et une offre absente de la table n'affiche aucun
# montant plutot qu'un montant faux.
PLAN_CAPS = {"pro": 60.0, "max": 300.0}


def monthly_cap(cfg: dict, account: dict | None) -> float:
    """Plafond mensuel en dollars : celui saisi, sinon celui de l'offre.

    Zero veut dire « pas saisi » : c'est la valeur par defaut. C'est aussi ce
    qui permet a une offre Max de ne pas afficher des montants calcules sur le
    plafond d'une Pro.
    """
    configured = float(cfg.get("monthly_quota") or 0)
    if configured > 0:
        return configured
    plan = str((account or {}).get("plan") or "").strip().lower()
    return PLAN_CAPS.get(plan, 0.0)


def cloud_usage(api_key: str) -> dict:
    """Usage du compte ollama.com, lu avec une cle API.

    Ollama n'expose l'usage ni en local ni dans sa documentation : le seul
    moyen programmable est cet endpoint, qui n'est pas documente — il a deja
    change de forme une fois, le 07/10/2026. D'ou une fonction qui ne leve
    jamais et renvoie toujours un dict : soit les donnees, soit
    `{"error": ...}` que l'interface affiche.
    """
    if not api_key:
        return {"error": "no key"}

    req = urllib.request.Request(
        f"{OLLAMA_CLOUD}/api/usage?range={CLOUD_RANGE}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "ollama-buddy",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"error": "key rejected"}
        return {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"error": type(exc).__name__}


# --------------------------------------------------------------------------
# Base de donnees
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_color (
    model TEXT PRIMARY KEY,
    slot  INTEGER NOT NULL
);

-- Echantillons de ollama.com/api/usage. Les compteurs de l'API sont des
-- cumuls du cycle en cours : c'est leur difference entre deux echantillons qui
-- donne l'activite d'un intervalle. `model` reste vide : l'API ne publie plus
-- le detail par modele depuis le 07/10/2026.
CREATE TABLE IF NOT EXISTS cloud_samples (
    ts       INTEGER NOT NULL,
    model    TEXT    NOT NULL,
    requests INTEGER NOT NULL,
    usage    REAL    NOT NULL,
    PRIMARY KEY (ts, model)
);
CREATE INDEX IF NOT EXISTS idx_cloud_samples_ts ON cloud_samples(ts);
"""


def connect() -> sqlite3.Connection:
    # Le serveur HTTP repond dans un thread par requete : la connexion est
    # partagee, et c'est State.lock qui serialise les acces.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    # Migration AVANT le schema : une base ancienne peut avoir des tables
    # que ce code ne cree plus, ou une colonne manquante.
    # pas de colonne client, et l'index correspondant echouerait.
    tables = {row[0] for row in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # Les transcripts Claude Code ne sont plus lus, et le quota ne se saisit
    # plus a la main : le montant colle des semaines plus tot etait affiche
    # comme le quota du jour. Ces tables n'ont plus de lecteur. Les remises a
    # zero observees partent aussi : l'observer regardait une chute de la part
    # du plafond, et plus rien ne retombe.
    for table in ("quota_readings", "events", "files", "quota_resets"):
        if table in tables:
            conn.execute(f"DROP TABLE {table}")
    if tables & {"quota_readings", "events", "files", "quota_resets"}:
        conn.commit()

    conn.executescript(SCHEMA)
    return conn


def month_shift(moment: datetime, months: int) -> datetime:
    """Decale d'un nombre de mois en conservant le quantieme."""
    index = moment.month - 1 + months
    year = moment.year + index // 12
    month = index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def cloud_days(cloud: dict | None) -> list[dict] | None:
    """Les jours publies par l'API : date, requetes, dollars, jetons.

    `None` quand la reponse n'est pas celle attendue — une forme inconnue ne
    doit pas se confondre avec « aucun usage ».

    C'est tout ce que /api/usage donne depuis le 07/10/2026. Il portait avant
    la part du quota et la repartition par modele ; ni l'une ni l'autre n'a
    ete reprise ailleurs, et aucun autre endpoint ne les publie.
    """
    buckets = (cloud or {}).get("buckets")
    if not isinstance(buckets, list):
        return None
    rows = []
    for bucket in buckets:
        debut = bucket.get("from")
        if not isinstance(debut, str):
            continue
        try:
            moment = datetime.fromisoformat(debut.replace("Z", "+00:00"))
        except ValueError:
            continue
        rows.append({
            "day": moment.astimezone(timezone.utc).date().isoformat(),
            "requests": int(bucket.get("request_count") or 0),
            "usd": float(bucket.get("usage_usd") or 0.0),
            "tokens": (int(bucket.get("input_tokens") or 0)
                       + int(bucket.get("output_tokens") or 0)),
        })
    rows.sort(key=lambda r: r["day"])
    return rows


def cloud_totals(cloud: dict | None) -> dict | None:
    """Totaux de la fenetre demandee, ou None si la reponse n'est pas la bonne.

    Leur presence distingue « la reponse a change de forme » de « aucun usage
    sur la periode », qui ne veulent pas dire la meme chose a l'ecran.
    """
    totaux = (cloud or {}).get("totals")
    return totaux if isinstance(totaux, dict) else None


def cycle_ratio(cap: float, cycle_start: int | None,
                cloud: dict | None) -> float | None:
    """Part du plafond consommee sur le cycle, ou None si elle ne se calcule pas.

    Peut depasser 1 : un depassement est une information, pas une erreur de
    calcul. C'est l'interface qui borne la jauge.
    """
    if cap <= 0:
        return None
    consume = cloud_cycle_usage(cloud, cycle_start)
    return (consume / cap) if consume is not None else None


def assign_model_colors(conn: sqlite3.Connection, names) -> None:
    """Attribue un slot de couleur stable a chaque modele (jamais selon son rang).

    La teinte est stockee en base : un modele garde la sienne d'une session a
    l'autre, meme s'il change de place dans le classement.
    """
    known = {r[0] for r in conn.execute("SELECT model FROM model_color")}
    used = {r[0] for r in conn.execute("SELECT slot FROM model_color")}
    free = [s for s in range(MAX_SLOTS) if s not in used]
    for model in sorted(set(names)):
        if model in known or not free:
            continue
        conn.execute("INSERT INTO model_color VALUES (?,?)", (model, free.pop(0)))
        known.add(model)
    conn.commit()


def fold_extra(rows: list[dict]) -> list[dict]:
    """Regroupe au-dela du dernier slot : jamais de 9e teinte inventee."""
    over = [r for r in rows if r["slot"] >= MAX_SLOTS]
    rows = [r for r in rows if r["slot"] < MAX_SLOTS]
    if not over:
        return rows
    rows.append({
        "model": f"Other ({len(over)})",
        # Le libelle affiche est compose par le client : seul lui sait dans
        # quelle langue la page se lit. Le serveur fournit de quoi le faire.
        "other_count": len(over),
        "slot": MAX_SLOTS,
        "total": sum(r["total"] for r in over),
        "messages": sum(r["messages"] for r in over),
        "other": True,
    })
    return rows


def last_model_reading(conn: sqlite3.Connection) -> tuple[int, list[dict]] | None:
    """Derniere repartition par modele lue, et quand.

    ollama.com ne la publie plus depuis le 07/10/2026 : elle vient des
    echantillons pris avant cette date, et ne bouge donc plus. Elle reste
    juste dans ses proportions — les compteurs sont des cumuls du cycle — et
    c'est sa date qui le dit, affichee a cote.
    """
    ts = conn.execute(
        "SELECT MAX(ts) FROM cloud_samples WHERE model <> ''").fetchone()[0]
    if not ts:
        return None
    rows = conn.execute(
        "SELECT model, requests FROM cloud_samples WHERE ts = ? AND model <> ''"
        " ORDER BY requests DESC", (ts,)).fetchall()
    return ts, [{"model": m, "requests": n} for m, n in rows]


def cycle_first_day(cycle_start: int | None) -> str | None:
    """Premier jour entier du cycle, en ISO. None sans debut de cycle.

    Les jours que publie l'API sont coupes a minuit UTC ; le cycle, lui,
    commence a une heure quelconque. On part donc du premier jour entier, et
    jamais de la journee charniere : elle appartient pour partie au cycle
    precedent, et la compter fausse tout. Verifie contre le site le
    07/10/2026, qui annoncait 59,81 $.
    """
    if not cycle_start:
        return None
    debut = datetime.fromtimestamp(cycle_start / 1000, timezone.utc)
    jour = debut.date()
    if (debut.hour, debut.minute, debut.second, debut.microsecond) != (0, 0, 0, 0):
        jour += timedelta(days=1)
    return jour.isoformat()


def cloud_cycle_usage(cloud: dict | None, cycle_start: int | None) -> float | None:
    """Dollars consommes depuis le debut du cycle, ou None si on ne sait pas.

    L'API ne donne plus la part du quota, mais la consommation de chaque jour :
    le cycle est donc la somme des jours depuis son debut.

    On part du premier jour entier du cycle, jamais de la journee charniere :
    les jours sont coupes a minuit UTC, la remise a zero tombe a une heure
    quelconque. Verifie contre le site le 07/10/2026 — il annoncait 59,81 $.
    Les jours entiers depuis le 12/09 donnent 59,80 $. La journee charniere
    comptee au prorata donnait 60,45 $, et comptee entiere 67,37 $.

    Sans debut de cycle, il n'y a pas de somme a faire : deviner reviendrait a
    publier un chiffre faux.
    """
    jours = cloud_days(cloud)
    premier = cycle_first_day(cycle_start)
    if jours is None or premier is None:
        return None
    return sum(j["usd"] for j in jours if j["day"] >= premier)


def next_occurrence(anchor: datetime, now: datetime) -> int:
    """Prochaine occurrence de `anchor`, en ms epoch. 0 si aucune.

    Le quantieme se rejoue de mois en mois, comme l'abonnement. `month_shift`
    borne le jour au dernier du mois : une remise a zero le 31 tombe le 30 en
    novembre.
    """
    if anchor > now:
        return int(anchor.timestamp() * 1000)
    # De mois en mois jusqu'a depasser aujourd'hui : robuste quand la date
    # saisie a plus d'un cycle de retard.
    for pas in range(1, 14):
        candidate = month_shift(anchor, pas)
        if candidate > now:
            return int(candidate.timestamp() * 1000)
    return 0


def reset_window(conn: sqlite3.Connection, cfg: dict,
                 now: datetime) -> tuple[int, int | None, str | None]:
    """Cycle en cours : (prochaine remise a zero, debut du cycle, origine).

    Une seule source : la date saisie dans les reglages, lue sur le site. Le
    quantieme se rejoue alors de mois en mois, comme l'abonnement.

    L'app a su la deduire de l'API, puis l'observer sur ses echantillons. Les
    deux sont mortes le 07/10/2026 : `activity.period` decrivait une fenetre
    glissante de quatre semaines — son `starting_at` avancait d'une semaine
    chaque semaine — et le nouveau /api/usage ne publie plus de cumul qui
    retombe a zero, donc plus rien a observer.

    Sans date saisie, il n'y en a pas, et l'interface se tait plutot que d'en
    inventer une. Elle rend son `origine` pour pouvoir le dire.
    """
    declared = int(cfg.get("quota_reset_at") or 0)
    if not declared:
        return 0, None, None

    anchor = datetime.fromtimestamp(declared / 1000, timezone.utc)
    resets_at = next_occurrence(anchor, now)
    if not resets_at:
        return 0, None, None
    # Le debut du cycle n'est pas publie : on le suppose un mois en arriere.
    fin = datetime.fromtimestamp(resets_at / 1000, timezone.utc)
    return resets_at, int(month_shift(fin, -1).timestamp() * 1000), "settings"

def record_cloud_sample(conn: sqlite3.Connection, cloud: dict | None, cycle_start: int | None,
                        cap: float) -> bool:
    """Enregistre un point de mesure, au plus un toutes les cinq minutes.

    La valeur conservee est la *part* du plafond consommee sur le cycle. C'est
    la meme grandeur qu'avant le 07/10/2026, quand l'API la publiait
    directement : les echantillons deja en base restent donc comparables, et
    c'est ce qui permet au rythme de traverser le changement d'API.
    """
    ratio = cycle_ratio(cap, cycle_start, cloud)
    if ratio is None:
        return False

    ts = int(time.time() * 1000)
    dernier = conn.execute("SELECT MAX(ts) FROM cloud_samples").fetchone()[0]
    if dernier and ts - dernier < CLOUD_SAMPLE_SECONDS * 1000:
        return False

    # Une seule ligne, globale : l'API ne publie plus le detail par modele.
    conn.execute("INSERT OR REPLACE INTO cloud_samples VALUES (?,?,?,?)",
                 (ts, "", int((cloud_totals(cloud) or {}).get("request_count") or 0), ratio))
    conn.commit()
    return True


def cloud_rate(conn: sqlite3.Connection,
               cycle_start: int | None) -> float | None:
    """Rythme de consommation du cycle en cours, en part du plafond par jour.

    Mesure sur les echantillons reels, donc il integre tous les clients — y
    compris ceux dont l'app ne voit jamais passer la requete. La fenetre part
    du debut du cycle : un echantillon d'avant la remise a zero ferait croire
    a une chute.
    """
    if not cycle_start:
        return None
    rows = conn.execute(
        """SELECT ts, usage FROM cloud_samples
           WHERE model = '' AND ts >= ? ORDER BY ts""",
        (cycle_start,),
    ).fetchall()
    if len(rows) < 2:
        return None

    # Une journee pleine au minimum. Sur trente minutes, une rafale suffit a
    # faire croire a 29 $/jour : la projection annoncait 240 $ au reset. Mieux
    # vaut ne rien dire tant que la mesure ne porte pas sur assez de temps.
    span = (rows[-1][0] - rows[0][0]) / DAY_MS
    if span < 1.0:
        return None
    part_par_jour = (rows[-1][1] - rows[0][1]) / span
    if part_par_jour <= 0:
        return None
    return part_par_jour


def build_quota(conn: sqlite3.Connection, cfg: dict, cloud: dict | None = None,
                account: dict | None = None) -> dict:
    """Etat du plafond mensuel, lu sur ollama.com quand une cle est fournie.

    Depuis le 07/10/2026, l'API ne publie plus la part du quota : elle publie
    la consommation de chaque jour, en dollars. Le cycle est donc leur somme
    depuis son debut, et la part est cette somme rapportee au plafond. Le
    debut du cycle vient des reglages — il porte desormais tout le calcul.

    Sans cle, ou sans debut de cycle, l'etat est neutre : aucun chiffre de
    quota n'est publie. Mieux vaut ne rien dire qu'afficher un a-peu-pres.
    """
    monthly = monthly_cap(cfg, account)
    now = datetime.now(timezone.utc)
    now_ms = int(time.time() * 1000)
    resets_at, cycle_start, reset_source = reset_window(conn, cfg, now)
    consume = cloud_cycle_usage(cloud, cycle_start)

    if consume is None or monthly <= 0:
        return quota_unavailable(conn, cfg, cloud, account)

    ratio = consume / monthly
    days_left = max(0.0, (resets_at - now_ms) / DAY_MS) if resets_at else None
    part_par_jour = cloud_rate(conn, cycle_start)
    rate = part_par_jour * monthly if part_par_jour else None

    return {
        "source": "api",
        "error": None,
        "key_set": True,
        "key_error": None,
        "monthly": monthly,
        # Ce qui est saisi, a part du plafond en vigueur : l'interface doit
        # pouvoir montrer « rien de saisi » sans confondre ca avec « zero ».
        "monthly_set": float(cfg.get("monthly_quota") or 0),
        "ratio": ratio,
        # Le montant vient de l'API telle quelle : plus de part multipliee par
        # le plafond, donc plus d'ecart avec le chiffre du site.
        "current": consume,
        "resets_at": resets_at,
        # Ce qui est saisi, a part de la date en vigueur : le champ des
        # reglages ne doit pas se remplir tout seul.
        "resets_set": int(cfg.get("quota_reset_at") or 0),
        "resets_source": reset_source,
        "days_left": days_left,
        # L'interface doit distinguer « la reponse est inattendue » de « il
        # manque la date de remise a zero » : deux causes, deux gestes.
        "cycle_known": True,
        "cycle_start": (datetime.fromtimestamp(cycle_start / 1000, timezone.utc).isoformat()
                        if cycle_start else None),
        "rate_per_day": rate,
        "projected_at_reset": (consume + rate * days_left
                               if rate and days_left is not None else None),
        "exhausted_in_days": (max(0.0, (monthly - consume) / rate) if rate else None),
    }


def quota_unavailable(conn: sqlite3.Connection, cfg: dict, cloud: dict | None = None,
                      account: dict | None = None) -> dict:
    """Etat neutre : aucun chiffre de quota n'a pu etre etabli.

    Meme jeu de cles que la branche API, mesures a None. Elles sont nommees
    explicitement : mini.html teste `days_left === null`, une cle absente
    donnerait « reset dans NaN j ».

    La date de reinitialisation, elle, reste renseignee : elle vient des
    reglages, pas de la mesure. C'est un fait, et il ne depend pas de la
    lecture du jour.
    """
    resets_at, cycle_start, reset_source = reset_window(conn, cfg, datetime.now(timezone.utc))
    now_ms = int(time.time() * 1000)
    return {
        "source": "indisponible",
        "error": None,
        "key_set": bool(str(cfg.get("ollama_api_key") or "").strip()),
        "key_error": (cloud or {}).get("error"),
        "monthly": monthly_cap(cfg, account),
        "monthly_set": float(cfg.get("monthly_quota") or 0),
        "ratio": None,
        "current": None,
        "resets_at": resets_at,
        "resets_set": int(cfg.get("quota_reset_at") or 0),
        "resets_source": reset_source,
        "days_left": (max(0.0, (resets_at - now_ms) / DAY_MS) if resets_at else None),
        # Sans debut de cycle, la somme des jours ne veut rien dire : c'est ce
        # qui manque, pas la reponse d'ollama.com.
        "cycle_known": cycle_start is not None,
        "cycle_start": None,
        "rate_per_day": None,
        "projected_at_reset": None,
        "exhausted_in_days": None,
    }


def empty_usage() -> dict:
    """Aucune source : pas de cle, donc pas de chiffres.

    L'app ne remonte plus aucune mesure partielle. Un chiffre qui ne couvre
    qu'une partie des clients laisserait croire a une mesure globale.
    """
    return {
        "unit": "jours",
        "days": [],
        "days_total": 0,
        "usd": 0.0,
        "requests": 0,
        "peak": 0.0,
        "models": [],
        "models_total": 0,
        "models_at": None,
        "generated_at": int(time.time() * 1000),
    }


def build_cloud_usage(conn: sqlite3.Connection, cfg: dict, cloud: dict) -> dict:
    """Consommation jour par jour, telle que la publie ollama.com.

    Remplace la repartition par modele : le 07/10/2026 l'API a cesse de la
    publier, et aucun autre endpoint ne la donne. Ce qui reste est le detail
    des jours — la seule ventilation que la cle API ouvre encore.
    """
    tous = cloud_days(cloud) or []
    _, cycle_start, _ = reset_window(conn, cfg, datetime.now(timezone.utc))
    premier = cycle_first_day(cycle_start)

    # Les jours du cycle, et eux seuls : la fenetre de trente jours deborde sur
    # le cycle precedent, et son total contredirait celui du quota.
    jours = [j for j in tous if premier and j["day"] >= premier]

    # La repartition par modele, elle, vient de la derniere lecture qui l'a
    # portee. La cle API ne la donne plus ; le site, lui, l'a toujours.
    lecture = last_model_reading(conn)
    modeles = []
    models_at = None
    if lecture:
        models_at, brut = lecture
        assign_model_colors(conn, [r["model"] for r in brut])
        slots = {m: s for m, s in conn.execute("SELECT model, slot FROM model_color")}
        # La part se calcule apres le repli : la ligne « Autres » n'existe
        # qu'a ce moment-la, et elle n'a pas de part a elle seule.
        modeles = fold_extra([
            {"model": r["model"], "slot": slots.get(r["model"], MAX_SLOTS),
             "total": r["requests"], "messages": r["requests"]}
            for r in brut
        ])
        total = sum(m["total"] for m in modeles)
        for m in modeles:
            m["share"] = (m["total"] / total) if total else 0.0

    return {
        "unit": "jours",
        "days": jours,
        "days_total": len(jours),
        "usd": sum(j["usd"] for j in jours),
        "requests": sum(j["requests"] for j in jours),
        # Le jour le plus charge donne l'echelle des barres.
        "peak": max((j["usd"] for j in jours), default=0.0),
        "models": modeles,
        "models_total": len(modeles),
        "models_at": models_at,
        "generated_at": int(time.time() * 1000),
    }


class State:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        # Reentrant : payload() appelle usage(), qui reprend le meme verrou.
        self.lock = threading.RLock()
        self.conn = connect()
        self.last_error: str | None = None
        self.subscribers: list[queue.Queue] = []
        self.revision = 0
        self.cloud_data: dict | None = None
        self.cloud_ts = 0.0

    def subscribe(self) -> queue.Queue:
        inbox: queue.Queue = queue.Queue(maxsize=8)
        self.subscribers.append(inbox)
        return inbox

    def unsubscribe(self, inbox: queue.Queue) -> None:
        try:
            self.subscribers.remove(inbox)
        except ValueError:
            pass

    def broadcast(self) -> None:
        for inbox in list(self.subscribers):
            try:
                inbox.put_nowait(self.revision)
            except queue.Full:
                pass  # client en retard : il se resynchronisera au prochain tour

    def invalidate_cloud(self) -> None:
        """Force la prochaine lecture a repasser par ollama.com."""
        with self.lock:
            self.cloud_data, self.cloud_ts = None, 0.0

    def cloud(self, account: dict | None = None) -> dict | None:
        """Usage ollama.com, mis en cache une minute. None si aucune cle."""
        api_key = str(self.cfg.get("ollama_api_key") or "")
        if not api_key:
            return None
        with self.lock:
            fresh = self.cloud_data is not None and time.time() - self.cloud_ts < CLOUD_TTL_SECONDS
            if fresh:
                return self.cloud_data
        data = cloud_usage(api_key)
        with self.lock:
            self.cloud_data, self.cloud_ts = data, time.time()
            if "error" not in data:
                try:
                    # L'echantillon est une part du plafond : il lui faut le
                    # debut du cycle et le plafond, tous deux pris maintenant.
                    _, cycle_start, _ = reset_window(self.conn, self.cfg,
                                                     datetime.now(timezone.utc))
                    record_cloud_sample(self.conn, data, cycle_start,
                                        monthly_cap(self.cfg, account))
                except sqlite3.Error as exc:   # un echantillon rate n'est pas fatal
                    print(f"[cloud] echantillon non enregistre ({exc})", file=sys.stderr)
        return data

    def payload(self) -> dict:
        """Une seule source : ollama.com, avec une cle.

        Sans cle il n'y a rien a montrer : on renvoie alors la forme vide,
        meme cle, mesures a None.
        """
        # Le compte est lu une fois : l'offre sert au plafond — donc a
        # l'echantillon — et part aussi telle quelle dans le payload.
        account = ollama_account()
        cloud = self.cloud(account)
        live = bool(cloud) and "error" not in (cloud or {})
        with self.lock:
            data = build_cloud_usage(self.conn, self.cfg, cloud) if live else empty_usage()
            data["quota"] = build_quota(self.conn, self.cfg, cloud, account)
        data["cloud"] = cloud
        data["account"] = account
        data["error"] = self.last_error
        data["theme"] = self.cfg.get("theme") or "auto"
        data["lang"] = self.cfg.get("lang") or "auto"
        return data


class Handler(BaseHTTPRequestHandler):
    state: State
    server_version = "OllamaBuddy"

    def log_message(self, fmt, *args):  # silence par defaut
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path in ("/", "/index.html", "/mini"):
            name = "mini.html" if url.path == "/mini" else "index.html"
            page = WEB_DIR / name
            if not page.exists():
                self._send(404, f"{name} introuvable".encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            return

        # Assets statiques (le logo). Le nom doit tenir en un segment : pas de
        # traversee de repertoire possible.
        name = url.path.lstrip("/")
        if name and "/" not in name and not name.startswith("api"):
            asset = WEB_DIR / name
            if asset.is_file():
                kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
                self._send(200, asset.read_bytes(), kind)
                return

        if url.path == "/health":
            self._json({"ok": True, "app": "ollama-buddy"})
            return

        if url.path == "/api/stream":
            self.stream()
            return

        if url.path == "/api/usage":
            self._json(self.state.payload())
            return

        if url.path == "/api/refresh":
            self.refresh_all()
            return

        self._json({"error": "not found"}, 404)

    def refresh_all(self) -> None:
        """Purge le cache et redemande l'usage a ollama.com.

        Le cache dure une minute : sans cette purge, un rafraichissement manuel
        afficherait la meme valeur qu'avant.
        """
        self.state.invalidate_cloud()
        self.state.broadcast()
        self._json({"ok": True, "error": self.state.last_error})

    def stream(self) -> None:
        """Flux SSE : le serveur pousse l'usage des qu'il change."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        inbox = self.state.subscribe()
        try:
            self._push(self.state.payload())
            while True:
                try:
                    inbox.get(timeout=15)
                    self._push(self.state.payload())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")  # commentaire : garde la connexion ouverte
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # onglet ferme, fenetre quittee
        finally:
            self.state.unsubscribe(inbox)

    def _push(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
        self.wfile.flush()

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/api/refresh":
            # Le GET existait seul : le bouton de l'interface faisait un POST et
            # tombait donc sur un 404, sans que rien ne le signale.
            self.refresh_all()
            return
        if url.path == "/api/quota":
            self.update_quota()
            return
        if url.path in ("/api/theme", "/api/lang"):
            self.update_prefs()
            return

    def update_prefs(self) -> None:
        """Reglages d'affichage : theme et langue.

        Les deux vivent dans le meme handler parce qu'ils se comportent pareil :
        une valeur contrainte, ecrite dans la config, puis poussee aux clients
        deja ouverts. Seuls les champs presents sont touches.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "corps invalide"}, 400)
            return

        cfg = self.state.cfg
        if "theme" in body:
            theme = str(body.get("theme") or "auto")
            if theme not in ("auto", "light", "dark"):
                self._json({"error": "theme inconnu"}, 400)
                return
            cfg["theme"] = theme
        if "lang" in body:
            lang = str(body.get("lang") or "auto")
            if lang not in ("auto", "en", "fr"):
                self._json({"error": "langue inconnue"}, 400)
                return
            cfg["lang"] = lang

        save_config(cfg)
        # Le theme et la langue voyagent dans le payload : sans cette purge et
        # ce reveil, une deuxieme fenetre garderait l'ancien reglage jusqu'a ce
        # que l'usage change d'elle-meme.
        self.state.broadcast()
        self._json({"ok": True, "theme": cfg.get("theme"),
                    "lang": cfg.get("lang")})

    def update_quota(self) -> None:
        """Plafond mensuel, date de reinitialisation, et cle API ollama.com."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "corps invalide"}, 400)
            return

        cfg = self.state.cfg
        if "monthly_quota" in body:
            try:
                cfg["monthly_quota"] = max(0.0, float(body["monthly_quota"] or 0))
            except (TypeError, ValueError):
                self._json({"error": "quota invalide"}, 400)
                return
        if "quota_reset_at" in body:
            try:
                cfg["quota_reset_at"] = max(0, int(body["quota_reset_at"] or 0))
            except (TypeError, ValueError):
                self._json({"error": "date invalide"}, 400)
                return
        if "ollama_api_key" in body:
            # Une cle changee doit etre testee tout de suite : sans purge, le
            # cache d'une minute continuerait de servir l'ancien resultat.
            cfg["ollama_api_key"] = str(body["ollama_api_key"] or "").strip()
            self.state.invalidate_cloud()
        save_config(cfg)

        with self.state.lock:
            # L'offre est repassee : vider le plafond doit rendre celui du plan
            # tout de suite, sans attendre le prochain envoi.
            account = ollama_account()
            quota = build_quota(self.state.conn, cfg, self.state.cloud(account),
                                account)
        self.state.broadcast()
        self._json({"ok": True, "quota": quota})


# --------------------------------------------------------------------------
# Entree
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Ollama Cloud dashboard")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    port = args.port or int(cfg.get("port") or DEFAULT_CONFIG["port"])

    state = State(cfg)

    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def watcher() -> None:
        """Redemande l'usage a ollama.com et pousse chaque changement.

        Le cache interne limite a une requete par minute, quelle que soit la
        frequence de passage : les tours suivants ne coutent rien. Une empreinte
        evite de reveiller l'interface quand rien n'a bouge.
        """
        interval = max(1.0, float(cfg.get("watch_seconds") or 2))
        fingerprint = None
        while True:
            time.sleep(interval)
            try:
                cloud = state.cloud(ollama_account())
            except Exception as exc:  # noqa: BLE001 - ne jamais tuer la boucle
                print(f"[veille] {exc}", file=sys.stderr)
                continue
            stamp = json.dumps(cloud, sort_keys=True) if cloud else None
            if stamp != fingerprint:
                fingerprint = stamp
                state.broadcast()

    threading.Thread(target=watcher, daemon=True).start()

    url = f"http://127.0.0.1:{port}/"
    print(f"Ollama Buddy -> {url}   (Ctrl+C to quit)")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
