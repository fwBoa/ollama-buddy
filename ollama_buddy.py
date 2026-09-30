#!/usr/bin/env python3
"""Ollama Buddy - suivi local de la consommation des modeles Ollama Cloud.

Lit les transcripts Claude Code (~/.claude/projects/**/*.jsonl), en extrait
l'usage en tokens par modele, et sert un tableau de bord sur 127.0.0.1.

Aucune donnee ne quitte la machine : tout reste en local (SQLite + HTTP local).
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

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
OLLAMA_API = "http://127.0.0.1:11434"
OLLAMA_CLOUD = "https://ollama.com"

# ollama.com/api/usage est interroge au plus une fois par minute : l'interface
# se rafraichit toutes les 2 secondes, on ne va pas suivre ce rythme.
CLOUD_TTL_SECONDS = 60

# Les compteurs de l'API sont des cumuls : un point toutes les cinq minutes
# suffit a reconstruire de l'horaire comme du journalier, sans noyer la base.
CLOUD_SAMPLE_SECONDS = 300

DAY_MS = 86_400_000

# Client attribue aux evenements deduits des transcripts Claude Code.
TRANSCRIPT_CLIENT = "Claude Code"

# Un modele au-dela de ce slot est regroupe dans "Autres" (la 9e serie n'a
# jamais de teinte inventee).
MAX_SLOTS = 8


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "daily_token_budget": 0,
    "monthly_quota": 0,        # plafond mensuel en dollars ($60 chez Ollama Pro)
    "quota_reset_at": 0,       # prochaine reinitialisation, en ms epoch
    # Cle API ollama.com (https://ollama.com/settings/keys). Sans elle, aucun
    # chiffre de quota n'est publie : ollama.com est la seule source.
    "ollama_api_key": "",
    "port": 11499,
    # Port du proxy. Mets 0 pour le desactiver.
    "proxy_port": 11439,
    # Lecture des transcripts Claude Code. A passer a false si tu fais aussi
    # passer Claude Code par le proxy : sinon ses requetes seraient comptees
    # deux fois, une par le proxy et une par les transcripts.
    "read_transcripts": True,
    # "auto" suit macOS, "light" et "dark" forcent le theme.
    "theme": "auto",
    # Intervalle de surveillance des transcripts. Le scan est incremental :
    # il ne relit que les octets ajoutes depuis le dernier passage.
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


def cloud_models() -> set[str] | None:
    """Noms des modeles heberges sur ollama.com, sans le suffixe :cloud.

    Renvoie None si le serveur Ollama ne repond pas (on ne filtre alors pas).
    """
    try:
        with urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None

    names: set[str] = set()
    for model in tags.get("models", []):
        if not model.get("remote_host"):
            continue
        for key in ("name", "model", "remote_model"):
            value = model.get(key)
            if value:
                names.add(value)
                names.add(value.removesuffix(":cloud"))
    return names


def ollama_account() -> dict | None:
    """Compte ollama.com (plan, email) via l'API locale."""
    try:
        req = urllib.request.Request(f"{OLLAMA_API}/api/me", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def cloud_usage(api_key: str) -> dict:
    """Usage du compte ollama.com, lu avec une cle API.

    Ollama n'expose l'usage ni en local ni dans sa documentation : le seul
    moyen programmable est cet endpoint, qui n'est pas documente et peut donc
    disparaitre. D'ou une fonction qui ne leve jamais et renvoie toujours un
    dict — soit les donnees, soit `{"error": ...}` que l'interface affiche.
    """
    if not api_key:
        return {"error": "aucune clé"}

    req = urllib.request.Request(
        f"{OLLAMA_CLOUD}/api/usage",
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
            return {"error": "clé refusée"}
        return {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"error": type(exc).__name__}


# --------------------------------------------------------------------------
# Base de donnees
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    message_id     TEXT PRIMARY KEY,
    ts             INTEGER NOT NULL,
    model          TEXT    NOT NULL,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_creation INTEGER NOT NULL DEFAULT 0,
    cache_read     INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    project        TEXT,
    client         TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model);
CREATE INDEX IF NOT EXISTS idx_events_client ON events(client);

CREATE TABLE IF NOT EXISTS files (
    path     TEXT PRIMARY KEY,
    mtime    REAL,
    size     INTEGER,
    offset   INTEGER
);

CREATE TABLE IF NOT EXISTS model_color (
    model TEXT PRIMARY KEY,
    slot  INTEGER NOT NULL
);

-- Echantillons de ollama.com/api/usage. Les compteurs de l'API sont des
-- cumuls du mois en cours : c'est leur difference entre deux echantillons qui
-- donne l'activite d'un intervalle. `model` vide = ligne globale.
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

    # Migration AVANT le schema : une base creee avant l'arrivee du proxy n'a
    # pas de colonne client, et l'index correspondant echouerait.
    tables = {row[0] for row in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "events" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "client" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN client TEXT")
            conn.execute("UPDATE events SET client = ? WHERE client IS NULL",
                         (TRANSCRIPT_CLIENT,))
            conn.commit()

    # Le quota ne se saisit plus a la main : un montant colle des semaines plus
    # tot etait affiche comme le quota du jour. Ses releves n'ont plus de
    # lecteur, la table part avec eux.
    if "quota_readings" in tables:
        conn.execute("DROP TABLE quota_readings")
        conn.commit()

    conn.executescript(SCHEMA)
    return conn


def iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def ingest(conn: sqlite3.Connection, verbose: bool = False) -> dict:
    """Indexe les transcripts Claude Code dans SQLite.

    Relit uniquement ce qui a grossi depuis le dernier passage. La deduplication
    est assuree par la cle primaire message_id : un meme message recopie dans
    plusieurs sessions n'est compte qu'une fois (facteur ~1,7x sur ce corpus).
    """
    if not CLAUDE_PROJECTS.exists():
        return {"files": 0, "parsed": 0, "inserted": 0, "skipped": 0}

    cloud = cloud_models()
    paths = sorted(CLAUDE_PROJECTS.glob("**/*.jsonl"))

    # Oublie les fichiers disparus (l'historique de consommation, lui, reste).
    if paths:
        marks = ",".join("?" * len(paths))
        conn.execute(f"DELETE FROM files WHERE path NOT IN ({marks})", [str(p) for p in paths])
    else:
        conn.execute("DELETE FROM files")

    stats = {"files": 0, "parsed": 0, "inserted": 0, "skipped": 0}
    batch: list[tuple] = []

    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue

        row = conn.execute(
            "SELECT size, offset FROM files WHERE path = ?", (str(path),)
        ).fetchone()

        start = 0
        if row and row[0] is not None and row[1] is not None and st.st_size >= row[1]:
            start = row[1]
            if start == st.st_size:
                continue  # rien de neuf, on ne touche pas au fichier

        stats["files"] += 1
        project = path.parent.name
        read = start
        leftover = b""

        try:
            with path.open("rb") as handle:
                handle.seek(start)
                for raw in handle:
                    read += len(raw)
                    if not raw.endswith(b"\n"):
                        leftover = raw  # ligne partielle : on la relira au prochain tour
                        read -= len(raw)
                        break
                    stats["parsed"] += 1
                    try:
                        entry = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    message = entry.get("message")
                    if not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    model = message.get("model")
                    msg_id = message.get("id")
                    ts = iso_to_ms(entry.get("timestamp"))
                    if not model or not msg_id or ts is None or model == "<synthetic>":
                        continue
                    if cloud is not None and model not in cloud:
                        continue
                    batch.append((
                        msg_id, ts, model,
                        usage.get("input_tokens") or 0,
                        usage.get("cache_creation_input_tokens") or 0,
                        usage.get("cache_read_input_tokens") or 0,
                        usage.get("output_tokens") or 0,
                        project,
                        TRANSCRIPT_CLIENT,
                    ))
        except OSError:
            continue

        if batch:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)", batch
            )
            stats["inserted"] += conn.total_changes - before
            batch.clear()

        conn.execute(
            "INSERT INTO files(path, mtime, size, offset) VALUES (?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, offset=excluded.offset",
            (str(path), st.st_mtime, st.st_size, read),
        )

    if batch:  # derniere ligne sans \n final
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)", batch)
        stats["inserted"] += conn.total_changes - before

    conn.commit()
    stats["skipped"] = stats["parsed"] - stats["inserted"]
    assign_colors(conn)
    if verbose:
        print(f"[ingest] {stats['files']} fichiers, {stats['parsed']} lignes, "
              f"{stats['inserted']} evenements neufs")
    return stats


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


def assign_colors(conn: sqlite3.Connection) -> None:
    """Slots des modeles vus dans les transcripts Claude Code."""
    assign_model_colors(conn, [m for (m,) in conn.execute("SELECT DISTINCT model FROM events")])


def fold_extra(rows: list[dict]) -> list[dict]:
    """Regroupe au-dela du dernier slot : jamais de 9e teinte inventee.

    Seuls le total et le nombre de requetes sont sommes : l'API ne publie rien
    d'autre par modele.
    """
    over = [r for r in rows if r["slot"] >= MAX_SLOTS]
    rows = [r for r in rows if r["slot"] < MAX_SLOTS]
    if not over:
        return rows
    rows.append({
        "model": f"Autres ({len(over)})",
        "slot": MAX_SLOTS,
        "total": sum(r["total"] for r in over),
        "messages": sum(r["messages"] for r in over),
        "other": True,
    })
    return rows


# --------------------------------------------------------------------------
# Agregation
# --------------------------------------------------------------------------

RANGES = {"today": 1, "7d": 7, "30d": 30, "all": None}


def range_bounds(key: str) -> tuple[int, int, int | None]:
    """Bornes (debut_ms, fin_ms, nb_jours) en heure locale.

    nb_jours vaut None pour "all" : l'etendue reelle n'est connue qu'en base.
    """
    now = datetime.now()
    end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    end = int(end_of_today.timestamp() * 1000)

    days = RANGES.get(key, 1)
    if days is None:
        return 0, end, None
    if days == 1:
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start_of_today.timestamp() * 1000), end, 1
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_of_day - timedelta(days=days - 1)
    return int(start.timestamp() * 1000), end, days


def build_series(conn: sqlite3.Connection, start: int, end: int, days: int,
                 cloud: set[str] | None, client: str | None = None) -> dict:
    """Serie temporelle par modele, granularite adaptative (jour ou semaine).

    Les buckets vides sont conserves pour que l'axe reste regulier.
    """
    granularity = "hour" if days <= 1 else ("day" if days <= 60 else "week")

    where, params = "ts < ? AND ts >= ?", [end, start]
    if client:
        where += " AND client = ?"
        params.append(client)

    rows = conn.execute(
        f"""SELECT ts, model, input_tokens + cache_creation + cache_read + output_tokens
            FROM events WHERE {where}""",
        params,
    ).fetchall()

    buckets: dict[datetime | date, dict] = {}

    def key_of(ts: int):
        moment = datetime.fromtimestamp(ts / 1000)
        if granularity == "hour":
            return moment.replace(minute=0, second=0, microsecond=0)
        day = moment.date()
        return day if granularity == "day" else day - timedelta(days=day.weekday())

    # Squelette de buckets vides, du debut a la fin de la periode.
    first_ts = start if start else min((r[0] for r in rows), default=end)
    last_ts = min(end - 1, int(time.time() * 1000))
    cursor, last = key_of(first_ts), key_of(last_ts)
    step = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[granularity]
    while cursor <= last:
        buckets[cursor] = {"date": cursor, "total": 0, "by_model": {}}
        cursor += step

    for ts, model, total in rows:
        if cloud is not None and model not in cloud:
            continue
        bucket = buckets.get(key_of(ts))
        if bucket is None:
            continue
        bucket["total"] += total
        bucket["by_model"][model] = bucket["by_model"].get(model, 0) + total

    def label(moment) -> str:
        if granularity == "hour":
            return moment.strftime("%Hh")
        if granularity == "week":
            return "sem. du " + moment.strftime("%d/%m")
        return moment.strftime("%d/%m")

    return {
        "granularity": granularity,
        "buckets": [
            {
                "date": b["date"].isoformat(),
                "label": label(b["date"]),
                "total": b["total"],
                "by_model": b["by_model"],
            }
            for b in buckets.values()
        ],
    }


def month_shift(moment: datetime, months: int) -> datetime:
    """Decale d'un nombre de mois en conservant le quantieme."""
    index = moment.month - 1 + months
    year = moment.year + index // 12
    month = index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def cloud_month_ratio(cloud: dict | None) -> float | None:
    """Part du quota mensuel consommee (0-1), telle que la publie l'API."""
    monthly = ((cloud or {}).get("limits") or {}).get("monthly") or {}
    usage = monthly.get("usage")
    return float(usage) if isinstance(usage, (int, float)) else None


def cloud_month_models(cloud: dict | None) -> list[dict]:
    """Nombre de requetes par modele sur le mois en cours."""
    monthly = ((cloud or {}).get("limits") or {}).get("monthly") or {}
    rows = []
    for entry in monthly.get("models") or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        count = entry.get("request_count")
        rows.append({"model": name, "requests": int(count or 0)})
    rows.sort(key=lambda r: (-r["requests"], r["model"]))
    return rows


def deduce_reset(cloud: dict | None, cfg: dict) -> int:
    """Prochaine remise a zero, deduite du cycle en cours.

    ollama.com ne publie pas cette date, mais publie le debut du cycle en
    cours (`activity.period.starting_at`). Le mois se rejoue a l'identique le
    mois suivant : sur ce compte, cycle ouvert le 07/09 et remise a zero le
    07/10, ce que confirme la page de reglages du site.

    Le quantieme ainsi deduit est celui de l'abonnement, pas celui du mois
    civil. Si l'API disparait, la date saisie a la main reprend la main.
    """
    fallback = int(cfg.get("quota_reset_at") or 0)
    period = ((cloud or {}).get("activity") or {}).get("period") or {}
    starting = period.get("starting_at")
    if not starting:
        return fallback

    try:
        anchor = datetime.fromisoformat(str(starting).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    # On avance de mois en mois jusqu'a depasser aujourd'hui : robuste si le
    # cycle a plus d'un mois de retard sur la date du jour.
    for step in range(1, 14):
        candidate = month_shift(anchor, step)
        if candidate > now:
            return int(candidate.timestamp() * 1000)
    return fallback


def record_cloud_sample(conn: sqlite3.Connection, cloud: dict | None) -> bool:
    """Enregistre un point de mesure, au plus un toutes les cinq minutes.

    Les cumuls bruts sont conserves tels quels : n'importe quelle granularite
    se recalcule ensuite a partir d'eux.
    """
    ratio = cloud_month_ratio(cloud)
    models = cloud_month_models(cloud)
    if ratio is None or not models:
        return False

    ts = int(time.time() * 1000)
    last = conn.execute("SELECT MAX(ts) FROM cloud_samples").fetchone()[0]
    if last and ts - last < CLOUD_SAMPLE_SECONDS * 1000:
        return False

    conn.executemany(
        "INSERT OR REPLACE INTO cloud_samples VALUES (?,?,?,?)",
        [(ts, m["model"], m["requests"], ratio) for m in models]
        + [(ts, "", sum(m["requests"] for m in models), ratio)],
    )
    conn.commit()
    return True


def cloud_rate(conn: sqlite3.Connection, resets_at: int) -> tuple[float | None, float | None]:
    """Rythme de consommation du cycle en cours : (part/jour, $/jour).

    Mesure sur les echantillons reels, donc il integre tous les clients —
    y compris ceux dont l'app ne voit jamais passer la requete.
    """
    if not resets_at:
        return None, None
    start = int(month_shift(datetime.fromtimestamp(resets_at / 1000), -1).timestamp() * 1000)
    rows = conn.execute(
        """SELECT ts, usage FROM cloud_samples
           WHERE model = '' AND ts >= ? ORDER BY ts""",
        (start,),
    ).fetchall()
    if len(rows) < 2:
        return None, None

    span = (rows[-1][0] - rows[0][0]) / DAY_MS
    if span <= 0.02:                        # deux points trop proches : bruit
        return None, None
    share_per_day = (rows[-1][1] - rows[0][1]) / span
    if share_per_day <= 0:
        return None, None
    return share_per_day, rows[-1][1]


def build_quota(conn: sqlite3.Connection, cfg: dict, cloud: dict | None = None) -> dict:
    """Etat du plafond mensuel, lu sur ollama.com quand une cle est fournie.

    L'API ne donne ni le montant en dollars ni la date de reinitialisation :
    `limits.monthly.usage` est une *part* du plafond, et la date se deduit du
    cycle en cours. Le reste (rythme, projection) se mesure sur les
    echantillons.

    Sans cle, l'etat est neutre : aucun chiffre de quota n'est publie. Mieux
    vaut ne rien dire qu'afficher comme mesure un montant colle des semaines
    plus tot.
    """
    monthly = float(cfg.get("monthly_quota") or 0)
    now_ms = int(time.time() * 1000)
    ratio = cloud_month_ratio(cloud)

    if ratio is None:
        return quota_unavailable(cfg, cloud)

    resets_at = deduce_reset(cloud, cfg)
    days_left = max(0.0, (resets_at - now_ms) / DAY_MS) if resets_at else None
    share_per_day, _ = cloud_rate(conn, resets_at)

    rate = share_per_day * monthly if share_per_day and monthly else None
    current = ratio * monthly if monthly else None
    period = ((cloud or {}).get("activity") or {}).get("period") or {}

    return {
        "source": "api",
        "error": None,
        "key_set": True,
        "key_error": None,
        "monthly": monthly,
        "ratio": ratio,
        "current": current,
        "resets_at": resets_at,
        "days_left": days_left,
        "cycle_start": period.get("starting_at"),
        "rate_per_day": rate,
        "projected_at_reset": (current + rate * days_left
                               if current is not None and rate and days_left is not None
                               else None),
        "exhausted_in_days": (max(0.0, (1.0 - ratio) / share_per_day)
                              if share_per_day else None),
    }


def quota_unavailable(cfg: dict, cloud: dict | None = None) -> dict:
    """Etat neutre : aucune part du plafond n'a pu etre lue.

    Meme jeu de cles que la branche API, mesures a None. Elles sont nommees
    explicitement : mini.html teste `days_left === null`, une cle absente
    donnerait « reset dans NaN j ».

    La date de reinitialisation, elle, reste renseignee. Elle vient des
    reglages, pas d'une mesure : c'est un fait connu, qui ne se perime pas.
    """
    resets_at = int(cfg.get("quota_reset_at") or 0)
    now_ms = int(time.time() * 1000)
    return {
        "source": "indisponible",
        "error": None,
        "key_set": bool(str(cfg.get("ollama_api_key") or "").strip()),
        "key_error": (cloud or {}).get("error"),
        "monthly": float(cfg.get("monthly_quota") or 0),
        "ratio": None,
        "current": None,
        "resets_at": resets_at,
        "days_left": (max(0.0, (resets_at - now_ms) / DAY_MS) if resets_at else None),
        "cycle_start": None,
        "rate_per_day": None,
        "projected_at_reset": None,
        "exhausted_in_days": None,
    }


def build_usage(conn: sqlite3.Connection, key: str, cfg: dict,
                client: str | None = None) -> dict:
    start, end, days = range_bounds(key)

    if days is None:
        # "Tout" : on se cale sur l'etendue reelle des donnees, pas sur le
        # 1er janvier - sinon le budget serait multiplie par des mois vides.
        oldest = conn.execute("SELECT MIN(ts) FROM events").fetchone()[0]
        if oldest:
            first_day = datetime.fromtimestamp(oldest / 1000).date()
            days = max(1, (datetime.now().date() - first_day).days + 1)
            start = int(datetime.combine(first_day, datetime.min.time()).timestamp() * 1000)
        else:
            days = 1

    where, params = "ts < ?", [end]
    if start:
        where += " AND ts >= ?"
        params.append(start)
    if client:
        where += " AND client = ?"
        params.append(client)

    totals = conn.execute(
        f"""SELECT model,
                   SUM(input_tokens), SUM(cache_creation), SUM(cache_read),
                   SUM(output_tokens), COUNT(*)
            FROM events WHERE {where} GROUP BY model""",
        params,
    ).fetchall()

    # Repartition par client, sur la meme periode mais sans le filtre de
    # client : c'est ce qui permet de passer de l'un a l'autre.
    client_where, client_params = "ts < ?", [end]
    if start:
        client_where += " AND ts >= ?"
        client_params.append(start)
    by_client = conn.execute(
        f"""SELECT COALESCE(client, ?) AS name,
                   SUM(input_tokens + cache_creation + cache_read + output_tokens),
                   COUNT(*)
            FROM events WHERE {client_where} GROUP BY name ORDER BY 2 DESC""",
        [TRANSCRIPT_CLIENT] + client_params,
    ).fetchall()

    cloud = cloud_models()
    slots = {m: s for m, s in conn.execute("SELECT model, slot FROM model_color")}

    rows = []
    for model, inp, cc, cr, out, count in totals:
        if cloud is not None and model not in cloud:
            continue
        total = (inp or 0) + (cc or 0) + (cr or 0) + (out or 0)
        slot = slots.get(model, MAX_SLOTS)
        rows.append({
            "model": model,
            "slot": slot,
            "total": total,
            "input": inp or 0,
            "cache_creation": cc or 0,
            "cache_read": cr or 0,
            "output": out or 0,
            "messages": count,
        })

    rows.sort(key=lambda r: (-r["total"], r["model"]))

    rows = fold_extra(rows)

    grand_total = sum(r["total"] for r in rows)
    for r in rows:
        r["share"] = (r["total"] / grand_total) if grand_total else 0.0

    # Periode precedente de meme duree, pour le delta ("+12 % vs periode precedente").
    previous_total = None
    if key != "all" and start:
        previous = conn.execute(
            """SELECT model, SUM(input_tokens + cache_creation + cache_read + output_tokens)
               FROM events WHERE ts < ? AND ts >= ? GROUP BY model""",
            (start, start - (end - start)),
        ).fetchall()
        previous_total = sum(t for m, t in previous if cloud is None or m in cloud)

    budget = int(cfg.get("daily_token_budget") or 0)
    capacity = budget * days if budget else 0

    return {
        "range": key,
        "days": days,
        "models": rows,
        "grand_total": grand_total,
        "messages": sum(r["messages"] for r in rows),
        "budget": budget,
        "capacity": capacity,
        "used_ratio": (grand_total / capacity) if capacity else None,
        "series": build_series(conn, start, end, days, cloud, client),
        "previous_total": previous_total,
        "client": client,
        "clients": [
            {"client": name, "total": total or 0, "messages": count}
            for name, total, count in by_client if total
        ],
        "cloud_filter_active": cloud is not None,
        "generated_at": int(time.time() * 1000),
    }


def empty_usage() -> dict:
    """Aucune source : pas de cle, donc pas de chiffres.

    L'usage de Claude Code n'est plus lu du tout. Il ne couvrait qu'un client
    sur onze, et le montrer laissait croire a une mesure globale.
    """
    return {
        "unit": "requetes",
        "models_total": 0,
        "cycle_days": 1,
        "cycle_start": None,
        "models": [],
        "grand_total": 0,
        "messages": 0,
        "generated_at": int(time.time() * 1000),
    }


def build_cloud_usage(conn: sqlite3.Connection, cloud: dict) -> dict:
    """Repartition par modele, telle que la publie ollama.com.

    Le classement est le cumul du mois en cours : il est donc complet des le
    premier appel, sans attendre d'historique. L'API ne publie aucun passe.
    """
    month = cloud_month_models(cloud)
    assign_model_colors(conn, [r["model"] for r in month])
    slots = {m: s for m, s in conn.execute("SELECT model, slot FROM model_color")}

    rows = [
        {
            "model": r["model"],
            "slot": slots.get(r["model"], MAX_SLOTS),
            "total": r["requests"],
            "messages": r["requests"],
        }
        for r in month
    ]
    rows = fold_extra(rows)
    grand_total = sum(r["total"] for r in rows)
    for r in rows:
        r["share"] = (r["total"] / grand_total) if grand_total else 0.0

    # Le cycle d'abonnement, pas le mois civil : c'est lui qui donne la
    # moyenne par jour. Sans lui, un cumul mensuel divise par un jour.
    cycle_start = (((cloud or {}).get("activity") or {}).get("period") or {}).get("starting_at")
    cycle_days = 1
    if cycle_start:
        try:
            moment = datetime.fromisoformat(str(cycle_start).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            cycle_days = max(1, int((datetime.now(timezone.utc) - moment).days) + 1)
        except ValueError:
            pass

    return {
        "unit": "requetes",
        "models_total": len(month),
        "cycle_days": cycle_days,
        "cycle_start": cycle_start,
        "models": rows,
        "grand_total": grand_total,
        "messages": grand_total,
        "generated_at": int(time.time() * 1000),
    }


# --------------------------------------------------------------------------
# Serveur HTTP
# --------------------------------------------------------------------------


class State:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        # Reentrant : payload() appelle usage(), qui reprend le meme verrou.
        self.lock = threading.RLock()
        self.conn = connect()
        self.cache: dict[str, dict] = {}
        self.last_error: str | None = None
        self.subscribers: list[queue.Queue] = []
        self.revision = 0
        self.cloud_data: dict | None = None
        self.cloud_ts = 0.0

    def refresh(self, force: bool = False) -> bool:
        """Reindexe les transcripts. True si de nouvelles donnees sont arrivees."""
        reading = bool(self.cfg.get("read_transcripts", True))
        with self.lock:
            try:
                stats = ingest(self.conn) if reading else {"inserted": 0}
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - ne jamais tuer le serveur
                self.last_error = str(exc)
                return False
            fresh = stats["inserted"] > 0
            if fresh or force:
                self.cache.clear()

        if fresh:
            self.revision += 1
            self.broadcast()
        return fresh

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

    def usage(self, key: str, client: str | None = None) -> dict:
        cache_key = f"{key}|{client or '*'}"
        with self.lock:
            if cache_key not in self.cache:
                self.cache[cache_key] = build_usage(self.conn, key, self.cfg, client)
            return self.cache[cache_key]

    def record_proxy_event(self, model: str, client: str, usage: dict) -> None:
        """Enregistre une requete observee par le proxy.

        Le modele est ramene a sa forme courte (`deepseek-v4.1-flash:cloud`
        devient `deepseek-v4.1-flash`) pour se cumuler avec les transcripts.
        """
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                (f"proxy-{uuid.uuid4()}", int(time.time() * 1000),
                 model.removesuffix(":cloud"),
                 usage["input"], usage["cache_creation"], usage["cache_read"],
                 usage["output"], None, client),
            )
            self.conn.commit()
            assign_colors(self.conn)
            self.cache.clear()
            self.revision += 1
        self.broadcast()

    def invalidate_cloud(self) -> None:
        """Force la prochaine lecture a repasser par ollama.com."""
        with self.lock:
            self.cloud_data, self.cloud_ts = None, 0.0

    def cloud(self) -> dict | None:
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
                    record_cloud_sample(self.conn, data)
                except sqlite3.Error as exc:   # un echantillon rate n'est pas fatal
                    print(f"[cloud] echantillon non enregistre ({exc})", file=sys.stderr)
        return data

    def payload(self) -> dict:
        """Une seule source : ollama.com, avec une cle.

        Sans cle il n'y a rien a montrer. L'usage de Claude Code n'est plus
        remonte : il ne couvrait qu'un client sur onze.
        """
        cloud = self.cloud()
        live = bool(cloud) and "error" not in (cloud or {})
        with self.lock:
            data = build_cloud_usage(self.conn, cloud) if live else empty_usage()
            data["quota"] = build_quota(self.conn, self.cfg, cloud)
        data["cloud"] = cloud
        data["account"] = ollama_account()
        data["error"] = self.last_error
        data["theme"] = self.cfg.get("theme") or "auto"
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
        """Relit les transcripts ET redemande l'usage a ollama.com.

        Le cache de la cle dure une minute : sans cette purge, un rafraichissement
        manuel afficherait la meme valeur qu'avant.
        """
        self.state.refresh(force=True)
        self.state.invalidate_cloud()
        self.state.broadcast()
        self._json({"ok": True, "error": self.state.last_error})

    def stream(self) -> None:
        """Flux SSE : le serveur pousse l'usage des qu'un transcript change."""
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
        if url.path == "/api/theme":
            self.update_theme()
            return
        if url.path != "/api/budget":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            budget = max(0, int(body.get("daily_token_budget") or 0))
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "corps invalide"}, 400)
            return

        self.state.cfg["daily_token_budget"] = budget
        save_config(self.state.cfg)
        self.state.cache.clear()
        self.state.broadcast()
        self._json({"ok": True, "daily_token_budget": budget})

    def update_theme(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "corps invalide"}, 400)
            return

        theme = str(body.get("theme") or "auto")
        if theme not in ("auto", "light", "dark"):
            self._json({"error": "theme inconnu"}, 400)
            return

        self.state.cfg["theme"] = theme
        save_config(self.state.cfg)
        self.state.cache.clear()
        self.state.broadcast()
        self._json({"ok": True, "theme": theme})

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
            quota = build_quota(self.state.conn, cfg, self.state.cloud())
        self.state.broadcast()
        self._json({"ok": True, "quota": quota})


# --------------------------------------------------------------------------
# Proxy : compte les tokens de TOUS les clients, pas seulement Claude Code
# --------------------------------------------------------------------------

# Le User-Agent est le signal le plus fiable pour nommer le client.
CLIENT_SIGNATURES = [
    ("claude",   "Claude Code"),
    ("codex",    "Codex"),
    ("opencode", "opencode"),
    ("chatgpt",  "ChatGPT"),
    ("copilot",  "Copilot"),
    ("continue", "Continue"),
    ("cursor",   "Cursor"),
    ("openwebui", "Open WebUI"),
    ("open-webui", "Open WebUI"),
    ("zed",      "Zed"),
    ("dsh",      "DSH"),
    ("hermes",   "Hermes"),
]

# A defaut de User-Agent reconnaissable, le chemin d'API trahit la famille.
API_FAMILIES = {
    "/v1/messages": "API Anthropic",
    "/v1/chat/completions": "API OpenAI",
    "/v1/completions": "API OpenAI",
    "/v1/embeddings": "API OpenAI",
    "/v1/responses": "API OpenAI",
    "/api/chat": "Ollama CLI",
    "/api/generate": "Ollama CLI",
}


def _harvest_usage(node, out: dict) -> None:
    """Parcourt un objet JSON et retient les compteurs de tokens rencontres.

    Les trois dialectes n'ont ni les memes noms de champs ni le meme endroit :
    Anthropic publie l'entree dans `message_start` et la sortie dans
    `message_delta`, OpenAI met tout dans `usage`, Ollama utilise
    `prompt_eval_count` / `eval_count`. On prend le maximum vu, ce qui est
    juste pour des compteurs qui ne font que croitre au fil du flux.
    """
    if isinstance(node, dict):
        for source in (node.get("usage"), node):
            if not isinstance(source, dict):
                continue
            for key, field in (
                ("input_tokens", "input"),
                ("prompt_tokens", "input"),
                ("prompt_eval_count", "input"),
                ("cache_read_input_tokens", "cache_read"),
                ("cache_creation_input_tokens", "cache_creation"),
                ("output_tokens", "output"),
                ("completion_tokens", "output"),
                ("eval_count", "output"),
            ):
                value = source.get(key)
                if isinstance(value, (int, float)) and value >= 0:
                    out[field] = max(out[field], int(value))
        for value in node.values():
            if isinstance(value, (dict, list)):
                _harvest_usage(value, out)
    elif isinstance(node, list):
        for value in node:
            _harvest_usage(value, out)


def extract_usage(payload: bytes, content_type: str) -> dict | None:
    """Extrait les compteurs d'une reponse, en flux ou en bloc."""
    text = payload.decode("utf-8", "ignore")
    out = {"input": 0, "cache_read": 0, "cache_creation": 0, "output": 0}

    if "event-stream" in content_type or text.lstrip().startswith("data:"):
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                _harvest_usage(json.loads(body), out)
            except json.JSONDecodeError:
                continue
    elif "json" in content_type:
        try:
            _harvest_usage(json.loads(text), out)
        except json.JSONDecodeError:
            return None
    else:
        # NDJSON : une reponse Ollama par ligne.
        found = False
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                _harvest_usage(json.loads(line), out)
                found = True
            except json.JSONDecodeError:
                continue
        if not found:
            return None

    if not any(out.values()):
        return None
    return out


class ProxyHandler(BaseHTTPRequestHandler):
    """Relais vers Ollama qui enregistre l'usage au passage."""

    state: "State"
    upstream = ("127.0.0.1", 11434)
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence
        pass

    def client_name(self) -> str:
        forced = self.headers.get("X-Ollama-Buddy-Client")
        if forced:
            return forced.strip()[:60] or "Autre"
        agent = (self.headers.get("User-Agent") or "").lower()
        for needle, name in CLIENT_SIGNATURES:
            if needle in agent:
                return name
        return API_FAMILIES.get(self.path.split("?")[0], "Autre")

    def do_GET(self) -> None:   # noqa: N802
        self.forward()

    def do_POST(self) -> None:  # noqa: N802
        self.forward()

    def do_DELETE(self) -> None:  # noqa: N802
        self.forward()

    def forward(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""

        client = self.client_name()
        model = None
        if body:
            try:
                model = json.loads(body).get("model")
            except (json.JSONDecodeError, AttributeError):
                model = None

        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in ("host", "connection", "content-length",
                                   "accept-encoding", "transfer-encoding")
        }
        headers["Host"] = "%s:%d" % self.upstream
        headers["Content-Length"] = str(len(body))

        upstream = http.client.HTTPConnection(*self.upstream, timeout=900)
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            self.send_error(502, f"Ollama injoignable : {exc}")
            upstream.close()
            return

        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        content_type = response.getheader("Content-Type") or ""
        collected: list[bytes] = []
        kept = 0
        limit = 12 * 1024 * 1024      # au-dela, on relaie sans plus accumuler

        try:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                if kept < limit:
                    collected.append(chunk)
                    kept += len(chunk)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            upstream.close()
            return
        finally:
            upstream.close()

        if model and response.status == 200 and collected:
            usage = extract_usage(b"".join(collected), content_type)
            if usage:
                self.state.record_proxy_event(model, client, usage)

    # Le proxy absorbe tout le trafic ; aucune route locale ici.
    def send_error_json(self, code: int, message: str) -> None:
        payload = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


# --------------------------------------------------------------------------
# Entree
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Tableau de bord Ollama Cloud")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="indexe puis affiche un resume, sans lancer le serveur")
    args = parser.parse_args()

    cfg = load_config()
    port = args.port or int(cfg.get("port") or DEFAULT_CONFIG["port"])

    if args.once:
        conn = connect()
        stats = ingest(conn, verbose=True)
        usage = build_usage(conn, "today", cfg)
        print(f"aujourd'hui : {usage['grand_total']:,} tokens "
              f"sur {len(usage['models'])} modeles")
        for row in usage["models"]:
            print(f"  {row['model']:<26} {row['total']:>14,}  {row['share']*100:5.1f} %")
        print(f"({stats['parsed']} lignes lues, {stats['skipped']} doublons ignores)")
        return 0

    state = State(cfg)
    state.refresh(force=True)

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
                cloud = state.cloud()
            except Exception as exc:  # noqa: BLE001 - ne jamais tuer la boucle
                print(f"[veille] {exc}", file=sys.stderr)
                continue
            stamp = json.dumps(cloud, sort_keys=True) if cloud else None
            if stamp != fingerprint:
                fingerprint = stamp
                state.broadcast()

    threading.Thread(target=watcher, daemon=True).start()

    url = f"http://127.0.0.1:{port}/"
    print(f"Ollama Buddy -> {url}   (Ctrl+C pour quitter)")

    # Proxy : compte l'usage de tous les clients, pas seulement Claude Code.
    proxy_port = int(cfg.get("proxy_port") or 0)
    if proxy_port:
        ProxyHandler.state = state
        try:
            proxy = ThreadingHTTPServer(("127.0.0.1", proxy_port), ProxyHandler)
        except OSError as exc:
            print(f"Proxy desactive (port {proxy_port} indisponible) : {exc}", file=sys.stderr)
        else:
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            print(f"Proxy Ollama -> http://127.0.0.1:{proxy_port}   "
                  f"(pointe tes clients ici pour les compter)")

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
