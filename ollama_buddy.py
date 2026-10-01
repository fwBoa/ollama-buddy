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

# ollama.com/api/usage est interroge au plus une fois par minute : l'interface
# se rafraichit toutes les 2 secondes, on ne va pas suivre ce rythme.
CLOUD_TTL_SECONDS = 60

# Les compteurs de l'API sont des cumuls : un point toutes les cinq minutes
# suffit a reconstruire de l'horaire comme du journalier, sans noyer la base.
CLOUD_SAMPLE_SECONDS = 300

DAY_MS = 86_400_000

# Un modele au-dela de ce slot est regroupe dans "Other" (la 9e serie n'a
# jamais de teinte inventee).
MAX_SLOTS = 8


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


def cloud_usage(api_key: str) -> dict:
    """Usage du compte ollama.com, lu avec une cle API.

    Ollama n'expose l'usage ni en local ni dans sa documentation : le seul
    moyen programmable est cet endpoint, qui n'est pas documente et peut donc
    disparaitre. D'ou une fonction qui ne leve jamais et renvoie toujours un
    dict — soit les donnees, soit `{"error": ...}` que l'interface affiche.
    """
    if not api_key:
        return {"error": "no key"}

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

    # Migration AVANT le schema : une base ancienne peut avoir des tables
    # que ce code ne cree plus, ou une colonne manquante.
    # pas de colonne client, et l'index correspondant echouerait.
    tables = {row[0] for row in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # Les transcripts Claude Code ne sont plus lus : leurs tables n'ont plus
    # de lecteur. Le quota non plus ne se saisit plus a la main : un montant colle des semaines plus
    # tot etait affiche comme le quota du jour. Ses releves n'ont plus de
    # lecteur, la table part avec eux.
    for table in ("quota_readings", "events", "files"):
        if table in tables:
            conn.execute(f"DROP TABLE {table}")
    if tables & {"quota_readings", "events", "files"}:
        conn.commit()

    conn.executescript(SCHEMA)
    return conn



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
    """Regroupe au-dela du dernier slot : jamais de 9e teinte inventee.

    Seuls le total et le nombre de requetes sont sommes : l'API ne publie rien
    d'autre par modele.
    """
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


# --------------------------------------------------------------------------
# Agregation
# --------------------------------------------------------------------------



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

    # Une journee pleine au minimum. Sur trente minutes, une rafale suffit a
    # faire croire a 29 $/jour : la projection annoncait 240 $ au reset. Mieux
    # vaut ne rien dire tant que la mesure ne porte pas sur assez de temps.
    span = (rows[-1][0] - rows[0][0]) / DAY_MS
    if span < 1.0:
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


def empty_usage() -> dict:
    """Aucune source : pas de cle, donc pas de chiffres.

    L'app ne remonte plus aucune mesure partielle. Un chiffre qui ne couvre
    qu'une partie des clients laisserait croire a une mesure globale.
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

        Sans cle il n'y a rien a montrer : on renvoie alors la forme vide,
        meme cle, mesures a None.
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
            quota = build_quota(self.state.conn, cfg, self.state.cloud())
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
