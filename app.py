from __future__ import annotations

import sqlite3
import base64
import csv
import json
import math
import os
import secrets
import time
import zlib
from io import BytesIO, StringIO
from contextlib import closing
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import click
from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import qrcode


# Dossier contenant ce fichier ; il sert de point de repère pour les fichiers du projet.
BASE_DIR = Path(__file__).resolve().parent
# Chemin absolu de la base SQLite utilisée par l'application.
DATABASE = Path(os.environ.get("TICKETS_DB_PATH", str(BASE_DIR / "tickets.db"))).expanduser()

# Instance principale de l'application web Flask.
app = Flask(__name__)
# Clé utilisée notamment par Flask pour signer les messages temporaires (flash).
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cle-developpement-a-remplacer")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=28800,
)
# La configuration centralise le chemin afin qu'il puisse être remplacé facilement.
app.config["DATABASE"] = DATABASE
app.config["WHATSAPP_VERIFY_TOKEN"] = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
app.config["WHATSAPP_ACCESS_TOKEN"] = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
app.config["WHATSAPP_PHONE_NUMBER_ID"] = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
app.config["WHATSAPP_GRAPH_VERSION"] = os.environ.get("WHATSAPP_GRAPH_VERSION", "v23.0")
app.config["AI_API_TOKEN"] = os.environ.get("AI_API_TOKEN", "")
app.config["SHWARY_API_BASE"] = os.environ.get("SHWARY_API_BASE", "https://api.shwary.com")
app.config["SHWARY_MERCHANT_ID"] = os.environ.get("SHWARY_MERCHANT_ID", "")
app.config["SHWARY_MERCHANT_KEY"] = os.environ.get("SHWARY_MERCHANT_KEY", "")
app.config["SHWARY_COUNTRY_CODE"] = os.environ.get("SHWARY_COUNTRY_CODE", "DRC")
app.config["SHWARY_CALLBACK_URL"] = os.environ.get("SHWARY_CALLBACK_URL", "")

DEFAULT_SETTINGS = {
    "agency_name": "Billetterie",
    "agency_address": "Agence principale",
    "agency_phone": "",
    "currency": "FC",
    "ticket_prefix": "BT",
    "ticket_footer": "Merci pour votre confiance. Bon voyage !",
    "paper_size": "A5",
    "theme": "navy",
}

CITIES = ("Likasi", "Lubumbashi", "Kolwezi", "Kasumbalesa")
FARE_ROUTES = tuple(
    (origin, destination)
    for index, origin in enumerate(CITIES)
    for destination in CITIES[index + 1:]
)


def get_db() -> sqlite3.Connection:
    """Retourne la connexion SQLite associée à la requête Flask courante."""
    if "db" not in g:
        database_path = Path(app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(database_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        user_columns = {row[1] for row in g.db.execute("PRAGMA table_info(users)")}
        if user_columns and "is_admin" not in user_columns:
            g.db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            first_user = g.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first_user:
                g.db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first_user[0],))
            g.db.commit()
        if user_columns and "is_active" not in user_columns:
            g.db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        reservation_columns = {row[1] for row in g.db.execute("PRAGMA table_info(reservations)")}
        if reservation_columns and "created_by" not in reservation_columns:
            g.db.execute("ALTER TABLE reservations ADD COLUMN created_by INTEGER REFERENCES users(id)")
        for column, declaration in {
            "cancelled_at": "TEXT",
            "cancel_reason": "TEXT",
            "cancelled_by": "INTEGER REFERENCES users(id)",
            "verification_token": "TEXT",
        }.items():
            if reservation_columns and column not in reservation_columns:
                g.db.execute(f"ALTER TABLE reservations ADD COLUMN {column} {declaration}")
        for row in g.db.execute("SELECT id FROM reservations WHERE verification_token IS NULL"):
            g.db.execute("UPDATE reservations SET verification_token = ? WHERE id = ?", (secrets.token_urlsafe(24), row["id"]))
        payment_columns = {row[1] for row in g.db.execute("PRAGMA table_info(payments)")}
        for column, declaration in {
            "received_by": "INTEGER REFERENCES users(id)",
            "voided_at": "TEXT",
            "void_reason": "TEXT",
            "voided_by": "INTEGER REFERENCES users(id)",
        }.items():
            if payment_columns and column not in payment_columns:
                g.db.execute(f"ALTER TABLE payments ADD COLUMN {column} {declaration}")
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS audit_logs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
                   action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                   phone TEXT PRIMARY KEY,
                   step TEXT NOT NULL DEFAULT 'ASK_ORIGIN',
                   data TEXT NOT NULL DEFAULT '{}',
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS whatsapp_messages (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   phone TEXT NOT NULL,
                   direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
                   message TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS payment_requests (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   reservation_id INTEGER NOT NULL REFERENCES reservations(id),
                   provider TEXT NOT NULL,
                   provider_transaction_id TEXT,
                   reference_id TEXT NOT NULL UNIQUE,
                   status TEXT NOT NULL DEFAULT 'PENDING',
                   amount REAL NOT NULL,
                   currency TEXT NOT NULL,
                   phone TEXT NOT NULL,
                   country_code TEXT NOT NULL,
                   checkout_payload TEXT,
                   failure_reason TEXT,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        g.db.commit()
    return g.db


def get_settings() -> dict[str, str]:
    """Retourne les préférences enregistrées, complétées par leurs valeurs par défaut."""
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    values = DEFAULT_SETTINGS.copy()
    values.update({row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")})
    return values


def fare_key(origin: str, destination: str) -> tuple[str, str] | None:
    """Normalise une liaison afin qu'un tarif unique s'applique dans les deux sens."""
    if origin not in CITIES or destination not in CITIES or origin == destination:
        return None
    return (origin, destination) if CITIES.index(origin) < CITIES.index(destination) else (destination, origin)


def get_fares() -> dict[tuple[str, str], float]:
    """Retourne les tarifs configurés par l'administrateur."""
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS fares (
               origin TEXT NOT NULL, destination TEXT NOT NULL,
               price REAL NOT NULL CHECK (price >= 0),
               PRIMARY KEY (origin, destination), CHECK (origin != destination)
           )"""
    )
    return {(row["origin"], row["destination"]): row["price"] for row in db.execute("SELECT * FROM fares")}


def audit(action: str, details: str = "") -> None:
    """Ajoute une opération au journal sans exposer de données sensibles."""
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS audit_logs (
               id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
               action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    db.execute(
        "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
        (g.user["id"] if getattr(g, "user", None) else None, action, details[:500]),
    )


def csrf_token() -> str:
    """Crée le jeton CSRF associé à la session courante."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.before_request
def protect_post_requests():
    """Refuse toute écriture ne provenant pas d'un formulaire de la session."""
    if request.method == "POST":
        if request.path in {"/webhooks/whatsapp", "/webhooks/shwary"} or request.path.startswith("/api/ai/"):
            return
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            abort(400)


@app.teardown_appcontext
def close_db(_error=None):
    """Ferme la connexion SQLite à la fin du contexte applicatif."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def load_logged_in_user():
    """Charge dans le contexte le compte associé à la session, s'il existe."""
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute("SELECT id, username, is_admin, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if g.user is None or not g.user["is_active"]:
            g.user = None
            session.clear()


@app.context_processor
def inject_current_user():
    """Rend le compte courant disponible dans les gabarits Jinja."""
    return {"current_user": getattr(g, "user", None), "app_settings": get_settings(), "csrf_token": csrf_token}


def login_required(view):
    """Redirige vers la connexion lorsqu'une route requiert une session active."""
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Veuillez vous connecter pour accéder à la billetterie.", "error")
            return redirect(url_for("login", next=request.path))
        return view(**kwargs)

    return wrapped_view


def admin_required(view):
    """Réserve une route aux administrateurs authentifiés."""
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(**kwargs)
    return wrapped_view


def valid_username(username: str) -> bool:
    """Valide un identifiant simple, lisible et compatible avec l'interface."""
    return 3 <= len(username) <= 80 and all(character.isalnum() or character in "._-" for character in username)


def safe_next_url(next_url: str | None) -> str | None:
    """N'autorise que les redirections internes après une connexion réussie."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return None


def init_db() -> None:
    """Crée les tables et index définis dans le script SQL du projet."""
    schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    database_path = Path(app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as db:
        db.executescript(schema)
        db.commit()


@app.template_filter("datefr")
def format_date(value: str | None) -> str:
    """Convertit une date ISO en format français, sans bloquer si elle est invalide."""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y à %H:%M")
    except ValueError:
        return value


def ticket_number(reservation_id: int, prefix: str = "BT") -> str:
    """Construit un numéro de billet à partir de la date et de son identifiant."""
    return f"{prefix}-{datetime.now():%Y%m%d}-{reservation_id:05d}"


def qr_code_data_uri(value: str) -> str:
    """Génère un QR Code PNG autonome pouvant être affiché dans le billet."""
    image = qrcode.make(value)
    output = BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def pdf_text(value: object) -> str:
    """Prepare un texte simple compatible avec le PDF genere sans dependance externe."""
    text = str(value if value is not None else "")
    replacements = {"→": "->", "✅": "", "👋": "", "—": "-"}
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_ticket_pdf(reservation: sqlite3.Row) -> bytes:
    """Genere un billet PDF simple avec les informations essentielles et un QR Code."""
    settings = get_settings()
    verify_url = url_for("verify_ticket", token=reservation["verification_token"], _external=True)
    qr_image = qrcode.make(verify_url).convert("RGB").resize((132, 132))
    qr_width, qr_height = qr_image.size
    qr_stream = zlib.compress(qr_image.tobytes())
    lines = [
        (settings["agency_name"], 20, 390),
        ("BILLET DE VOYAGE", 16, 365),
        (f"Numero : {reservation['ticket_number']}", 11, 335),
        (f"Passager : {reservation['customer_name']}", 11, 315),
        (f"Trajet : {reservation['origin']} -> {reservation['destination']}", 11, 295),
        (f"Depart : {format_date(reservation['departure_at'])}", 11, 275),
        (f"Siege : {reservation['seat_number']}", 11, 255),
        (f"Prix : {reservation['amount']:.0f} {settings['currency']}", 11, 235),
        (f"Statut : {reservation['status']}", 11, 215),
        (f"Agence : {settings['agency_name']}", 10, 190),
        (settings["agency_phone"], 9, 176),
        ("QR Code de controle", 10, 155),
        (settings["ticket_footer"], 9, 25),
    ]
    text_commands = ["BT", "/F1 11 Tf"]
    for text, size, y in lines:
        text_commands.append(f"/F1 {size} Tf")
        text_commands.append(f"38 {y} Td ({pdf_text(text)}) Tj")
        text_commands.append(f"-38 {-y} Td")
    text_commands.append("ET")
    content = "\n".join(text_commands) + "\nq 132 0 0 132 38 45 cm /Im1 Do Q\n"
    content_bytes = content.encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 420] /Resources << /Font << /F1 4 0 R >> /XObject << /Im1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        (
            f"<< /Type /XObject /Subtype /Image /Width {qr_width} /Height {qr_height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(qr_stream)} >>"
        ).encode("ascii") + b"\nstream\n" + qr_stream + b"\nendstream",
        f"<< /Length {len(content_bytes)} >>".encode("ascii") + b"\nstream\n" + content_bytes + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_at = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def api_error(message: str, status_code: int = 400):
    """Retourne une erreur JSON lisible par une application externe."""
    return jsonify({"error": message}), status_code


def api_login_required(view):
    """Protege les routes API sans redirection vers la page de connexion."""
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return api_error("Connexion requise.", 401)
        return view(**kwargs)

    return wrapped_view


def api_admin_required(view):
    """Reserve une route API aux administrateurs."""
    @wraps(view)
    @api_login_required
    def wrapped_view(**kwargs):
        if not g.user["is_admin"]:
            return api_error("Droits administrateur requis.", 403)
        return view(**kwargs)

    return wrapped_view


def ai_api_required(view):
    """Protège les routes destinées à une IA externe avec un jeton Bearer."""
    @wraps(view)
    def wrapped_view(**kwargs):
        expected = app.config["AI_API_TOKEN"]
        if not expected:
            return api_error("AI_API_TOKEN non configure.", 503)
        authorization = request.headers.get("Authorization", "")
        provided = request.headers.get("X-AI-Token", "")
        if authorization.startswith("Bearer "):
            provided = authorization.removeprefix("Bearer ").strip()
        if not provided or not secrets.compare_digest(provided, expected):
            return api_error("Jeton IA invalide.", 401)
        return view(**kwargs)

    return wrapped_view


def json_payload() -> dict:
    """Lit un corps JSON objet."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400)
    return payload


def serialize_trip(row: sqlite3.Row) -> dict:
    """Convertit un trajet SQLite en objet JSON."""
    available_seats = row["available_seats"] if "available_seats" in row.keys() else None
    return {
        "id": row["id"],
        "origin": row["origin"],
        "destination": row["destination"],
        "departure_at": row["departure_at"],
        "seat_count": row["seat_count"],
        "available_seats": available_seats,
        "price": row["price"],
        "created_at": row["created_at"],
    }


def serialize_reservation(row: sqlite3.Row) -> dict:
    """Convertit une reservation enrichie en objet JSON."""
    return {
        "id": row["id"],
        "ticket_number": row["ticket_number"],
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"],
        "seat_number": row["seat_number"],
        "amount": row["amount"],
        "status": row["status"],
        "created_at": row["created_at"],
        "verification_url": url_for("verify_ticket", token=row["verification_token"], _external=True) if row["verification_token"] else None,
        "trip": {
            "id": row["trip_id"],
            "origin": row["origin"],
            "destination": row["destination"],
            "departure_at": row["departure_at"],
        },
    }


def reservation_with_trip(reservation_id: int) -> sqlite3.Row | None:
    """Retourne une reservation avec les informations du trajet."""
    return get_db().execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id WHERE r.id = ?""",
        (reservation_id,),
    ).fetchone()


def city_from_text(value: str) -> str | None:
    """Reconnaît une ville autorisée depuis un message court."""
    normalized = value.strip().casefold()
    for city in CITIES:
        if normalized == city.casefold():
            return city
    return None


def whatsapp_tables_ready() -> None:
    """Prépare les tables utilisées par le bot WhatsApp."""
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS whatsapp_conversations (
               phone TEXT PRIMARY KEY,
               step TEXT NOT NULL DEFAULT 'ASK_ORIGIN',
               data TEXT NOT NULL DEFAULT '{}',
               updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS whatsapp_messages (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               phone TEXT NOT NULL,
               direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
               message TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )


def whatsapp_log(phone: str, direction: str, message: str) -> None:
    """Garde une trace simple des échanges WhatsApp."""
    whatsapp_tables_ready()
    get_db().execute(
        "INSERT INTO whatsapp_messages (phone, direction, message) VALUES (?, ?, ?)",
        (phone[:40], direction, message[:2000]),
    )
    get_db().commit()


def whatsapp_conversation(phone: str) -> tuple[str, dict]:
    """Retourne l'étape de conversation courante d'un client WhatsApp."""
    whatsapp_tables_ready()
    row = get_db().execute("SELECT step, data FROM whatsapp_conversations WHERE phone = ?", (phone,)).fetchone()
    if row is None:
        return "ASK_ORIGIN", {}
    try:
        data = json.loads(row["data"])
    except json.JSONDecodeError:
        data = {}
    return row["step"], data if isinstance(data, dict) else {}


def save_whatsapp_conversation(phone: str, step: str, data: dict) -> None:
    """Enregistre l'étape courante d'un client WhatsApp."""
    whatsapp_tables_ready()
    get_db().execute(
        """INSERT INTO whatsapp_conversations (phone, step, data, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(phone) DO UPDATE SET
               step = excluded.step,
               data = excluded.data,
               updated_at = CURRENT_TIMESTAMP""",
        (phone, step, json.dumps(data, ensure_ascii=False)),
    )
    get_db().commit()


def reset_whatsapp_conversation(phone: str) -> None:
    """Redémarre le dialogue d'un client."""
    save_whatsapp_conversation(phone, "ASK_ORIGIN", {})


def whatsapp_city_prompt() -> str:
    """Message de sélection des villes."""
    cities = "\n".join(f"- {city}" for city in CITIES)
    return (
        "Bonjour 👋 Bienvenue à la billetterie.\n"
        "Je peux vous aider à réserver un billet.\n\n"
        "Dites-moi d'abord votre ville de départ :\n"
        f"{cities}\n\n"
        "Si vous voulez recommencer, écrivez simplement MENU."
    )


def available_trips_for_bot(origin: str, destination: str) -> list[sqlite3.Row]:
    """Liste les prochains trajets disponibles pour le bot."""
    return get_db().execute(
        """SELECT t.*, t.seat_count - COUNT(r.id) AS available_seats
           FROM trips t
           LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
           WHERE t.origin = ? AND t.destination = ? AND datetime(t.departure_at) >= datetime('now', '-1 day')
           GROUP BY t.id
           HAVING available_seats > 0
           ORDER BY t.departure_at
           LIMIT 8""",
        (origin, destination),
    ).fetchall()


def create_bot_reservation(trip_id: int, customer_name: str, customer_phone: str, source: str = "WHATSAPP") -> sqlite3.Row:
    """Crée une réservation issue du bot avec attribution automatique du siège."""
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        raise ValueError("Trajet introuvable.")
    db.execute("BEGIN IMMEDIATE")
    try:
        occupied = {
            row["seat_number"] for row in db.execute(
                "SELECT seat_number FROM reservations WHERE trip_id = ? AND status != 'ANNULE'",
                (trip_id,),
            )
        }
        seat_number = next((number for number in range(1, trip["seat_count"] + 1) if number not in occupied), None)
        if seat_number is None:
            raise ValueError("Ce trajet est complet.")
        cursor = db.execute(
            """INSERT INTO reservations
               (trip_id, customer_name, customer_phone, seat_number, amount, verification_token)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (trip_id, customer_name, customer_phone, seat_number, trip["price"], secrets.token_urlsafe(24)),
        )
        reservation_id = cursor.lastrowid
        prefix = get_settings()["ticket_prefix"]
        db.execute("UPDATE reservations SET ticket_number = ? WHERE id = ?", (ticket_number(reservation_id, prefix), reservation_id))
        audit(f"{source}_RESERVATION_CREATED", f"Reservation {reservation_id}; siege {seat_number}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    reservation = reservation_with_trip(reservation_id)
    if reservation is None:
        raise ValueError("Reservation introuvable apres creation.")
    return reservation


def format_bot_trip(row: sqlite3.Row, index: int) -> str:
    """Formate un trajet en une ligne lisible dans WhatsApp."""
    settings = get_settings()
    return (
        f"{index}. {row['origin']} → {row['destination']}\n"
        f"   Départ : {format_date(row['departure_at'])}\n"
        f"   Places restantes : {row['available_seats']}\n"
        f"   Prix : {row['price']:.0f} {settings['currency']}"
    )


def whatsapp_bot_reply(phone: str, incoming_text: str) -> str:
    """Produit la réponse du bot et modifie l'état de conversation."""
    text = incoming_text.strip()
    lowered = text.casefold()
    if lowered in {"bonjour", "salut", "hello", "menu", "start", "aide", "annuler"}:
        reset_whatsapp_conversation(phone)
        return whatsapp_city_prompt()

    step, data = whatsapp_conversation(phone)
    if step == "ASK_ORIGIN":
        origin = city_from_text(text)
        if origin is None:
            return whatsapp_city_prompt()
        data = {"origin": origin}
        save_whatsapp_conversation(phone, "ASK_DESTINATION", data)
        return f"D'accord, départ depuis {origin}.\n\nQuelle est votre destination ?\n" + "\n".join(f"- {city}" for city in CITIES if city != origin)

    if step == "ASK_DESTINATION":
        destination = city_from_text(text)
        origin = data.get("origin")
        if destination is None or destination == origin:
            return "Je n'ai pas reconnu cette destination. Choisissez une ville dans la liste :\n" + "\n".join(f"- {city}" for city in CITIES if city != origin)
        trips = available_trips_for_bot(origin, destination)
        if not trips:
            reset_whatsapp_conversation(phone)
            return f"Désolé, je ne vois aucun trajet disponible pour {origin} → {destination} pour le moment.\n\nEnvoyez MENU pour essayer un autre trajet."
        data["destination"] = destination
        data["trip_options"] = [trip["id"] for trip in trips]
        save_whatsapp_conversation(phone, "ASK_TRIP", data)
        trip_lines = "\n\n".join(format_bot_trip(trip, index) for index, trip in enumerate(trips, 1))
        return f"Parfait, voici les trajets disponibles :\n\n{trip_lines}\n\nRépondez avec le numéro du trajet qui vous convient. Exemple : 1"

    if step == "ASK_TRIP":
        options = data.get("trip_options", [])
        if not isinstance(options, list) or not options:
            reset_whatsapp_conversation(phone)
            return whatsapp_city_prompt()
        try:
            choice = int(text)
        except ValueError:
            return "Je n'ai pas compris le choix. Répondez juste avec le numéro du trajet. Exemple : 1"
        if choice < 1 or choice > len(options):
            return "Ce numéro n'est pas dans la liste. Envoyez le numéro du trajet choisi."
        data["trip_id"] = options[choice - 1]
        save_whatsapp_conversation(phone, "ASK_NAME", data)
        return "Très bien 👍\nEnvoyez maintenant le nom complet du passager."

    if step == "ASK_NAME":
        customer_name = " ".join(text.split())
        if len(customer_name) < 3:
            return "Le nom semble trop court. Envoyez le nom complet du passager, s'il vous plaît."
        try:
            reservation = create_bot_reservation(int(data["trip_id"]), customer_name, phone)
        except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            reset_whatsapp_conversation(phone)
            return f"Désolé, je n'ai pas pu créer la réservation : {exc}\n\nEnvoyez MENU pour recommencer."
        reset_whatsapp_conversation(phone)
        verify_url = url_for("verify_ticket", token=reservation["verification_token"], _external=True)
        pdf_url = url_for("ticket_pdf", token=reservation["verification_token"], _external=True)
        payment_message = "Paiement : un agent va vous indiquer la procédure."
        if shwary_configured():
            try:
                payment_request = start_shwary_payment(reservation, phone)
                payment_message = (
                    "Paiement : une demande Shwary vient d'être envoyée sur votre téléphone. "
                    f"Référence : {payment_request['reference_id']}"
                )
            except Exception:
                payment_message = "Paiement : la demande automatique n'a pas pu partir. Un agent va vous aider."
        return (
            "C'est bon, votre réservation est créée ✅\n\n"
            f"Billet : {reservation['ticket_number']}\n"
            f"Passager : {reservation['customer_name']}\n"
            f"Trajet : {reservation['origin']} → {reservation['destination']}\n"
            f"Départ : {format_date(reservation['departure_at'])}\n"
            f"Siège : {reservation['seat_number']}\n"
            f"Prix : {reservation['amount']:.0f} {get_settings()['currency']}\n"
            f"Statut : {reservation['status']}\n\n"
            f"{payment_message}\n"
            "Votre billet sera confirmé après le paiement.\n"
            f"Billet PDF : {pdf_url}\n"
            f"Lien de contrôle : {verify_url}\n\n"
            "Merci et bon voyage."
        )

    reset_whatsapp_conversation(phone)
    return whatsapp_city_prompt()


def extract_whatsapp_text_messages(payload: dict) -> list[tuple[str, str]]:
    """Extrait les messages texte entrants du format webhook WhatsApp."""
    extracted: list[tuple[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                phone = str(message.get("from", "")).strip()
                if message.get("type") == "text":
                    text = str(message.get("text", {}).get("body", "")).strip()
                else:
                    text = ""
                if phone and text:
                    extracted.append((phone, text))
    return extracted


def send_whatsapp_text(phone: str, message: str) -> bool:
    """Envoie un message texte avec WhatsApp Cloud API si les secrets sont configurés."""
    access_token = app.config["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = app.config["WHATSAPP_PHONE_NUMBER_ID"]
    if not access_token or not phone_number_id:
        return False
    url = f"https://graph.facebook.com/{app.config['WHATSAPP_GRAPH_VERSION']}/{phone_number_id}/messages"
    body = json.dumps({
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }).encode("utf-8")
    api_request = urlrequest.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(api_request, timeout=15):
            return True
    except urlerror.URLError:
        return False


def payment_requests_ready() -> None:
    """Prepare la table de suivi des paiements reseau."""
    get_db().execute(
        """CREATE TABLE IF NOT EXISTS payment_requests (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               reservation_id INTEGER NOT NULL REFERENCES reservations(id),
               provider TEXT NOT NULL,
               provider_transaction_id TEXT,
               reference_id TEXT NOT NULL UNIQUE,
               status TEXT NOT NULL DEFAULT 'PENDING',
               amount REAL NOT NULL,
               currency TEXT NOT NULL,
               phone TEXT NOT NULL,
               country_code TEXT NOT NULL,
               checkout_payload TEXT,
               failure_reason TEXT,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )


def normalize_payment_phone(phone: str) -> str:
    """Normalise un numero client pour un paiement mobile money."""
    cleaned = "".join(character for character in phone if character.isdigit() or character == "+")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("0"):
        cleaned = "+243" + cleaned[1:]
    if cleaned.startswith("243"):
        cleaned = "+" + cleaned
    return cleaned


def shwary_configured() -> bool:
    """Indique si les identifiants Shwary sont disponibles."""
    return bool(app.config["SHWARY_MERCHANT_ID"] and app.config["SHWARY_MERCHANT_KEY"])


def shwary_headers() -> dict[str, str]:
    """Construit les en-tetes d'authentification Shwary."""
    return {
        "Content-Type": "application/json",
        "x-merchant-id": app.config["SHWARY_MERCHANT_ID"],
        "x-merchant-key": app.config["SHWARY_MERCHANT_KEY"],
    }


def shwary_status_from_payload(payload: dict) -> str:
    """Normalise un statut Shwary en statut interne."""
    raw_status = str(
        payload.get("status")
        or payload.get("transactionStatus")
        or payload.get("paymentStatus")
        or payload.get("state")
        or ""
    ).strip().casefold()
    if raw_status in {"completed", "success", "successful", "paid", "approved", "succeeded"}:
        return "COMPLETED"
    if raw_status in {"failed", "failure", "cancelled", "canceled", "rejected", "expired"}:
        return "FAILED"
    return "PENDING"


def reservation_reference_row(reference: str) -> sqlite3.Row | None:
    """Retrouve une reservation par id numerique ou numero de billet."""
    if reference.isdigit():
        return reservation_with_trip(int(reference))
    return get_db().execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id
           WHERE r.ticket_number = ?""",
        (reference,),
    ).fetchone()


def start_shwary_payment(reservation: sqlite3.Row, phone: str | None = None) -> sqlite3.Row:
    """Cree une demande de paiement Shwary et appelle l'API du fournisseur."""
    if not shwary_configured():
        raise RuntimeError("Configuration Shwary manquante.")
    payment_requests_ready()
    customer_phone = normalize_payment_phone(phone or reservation["customer_phone"])
    reference_id = f"{reservation['ticket_number']}-{secrets.token_hex(4)}"
    amount = math.ceil(float(reservation["amount"]))
    settings = get_settings()
    shwary_currency = "CDF" if settings["currency"].upper() in {"FC", "CDF"} else settings["currency"].upper()
    country_code = app.config["SHWARY_COUNTRY_CODE"]
    callback_url = app.config["SHWARY_CALLBACK_URL"] or url_for("shwary_webhook_receive", _external=True)
    payload = {
        "amount": amount,
        "currency": shwary_currency,
        "phoneNumber": customer_phone,
        "reference": reference_id,
        "referenceId": reference_id,
        "description": f"Billet {reservation['ticket_number']}",
        "callbackUrl": callback_url,
        "customer": {
            "name": reservation["customer_name"],
            "phone": customer_phone,
        },
    }
    request_row_id = None
    db = get_db()
    try:
        cursor = db.execute(
            """INSERT INTO payment_requests
               (reservation_id, provider, reference_id, status, amount, currency, phone, country_code, checkout_payload)
               VALUES (?, 'SHWARY', ?, 'PENDING', ?, ?, ?, ?, ?)""",
            (
                reservation["id"],
                reference_id,
                amount,
                shwary_currency,
                customer_phone,
                country_code,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        request_row_id = cursor.lastrowid
        url = f"{app.config['SHWARY_API_BASE'].rstrip('/')}/api/v1/merchants/payment/{country_code}"
        api_request = urlrequest.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=shwary_headers(),
            method="POST",
        )
        with urlrequest.urlopen(api_request, timeout=20) as response:
            response_payload = json.loads(response.read().decode("utf-8") or "{}")
        provider_id = str(
            response_payload.get("transactionId")
            or response_payload.get("transactionID")
            or response_payload.get("id")
            or response_payload.get("paymentId")
            or ""
        )
        db.execute(
            """UPDATE payment_requests
               SET provider_transaction_id = ?, checkout_payload = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (provider_id or None, json.dumps(response_payload, ensure_ascii=False), request_row_id),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        if request_row_id:
            db.execute(
                """UPDATE payment_requests
                   SET status = 'FAILED', failure_reason = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (str(exc)[:500], request_row_id),
            )
            db.commit()
        raise
    return db.execute("SELECT * FROM payment_requests WHERE id = ?", (request_row_id,)).fetchone()


def complete_network_payment(reservation_id: int, provider: str) -> None:
    """Marque un paiement reseau reussi dans l'historique comptable."""
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None or reservation["status"] != "EN_ATTENTE":
        return
    active_payment = db.execute(
        "SELECT 1 FROM payments WHERE reservation_id = ? AND voided_at IS NULL",
        (reservation_id,),
    ).fetchone()
    if active_payment:
        return
    db.execute(
        "INSERT INTO payments (reservation_id, method, amount, received_by) VALUES (?, ?, ?, NULL)",
        (reservation_id, provider, reservation["amount"]),
    )
    db.execute("UPDATE reservations SET status = 'PAYE' WHERE id = ?", (reservation_id,))
    audit("NETWORK_PAYMENT_RECORDED", f"{provider}; {reservation['ticket_number']}; {reservation['amount']}")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Authentifie un compte à partir de son identifiant et de son mot de passe."""
    if g.user is not None:
        return redirect(url_for("dashboard"))
    next_url = safe_next_url(request.values.get("next"))
    if request.method == "POST":
        locked_until = session.get("login_locked_until", 0)
        if locked_until > time.time():
            flash("Trop de tentatives. Réessayez dans quelques minutes.", "error")
            return render_template("login.html", next_url=next_url), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not user["is_active"] or not check_password_hash(user["password_hash"], password):
            failures = session.get("login_failures", 0) + 1
            session["login_failures"] = failures
            if failures >= 5:
                session["login_locked_until"] = time.time() + 300
                session["login_failures"] = 0
            flash("Identifiant ou mot de passe incorrect.", "error")
        else:
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Connexion réussie.", "success")
            return redirect(next_url or url_for("dashboard"))
    return render_template("login.html", next_url=next_url)


@app.post("/logout")
def logout():
    """Ferme la session de l'utilisateur courant."""
    session.clear()
    flash("Vous êtes déconnecté.", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    """Affiche les indicateurs opérationnels, les tendances et les derniers billets."""
    db = get_db()
    stats = db.execute(
        """SELECT
             COUNT(CASE WHEN date(created_at) = date('now', 'localtime') THEN 1 END) AS tickets_today,
             COUNT(CASE WHEN status = 'EN_ATTENTE' THEN 1 END) AS pending,
             COUNT(CASE WHEN status = 'ANNULE' THEN 1 END) AS cancelled,
             COALESCE((SELECT SUM(amount) FROM payments
                       WHERE voided_at IS NULL AND date(paid_at) = date('now', 'localtime')), 0) AS revenue_today,
             (SELECT COUNT(*) FROM trips WHERE date(departure_at) = date('now', 'localtime')) AS trips_today,
             COALESCE(ROUND(100.0 *
                 (SELECT COUNT(*) FROM reservations future_r
                  JOIN trips future_t ON future_t.id = future_r.trip_id
                  WHERE future_r.status != 'ANNULE' AND future_t.departure_at >= datetime('now', 'localtime')) /
                 NULLIF((SELECT SUM(seat_count) FROM trips WHERE departure_at >= datetime('now', 'localtime')), 0)), 0) AS fill_rate
           FROM reservations"""
    ).fetchone()
    reservations = db.execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id
           ORDER BY r.created_at DESC LIMIT 8"""
    ).fetchall()
    sales = db.execute(
        """WITH RECURSIVE days(day) AS (
             SELECT date('now', 'localtime', '-6 days') UNION ALL
             SELECT date(day, '+1 day') FROM days WHERE day < date('now', 'localtime')
           )
           SELECT days.day, COUNT(r.id) AS tickets,
                  COALESCE(SUM(CASE WHEN p.voided_at IS NULL THEN p.amount ELSE 0 END), 0) AS revenue
           FROM days LEFT JOIN reservations r ON date(r.created_at) = days.day
           LEFT JOIN payments p ON p.reservation_id = r.id AND p.voided_at IS NULL
           GROUP BY days.day ORDER BY days.day"""
    ).fetchall()
    popular_trips = db.execute(
        """SELECT t.origin, t.destination, t.departure_at, t.seat_count,
                  COUNT(r.id) AS sold, t.seat_count - COUNT(r.id) AS remaining,
                  COALESCE(SUM(CASE WHEN r.status IN ('PAYE','CONFIRME','UTILISE') THEN r.amount ELSE 0 END), 0) AS revenue
           FROM trips t LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
           GROUP BY t.id ORDER BY sold DESC, t.departure_at LIMIT 5"""
    ).fetchall()
    return render_template(
        "dashboard.html", stats=stats, reservations=reservations,
        sales=sales, popular_trips=popular_trips,
        max_daily_tickets=max((row["tickets"] for row in sales), default=1) or 1,
    )


@app.route("/trips", methods=["GET", "POST"])
@login_required
def trips():
    """Liste les trajets et traite le formulaire de création d'un trajet."""
    db = get_db()
    # La même URL sert à afficher le formulaire (GET) et à enregistrer son contenu (POST).
    if request.method == "POST":
        origin = request.form.get("origin", "").strip()
        destination = request.form.get("destination", "").strip()
        departure_at = request.form.get("departure_at", "").strip()
        seats = request.form.get("seat_count", type=int)
        route_key = fare_key(origin, destination)
        price = get_fares().get(route_key) if route_key else None
        if route_key is None or not departure_at or not seats or seats < 1:
            flash("Veuillez renseigner un trajet valide.", "error")
        elif price is None:
            flash("Aucun tarif n'est configuré pour cette liaison. Contactez l'administrateur.", "error")
        else:
            db.execute(
                "INSERT INTO trips (origin, destination, departure_at, seat_count, price) VALUES (?, ?, ?, ?, ?)",
                (origin, destination, departure_at, seats, price),
            )
            audit("TRIP_CREATED", f"{origin} - {destination}; {departure_at}")
            db.commit()
            flash("Trajet créé.", "success")
            return redirect(url_for("trips"))
    # Compte les sièges non annulés afin de calculer les places disponibles.
    rows = db.execute(
        """SELECT t.*, t.seat_count - COUNT(r.id) AS available_seats
           FROM trips t LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
           GROUP BY t.id ORDER BY t.departure_at"""
    ).fetchall()
    return render_template("trips.html", trips=rows, cities=CITIES)


@app.route("/trips/<int:trip_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_trip(trip_id: int):
    """Autorise une modification prudente de l'horaire et de la capacité d'un trajet."""
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        abort(404)
    if request.method == "POST":
        departure_at = request.form.get("departure_at", "").strip()
        seats = request.form.get("seat_count", type=int)
        highest_seat = db.execute(
            "SELECT COALESCE(MAX(seat_number), 0) FROM reservations WHERE trip_id = ? AND status != 'ANNULE'", (trip_id,)
        ).fetchone()[0]
        if not departure_at or not seats or seats < 1:
            flash("Horaire ou capacité invalide.", "error")
        elif seats < highest_seat:
            flash(f"La capacité ne peut pas être inférieure au siège déjà attribué n° {highest_seat}.", "error")
        else:
            db.execute("UPDATE trips SET departure_at = ?, seat_count = ? WHERE id = ?", (departure_at, seats, trip_id))
            audit("TRIP_UPDATED", f"Trajet {trip_id}; {departure_at}; {seats} places")
            db.commit()
            flash("Trajet mis à jour.", "success")
            return redirect(url_for("trips"))
    return render_template("trip_edit.html", trip=trip)


@app.route("/reservations")
@login_required
def reservations():
    """Recherche et filtre les réservations sans concaténer les entrées dans SQL."""
    db = get_db()
    query = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    trip_id = request.args.get("trip_id", type=int)
    clauses, parameters = [], []
    if query:
        clauses.append("(r.ticket_number LIKE ? OR r.customer_name LIKE ? OR r.customer_phone LIKE ?)")
        pattern = f"%{query}%"
        parameters.extend((pattern, pattern, pattern))
    if status in {"EN_ATTENTE", "PAYE", "CONFIRME", "ANNULE", "UTILISE"}:
        clauses.append("r.status = ?")
        parameters.append(status)
    if trip_id:
        clauses.append("r.trip_id = ?")
        parameters.append(trip_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        f"""SELECT r.*, t.origin, t.destination, t.departure_at
            FROM reservations r JOIN trips t ON t.id = r.trip_id
            {where} ORDER BY r.created_at DESC LIMIT 500""", parameters
    ).fetchall()
    trips_list = db.execute("SELECT id, origin, destination, departure_at FROM trips ORDER BY departure_at DESC").fetchall()
    return render_template(
        "reservations.html", reservations=rows, trips=trips_list,
        filters={"q": query, "status": status, "trip_id": trip_id},
    )


@app.route("/trips/<int:trip_id>/manifest")
@login_required
def trip_manifest(trip_id: int):
    """Affiche ou exporte la liste d'embarquement d'un trajet."""
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        abort(404)
    passengers = db.execute(
        """SELECT ticket_number, customer_name, customer_phone, seat_number, status
           FROM reservations WHERE trip_id = ? AND status != 'ANNULE' ORDER BY seat_number""", (trip_id,)
    ).fetchall()
    if request.args.get("export") == "csv":
        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Siège", "Billet", "Passager", "Téléphone", "Statut"])
        for passenger in passengers:
            writer.writerow([passenger["seat_number"], passenger["ticket_number"], passenger["customer_name"], passenger["customer_phone"], passenger["status"]])
        return Response(
            "\ufeff" + output.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=manifeste-trajet-{trip_id}.csv"},
        )
    return render_template("manifest.html", trip=trip, passengers=passengers)


@app.route("/reports")
@admin_required
def reports():
    """Produit un rapport filtrable et exportable sans dupliquer les montants encaissés."""
    db = get_db()
    filters = {
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "trip_id": request.args.get("trip_id", type=int),
        "destination": request.args.get("destination", "").strip(),
        "user_id": request.args.get("user_id", type=int),
        "method": request.args.get("method", "").strip(),
        "status": request.args.get("status", "").strip(),
    }
    clauses, parameters = [], []
    if filters["date_from"]:
        clauses.append("date(r.created_at) >= date(?)")
        parameters.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("date(r.created_at) <= date(?)")
        parameters.append(filters["date_to"])
    if filters["trip_id"]:
        clauses.append("r.trip_id = ?")
        parameters.append(filters["trip_id"])
    if filters["destination"] in CITIES:
        clauses.append("t.destination = ?")
        parameters.append(filters["destination"])
    if filters["user_id"]:
        clauses.append("r.created_by = ?")
        parameters.append(filters["user_id"])
    if filters["method"]:
        clauses.append("p.method = ?")
        parameters.append(filters["method"])
    if filters["status"] in {"EN_ATTENTE", "PAYE", "CONFIRME", "ANNULE", "UTILISE"}:
        clauses.append("r.status = ?")
        parameters.append(filters["status"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        f"""SELECT r.id, r.ticket_number, r.customer_name, r.status, r.amount, r.created_at,
                   t.origin, t.destination, t.departure_at, u.username AS agent,
                   p.method, COALESCE(p.paid_amount, 0) AS paid_amount
            FROM reservations r JOIN trips t ON t.id = r.trip_id
            LEFT JOIN users u ON u.id = r.created_by
            LEFT JOIN (
                SELECT reservation_id, MAX(method) AS method, SUM(amount) AS paid_amount
                FROM payments WHERE voided_at IS NULL GROUP BY reservation_id
            ) p ON p.reservation_id = r.id
            {where} ORDER BY r.created_at DESC""",
        parameters,
    ).fetchall()
    summary = {
        "total": len(rows),
        "paid": sum(1 for row in rows if row["status"] in {"PAYE", "CONFIRME", "UTILISE"}),
        "pending": sum(1 for row in rows if row["status"] == "EN_ATTENTE"),
        "cancelled": sum(1 for row in rows if row["status"] == "ANNULE"),
        "revenue": sum(row["paid_amount"] for row in rows),
    }
    if request.args.get("export") == "csv":
        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Billet", "Passager", "Trajet", "Départ", "Statut", "Paiement", "Encaissé", "Agent", "Créé le"])
        for row in rows:
            writer.writerow([
                row["ticket_number"], row["customer_name"], f'{row["origin"]} - {row["destination"]}',
                row["departure_at"], row["status"], row["method"] or "", row["paid_amount"],
                row["agent"] or "", row["created_at"],
            ])
        return Response(
            "\ufeff" + output.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=rapport-billetterie.csv"},
        )
    trips_list = db.execute("SELECT id, origin, destination, departure_at FROM trips ORDER BY departure_at DESC").fetchall()
    users = db.execute("SELECT id, username FROM users ORDER BY username").fetchall()
    return render_template(
        "reports.html", rows=rows, summary=summary, filters=filters,
        trips=trips_list, users=users, cities=CITIES,
    )


@app.route("/reservations/new", methods=["GET", "POST"])
@login_required
def new_reservation():
    """Affiche et traite le formulaire de création d'une réservation."""
    db = get_db()
    if request.method == "POST":
        trip_id = request.form.get("trip_id", type=int)
        customer_name = request.form.get("customer_name", "").strip()
        customer_phone = request.form.get("customer_phone", "").strip()
        if not trip_id or not customer_name or not customer_phone:
            flash("Tous les champs sont obligatoires.", "error")
        else:
            trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
            if trip is None:
                flash("Le trajet sélectionné n'existe pas.", "error")
            else:
                try:
                    # Un verrou court garantit une attribution fiable lors de ventes simultanées.
                    db.execute("BEGIN IMMEDIATE")
                    occupied = {
                        row["seat_number"] for row in db.execute(
                            "SELECT seat_number FROM reservations WHERE trip_id = ? AND status != 'ANNULE'",
                            (trip_id,),
                        )
                    }
                    seat_number = next((number for number in range(1, trip["seat_count"] + 1) if number not in occupied), None)
                    if seat_number is None:
                        db.rollback()
                        flash("Ce trajet est complet.", "error")
                        return redirect(url_for("new_reservation"))
                    # Le prix est copié depuis le trajet pour figer le montant du billet.
                    cursor = db.execute(
                        """INSERT INTO reservations
                           (trip_id, customer_name, customer_phone, seat_number, amount, created_by, verification_token)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (trip_id, customer_name, customer_phone, seat_number, trip["price"], g.user["id"], secrets.token_urlsafe(24)),
                    )
                    # L'identifiant généré sert ensuite à produire le numéro de billet.
                    reservation_id = cursor.lastrowid
                    prefix = get_settings()["ticket_prefix"]
                    db.execute("UPDATE reservations SET ticket_number = ? WHERE id = ?", (ticket_number(reservation_id, prefix), reservation_id))
                    audit("RESERVATION_CREATED", f"Réservation {reservation_id}; siège {seat_number}")
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("Ce siège vient d'être réservé par un autre client.", "error")
                else:
                    flash("Réservation créée. En attente de paiement.", "success")
                    return redirect(url_for("reservation_detail", reservation_id=reservation_id))
    # Seuls les départs encore à venir sont proposés dans le formulaire.
    trips_list = db.execute("SELECT * FROM trips WHERE departure_at >= datetime('now') ORDER BY departure_at").fetchall()
    return render_template("reservation_form.html", trips=trips_list)


@app.route("/reservations/<int:reservation_id>")
@login_required
def reservation_detail(reservation_id: int):
    """Affiche une réservation ou une erreur 404 si elle est inconnue."""
    row = get_db().execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at, t.price
           FROM reservations r JOIN trips t ON t.id = r.trip_id WHERE r.id = ?""",
        (reservation_id,),
    ).fetchone()
    if row is None:
        abort(404)
    payments = get_db().execute(
        """SELECT p.*, receiver.username AS receiver_name, voider.username AS voider_name
           FROM payments p
           LEFT JOIN users receiver ON receiver.id = p.received_by
           LEFT JOIN users voider ON voider.id = p.voided_by
           WHERE p.reservation_id = ? ORDER BY p.paid_at DESC""",
        (reservation_id,),
    ).fetchall()
    payment_requests_ready()
    network_payments = get_db().execute(
        """SELECT * FROM payment_requests
           WHERE reservation_id = ? ORDER BY created_at DESC""",
        (reservation_id,),
    ).fetchall()
    qr_value = url_for("verify_ticket", token=row["verification_token"], _external=True)
    return render_template(
        "reservation_detail.html",
        reservation=row,
        payments=payments,
        network_payments=network_payments,
        shwary_ready=shwary_configured(),
        qr_code=qr_code_data_uri(qr_value),
    )


@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    """Affiche et enregistre les préférences générales, de billet et d'impression."""
    db = get_db()
    if request.method == "POST":
        values = {
            "agency_name": request.form.get("agency_name", "").strip(),
            "agency_address": request.form.get("agency_address", "").strip(),
            "agency_phone": request.form.get("agency_phone", "").strip(),
            "currency": request.form.get("currency", "").strip(),
            "ticket_prefix": request.form.get("ticket_prefix", "").strip().upper(),
            "ticket_footer": request.form.get("ticket_footer", "").strip(),
            "paper_size": request.form.get("paper_size", ""),
            "theme": request.form.get("theme", ""),
        }
        fares = {}
        fares_valid = True
        for origin, destination in FARE_ROUTES:
            price = request.form.get(f"fare_{origin}_{destination}", type=float)
            if price is None or not math.isfinite(price) or price < 0:
                fares_valid = False
                break
            fares[(origin, destination)] = price
        if not values["agency_name"] or not values["currency"] or not values["ticket_prefix"].isalnum():
            flash("Renseignez un nom d'agence, une devise et un préfixe alphanumérique valides.", "error")
        elif values["paper_size"] not in {"A4", "A5", "80mm"} or values["theme"] not in {"navy", "emerald", "purple", "terracotta"}:
            abort(400)
        elif not fares_valid:
            flash("Renseignez un prix valide pour chaque liaison.", "error")
        else:
            db.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                values.items(),
            )
            db.executemany(
                """INSERT INTO fares (origin, destination, price) VALUES (?, ?, ?)
                   ON CONFLICT(origin, destination) DO UPDATE SET price = excluded.price""",
                ((origin, destination, price) for (origin, destination), price in fares.items()),
            )
            audit("SETTINGS_UPDATED", "Préférences et tarifs")
            db.commit()
            flash("Paramètres enregistrés.", "success")
            return redirect(url_for("settings"))
    users = db.execute("SELECT id, username, is_admin, is_active, created_at FROM users ORDER BY username").fetchall()
    return render_template(
        "settings.html", settings=get_settings(), users=users,
        fare_routes=FARE_ROUTES, fares=get_fares(),
    )


@app.route("/settings/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    """Permet à un utilisateur connecté de créer un autre compte d'accès."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")
        is_admin = 1 if request.form.get("is_admin") == "1" else 0
        if not valid_username(username):
            flash("L'identifiant doit compter de 3 à 80 caractères valides.", "error")
        elif len(password) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
        elif password != password_confirmation:
            flash("Les mots de passe ne correspondent pas.", "error")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), is_admin),
                )
                audit("USER_CREATED", username)
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Cet identifiant est déjà utilisé.", "error")
            else:
                flash("Utilisateur ajouté.", "success")
                return redirect(url_for("settings"))
    return render_template("user_form.html")


@app.route("/settings/users/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_user(user_id: int):
    """Modifie, désactive ou réinitialise le mot de passe d'un compte."""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        is_admin = 1 if request.form.get("is_admin") == "1" else 0
        is_active = 1 if request.form.get("is_active") == "1" else 0
        new_password = request.form.get("new_password", "")
        admin_count = db.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()[0]
        removing_last_admin = user["is_admin"] and user["is_active"] and admin_count == 1 and (not is_admin or not is_active)
        if not valid_username(username):
            flash("Identifiant invalide.", "error")
        elif removing_last_admin:
            flash("Le dernier administrateur actif ne peut pas être désactivé ou rétrogradé.", "error")
        elif user_id == g.user["id"] and not is_active:
            flash("Vous ne pouvez pas désactiver votre propre compte.", "error")
        elif new_password and len(new_password) < 8:
            flash("Le nouveau mot de passe doit contenir au moins 8 caractères.", "error")
        else:
            try:
                if new_password:
                    db.execute(
                        "UPDATE users SET username = ?, is_admin = ?, is_active = ?, password_hash = ? WHERE id = ?",
                        (username, is_admin, is_active, generate_password_hash(new_password), user_id),
                    )
                else:
                    db.execute(
                        "UPDATE users SET username = ?, is_admin = ?, is_active = ? WHERE id = ?",
                        (username, is_admin, is_active, user_id),
                    )
                audit("USER_UPDATED", f"{username}; actif={is_active}; admin={is_admin}")
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Cet identifiant est déjà utilisé.", "error")
            else:
                flash("Compte utilisateur mis à jour.", "success")
                return redirect(url_for("settings"))
    return render_template("user_edit.html", user=user)


@app.route("/settings/audit")
@admin_required
def audit_log():
    """Affiche les dernières opérations sensibles du logiciel."""
    rows = get_db().execute(
        """SELECT logs.*, users.username FROM audit_logs logs
           LEFT JOIN users ON users.id = logs.user_id ORDER BY logs.created_at DESC LIMIT 300"""
    ).fetchall()
    return render_template("audit_log.html", logs=rows)


@app.route("/settings/backup")
@admin_required
def download_backup():
    """Télécharge une copie cohérente de la base SQLite courante."""
    data = get_db().serialize()
    audit("DATABASE_BACKUP", "Sauvegarde téléchargée")
    get_db().commit()
    return send_file(
        BytesIO(data), as_attachment=True,
        download_name=f"billetterie-{datetime.now():%Y%m%d-%H%M}.db",
        mimetype="application/vnd.sqlite3",
    )


@app.route("/verify/<token>")
def verify_ticket(token: str):
    """Affiche une page de contrôle publique limitée pour un QR Code."""
    reservation = get_db().execute(
        """SELECT r.id, r.ticket_number, r.customer_name, r.seat_number, r.status,
                  t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id
           WHERE r.verification_token = ?""", (token,)
    ).fetchone()
    if reservation is None:
        abort(404)
    return render_template("verify_ticket.html", reservation=reservation, token=token)


@app.get("/tickets/<token>.pdf")
def ticket_pdf(token: str):
    """Telecharge le billet PDF depuis le token QR du billet."""
    reservation = get_db().execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id
           WHERE r.verification_token = ?""",
        (token,),
    ).fetchone()
    if reservation is None:
        abort(404)
    return send_file(
        BytesIO(build_ticket_pdf(reservation)),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{reservation['ticket_number']}.pdf",
    )


@app.post("/verify/<token>/use")
@login_required
def use_verified_ticket(token: str):
    """Valide l'embarquement une seule fois depuis la page du QR Code."""
    db = get_db()
    reservation = db.execute("SELECT id, ticket_number, status FROM reservations WHERE verification_token = ?", (token,)).fetchone()
    if reservation is None:
        abort(404)
    if reservation["status"] != "CONFIRME":
        flash("Seul un billet confirmé peut être validé pour l'embarquement.", "error")
    else:
        db.execute("UPDATE reservations SET status = 'UTILISE' WHERE id = ?", (reservation["id"],))
        audit("TICKET_USED", reservation["ticket_number"])
        db.commit()
        flash("Billet validé pour l'embarquement.", "success")
    return redirect(url_for("verify_ticket", token=token))


@app.route("/scanner")
@login_required
def scanner():
    """Affiche le scanner mobile des QR Codes de billets."""
    return render_template("scanner.html")


@app.get("/webhooks/whatsapp")
def whatsapp_webhook_verify():
    """Vérifie le webhook demandé par Meta WhatsApp Cloud API."""
    mode = request.args.get("hub.mode", "")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    expected = app.config["WHATSAPP_VERIFY_TOKEN"]
    if mode == "subscribe" and expected and secrets.compare_digest(token, expected):
        return Response(challenge, mimetype="text/plain")
    return jsonify({"error": "Verification WhatsApp refusee."}), 403


@app.post("/webhooks/whatsapp")
def whatsapp_webhook_receive():
    """Reçoit les messages WhatsApp, répond au client et mémorise la conversation."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"received": False, "error": "JSON invalide."}), 400
    responses = []
    for phone, incoming_text in extract_whatsapp_text_messages(payload):
        whatsapp_log(phone, "IN", incoming_text)
        reply = whatsapp_bot_reply(phone, incoming_text)
        sent = send_whatsapp_text(phone, reply)
        whatsapp_log(phone, "OUT", reply)
        responses.append({"phone": phone, "reply": reply, "sent": sent})
    return jsonify({"received": True, "responses": responses})


@app.post("/webhooks/shwary")
def shwary_webhook_receive():
    """Reçoit les callbacks Shwary et marque le paiement si la transaction est réussie."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"received": False, "error": "JSON invalide."}), 400
    reference = str(payload.get("reference") or payload.get("referenceId") or payload.get("merchantReference") or "").strip()
    provider_id = str(payload.get("transactionId") or payload.get("transactionID") or payload.get("id") or "").strip()
    status = shwary_status_from_payload(payload)
    db = get_db()
    payment_requests_ready()
    if reference:
        request_row = db.execute("SELECT * FROM payment_requests WHERE reference_id = ?", (reference,)).fetchone()
    elif provider_id:
        request_row = db.execute("SELECT * FROM payment_requests WHERE provider_transaction_id = ?", (provider_id,)).fetchone()
    else:
        request_row = None
    if request_row is None:
        return jsonify({"received": True, "matched": False})
    db.execute(
        """UPDATE payment_requests
           SET status = ?, provider_transaction_id = COALESCE(?, provider_transaction_id),
               checkout_payload = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            status,
            provider_id or None,
            json.dumps(payload, ensure_ascii=False),
            None if status == "COMPLETED" else str(payload.get("message") or payload.get("reason") or "")[:500],
            request_row["id"],
        ),
    )
    if status == "COMPLETED":
        complete_network_payment(request_row["reservation_id"], "SHWARY")
    db.commit()
    return jsonify({"received": True, "matched": True, "status": status})


@app.get("/api")
@api_login_required
def api_index():
    """Presente les principales routes JSON disponibles."""
    return jsonify({
        "name": "API Billetterie",
        "current_user": {"id": g.user["id"], "username": g.user["username"], "is_admin": bool(g.user["is_admin"])},
        "csrf_token": csrf_token(),
        "endpoints": {
            "health": "/api/health",
            "docs": "/api/docs",
            "csrf": "/api/csrf",
            "login": "/api/login",
            "logout": "/api/logout",
            "trips": "/api/trips",
            "reservations": "/api/reservations",
            "payments": "/api/payments",
            "settings": "/api/settings",
            "verify_ticket": "/api/verify/<token>",
            "use_verified_ticket": "/api/verify/<token>/use",
            "whatsapp_webhook": "/webhooks/whatsapp",
            "ai_capabilities": "/api/ai/capabilities",
            "ai_context": "/api/ai/context",
            "ai_search_trips": "/api/ai/trips/search",
            "ai_create_reservation": "/api/ai/reservations",
            "ai_ticket_pdf": "/api/ai/reservations/<reference>/ticket.pdf",
            "ai_shwary_payment": "/api/ai/reservations/<reference>/payments/shwary",
            "shwary_webhook": "/webhooks/shwary",
        },
    })


@app.get("/api/health")
def api_health():
    """Indique a un bot ou a un superviseur que l'API est disponible."""
    return jsonify({
        "status": "ok",
        "service": "billetterie",
        "time": datetime.now().isoformat(timespec="seconds"),
    })


@app.get("/api/ai/capabilities")
@ai_api_required
def api_ai_capabilities():
    """Décrit ce qu'une IA externe peut faire avec la billetterie."""
    return jsonify({
        "name": "API IA Billetterie",
        "version": "1.0",
        "language": "fr",
        "auth": "Authorization: Bearer <AI_API_TOKEN>",
        "conversation_style": {
            "tone": "naturel, poli, simple et rassurant",
            "rules": [
                "Répondre comme un agent humain de billetterie.",
                "Utiliser des phrases courtes.",
                "Ne pas parler comme un robot.",
                "Guider le client étape par étape.",
                "Confirmer clairement les informations importantes.",
                "Ne pas inventer de trajet, prix, siège ou statut.",
            ],
        },
        "capabilities": {
            "read_business_context": True,
            "search_available_trips": True,
            "create_reservation": True,
            "automatic_seat_assignment": True,
            "read_reservation": True,
            "record_payment": False,
            "confirm_payment": False,
            "start_shwary_payment": True,
            "generate_ticket_pdf": True,
            "admin_actions": False,
        },
        "endpoints": {
            "context": "/api/ai/context",
            "search_trips": "/api/ai/trips/search",
            "create_reservation": "/api/ai/reservations",
            "read_reservation": "/api/ai/reservations/<reference>",
            "ticket_pdf": "/api/ai/reservations/<reference>/ticket.pdf",
            "shwary_payment": "/api/ai/reservations/<reference>/payments/shwary",
        },
    })


@app.get("/api/ai/context")
@ai_api_required
def api_ai_context():
    """Fournit à l'IA le contexte métier non sensible de la billetterie."""
    settings = get_settings()
    fares = [
        {"origin": origin, "destination": destination, "price": price}
        for (origin, destination), price in sorted(get_fares().items())
    ]
    next_trips = get_db().execute(
        """SELECT t.*, t.seat_count - COUNT(r.id) AS available_seats
           FROM trips t
           LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
           WHERE datetime(t.departure_at) >= datetime('now', '-1 day')
           GROUP BY t.id
           HAVING available_seats > 0
           ORDER BY t.departure_at
           LIMIT 10"""
    ).fetchall()
    return jsonify({
        "agency": {
            "name": settings["agency_name"],
            "address": settings["agency_address"],
            "phone": settings["agency_phone"],
            "currency": settings["currency"],
        },
        "business_rules": [
            "Les villes autorisées sont fixes.",
            "Le siège est attribué automatiquement par le système.",
            "Le client ne choisit pas le siège.",
            "Une réservation créée par l'IA reste en attente jusqu'au paiement et à la confirmation.",
            "Les paiements et confirmations restent réservés aux agents autorisés dans le logiciel.",
        ],
        "assistant_style": {
            "role": "Agent de réservation WhatsApp pour une billetterie de transport.",
            "tone": "naturel, chaleureux, clair et professionnel.",
            "examples": [
                "Bonjour 👋 Je peux vous aider à réserver un billet.",
                "D'accord, vous partez de Likasi. Quelle est votre destination ?",
                "Parfait, voici les trajets disponibles.",
                "C'est bon, votre réservation est créée ✅",
            ],
        },
        "cities": list(CITIES),
        "fares": fares,
        "next_available_trips": [serialize_trip(row) for row in next_trips],
    })


@app.post("/api/ai/trips/search")
@ai_api_required
def api_ai_search_trips():
    """Recherche les trajets disponibles pour une IA conversationnelle."""
    payload = json_payload()
    origin = city_from_text(str(payload.get("origin", "")))
    destination = city_from_text(str(payload.get("destination", "")))
    travel_date = str(payload.get("date", "")).strip()
    if origin is None or destination is None or origin == destination:
        return api_error("origin et destination doivent être deux villes différentes.")
    clauses = [
        "t.origin = ?",
        "t.destination = ?",
        "datetime(t.departure_at) >= datetime('now', '-1 day')",
    ]
    parameters: list[object] = [origin, destination]
    if travel_date:
        clauses.append("date(t.departure_at) = date(?)")
        parameters.append(travel_date)
    rows = get_db().execute(
        f"""SELECT t.*, t.seat_count - COUNT(r.id) AS available_seats
            FROM trips t
            LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
            WHERE {' AND '.join(clauses)}
            GROUP BY t.id
            HAVING available_seats > 0
            ORDER BY t.departure_at
            LIMIT 20""",
        parameters,
    ).fetchall()
    return jsonify({
        "origin": origin,
        "destination": destination,
        "date": travel_date or None,
        "trips": [serialize_trip(row) for row in rows],
    })


@app.post("/api/ai/reservations")
@ai_api_required
def api_ai_create_reservation():
    """Crée une réservation demandée par une IA, sans choix manuel du siège."""
    payload = json_payload()
    trip_id = payload.get("trip_id")
    customer_name = " ".join(str(payload.get("customer_name", "")).split())
    customer_phone = " ".join(str(payload.get("customer_phone", "")).split())
    if not isinstance(trip_id, int) or not customer_name or not customer_phone:
        return api_error("trip_id, customer_name et customer_phone sont obligatoires.")
    try:
        reservation = create_bot_reservation(trip_id, customer_name, customer_phone, source="AI")
    except ValueError as exc:
        return api_error(str(exc), 409)
    except sqlite3.IntegrityError:
        return api_error("Ce siège vient d'être réservé par un autre client.", 409)
    return jsonify({
        "message": "Réservation créée par l'IA.",
        "reservation": serialize_reservation(reservation),
        "next_step": "Faire payer le client puis confirmer le billet dans le logiciel.",
    }), 201


@app.get("/api/ai/reservations/<reference>")
@ai_api_required
def api_ai_reservation_detail(reference: str):
    """Lit une réservation par identifiant numérique ou numéro de billet."""
    db = get_db()
    if reference.isdigit():
        row = reservation_with_trip(int(reference))
    else:
        row = db.execute(
            """SELECT r.*, t.origin, t.destination, t.departure_at
               FROM reservations r JOIN trips t ON t.id = r.trip_id
               WHERE r.ticket_number = ?""",
            (reference,),
        ).fetchone()
    if row is None:
        return api_error("Réservation introuvable.", 404)
    return jsonify({"reservation": serialize_reservation(row)})


@app.get("/api/ai/reservations/<reference>/ticket.pdf")
@ai_api_required
def api_ai_ticket_pdf(reference: str):
    """Telecharge le billet au format PDF pour un bot ou une IA."""
    row = reservation_reference_row(reference)
    if row is None:
        return api_error("Reservation introuvable.", 404)
    return send_file(
        BytesIO(build_ticket_pdf(row)),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{row['ticket_number']}.pdf",
    )


@app.post("/api/ai/reservations/<reference>/payments/shwary")
@ai_api_required
def api_ai_start_shwary_payment(reference: str):
    """Demande un paiement reseau via Shwary pour une reservation."""
    row = reservation_reference_row(reference)
    if row is None:
        return api_error("Reservation introuvable.", 404)
    if row["status"] != "EN_ATTENTE":
        return api_error("Cette reservation ne peut plus recevoir une demande de paiement.", 409)
    payload = json_payload()
    phone = str(payload.get("phone") or row["customer_phone"]).strip()
    try:
        payment_request = start_shwary_payment(row, phone)
    except RuntimeError as exc:
        return api_error(str(exc), 503)
    except Exception as exc:
        return api_error(f"Demande Shwary impossible : {exc}", 502)
    return jsonify({
        "message": "Demande de paiement Shwary envoyee.",
        "payment_request": dict(payment_request),
        "instruction": "Le client doit valider le paiement sur son telephone. Le statut sera mis a jour par le callback Shwary.",
    }), 201


@app.get("/api/ai/payments/shwary/<reference_id>")
@ai_api_required
def api_ai_shwary_payment_status(reference_id: str):
    """Lit le statut local d'une demande de paiement Shwary."""
    payment_requests_ready()
    row = get_db().execute("SELECT * FROM payment_requests WHERE reference_id = ?", (reference_id,)).fetchone()
    if row is None:
        return api_error("Demande de paiement introuvable.", 404)
    reservation = reservation_with_trip(row["reservation_id"])
    return jsonify({
        "payment_request": dict(row),
        "reservation": serialize_reservation(reservation) if reservation else None,
    })


@app.get("/api/docs")
def api_docs():
    """Expose une documentation JSON courte pour les clients externes."""
    return jsonify({
        "name": "API Billetterie",
        "version": "1.0",
        "auth": {
            "type": "session_cookie",
            "csrf_header": "X-CSRF-Token",
            "flow": [
                "GET /api/csrf",
                "POST /api/login avec username, password et X-CSRF-Token",
                "Conserver le cookie de session et utiliser le nouveau csrf_token pour les POST suivants",
            ],
        },
        "bot_ready": {
            "can_list_trips": True,
            "can_create_reservation": True,
            "automatic_seat_assignment": True,
            "can_record_payment": True,
            "can_confirm_ticket": True,
            "can_verify_qr_ticket": True,
            "can_mark_boarding_used": True,
            "has_whatsapp_webhook": True,
            "has_ai_api": True,
        },
        "endpoints": [
            {"method": "GET", "path": "/api/health", "auth": False, "purpose": "Verifier que l'API repond."},
            {"method": "GET", "path": "/api/csrf", "auth": False, "purpose": "Obtenir un jeton CSRF."},
            {"method": "POST", "path": "/api/login", "auth": False, "purpose": "Ouvrir une session API."},
            {"method": "POST", "path": "/api/logout", "auth": True, "purpose": "Fermer la session."},
            {"method": "GET", "path": "/api/trips", "auth": True, "purpose": "Lister les trajets et places disponibles."},
            {"method": "GET", "path": "/api/trips/<id>", "auth": True, "purpose": "Lire un trajet et ses reservations."},
            {"method": "GET", "path": "/api/reservations", "auth": True, "purpose": "Rechercher les reservations."},
            {"method": "POST", "path": "/api/reservations", "auth": True, "purpose": "Creer une reservation avec siege automatique."},
            {"method": "GET", "path": "/api/reservations/<id>", "auth": True, "purpose": "Lire une reservation et ses paiements."},
            {"method": "POST", "path": "/api/reservations/<id>/payment", "auth": True, "purpose": "Enregistrer un paiement."},
            {"method": "POST", "path": "/api/reservations/<id>/status", "auth": True, "purpose": "Confirmer, utiliser ou annuler un billet selon les droits."},
            {"method": "GET", "path": "/api/verify/<token>", "auth": True, "purpose": "Verifier un billet depuis le QR Code."},
            {"method": "POST", "path": "/api/verify/<token>/use", "auth": True, "purpose": "Marquer un billet confirme comme utilise a l'embarquement."},
            {"method": "GET", "path": "/api/payments", "auth": True, "purpose": "Lister les paiements recents."},
            {"method": "GET", "path": "/api/settings", "auth": "admin", "purpose": "Lire les parametres, villes et tarifs."},
            {"method": "GET", "path": "/webhooks/whatsapp", "auth": "verify_token", "purpose": "Verifier le webhook WhatsApp Cloud API."},
            {"method": "POST", "path": "/webhooks/whatsapp", "auth": "meta_webhook", "purpose": "Recevoir les messages WhatsApp et guider une reservation."},
            {"method": "GET", "path": "/api/ai/capabilities", "auth": "AI_API_TOKEN", "purpose": "Lire les capacités disponibles pour une IA."},
            {"method": "GET", "path": "/api/ai/context", "auth": "AI_API_TOKEN", "purpose": "Lire le contexte métier non sensible."},
            {"method": "POST", "path": "/api/ai/trips/search", "auth": "AI_API_TOKEN", "purpose": "Rechercher les trajets disponibles."},
            {"method": "POST", "path": "/api/ai/reservations", "auth": "AI_API_TOKEN", "purpose": "Creer une reservation par IA avec siege automatique."},
            {"method": "GET", "path": "/api/ai/reservations/<reference>", "auth": "AI_API_TOKEN", "purpose": "Lire une reservation par id ou numero de billet."},
            {"method": "GET", "path": "/api/ai/reservations/<reference>/ticket.pdf", "auth": "AI_API_TOKEN", "purpose": "Telecharger le billet au format PDF."},
            {"method": "POST", "path": "/api/ai/reservations/<reference>/payments/shwary", "auth": "AI_API_TOKEN", "purpose": "Lancer une demande de paiement Shwary."},
            {"method": "GET", "path": "/api/ai/payments/shwary/<reference_id>", "auth": "AI_API_TOKEN", "purpose": "Lire le statut local d'une demande Shwary."},
            {"method": "POST", "path": "/webhooks/shwary", "auth": "callback", "purpose": "Recevoir le statut de paiement envoye par Shwary."},
        ],
    })


@app.get("/api/csrf")
def api_csrf():
    """Fournit un jeton CSRF pour les appels POST de l'API."""
    return jsonify({"csrf_token": csrf_token()})


@app.post("/api/login")
def api_login():
    """Connecte un utilisateur depuis un client JSON."""
    payload = json_payload()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None or not user["is_active"] or not check_password_hash(user["password_hash"], password):
        return api_error("Identifiant ou mot de passe incorrect.", 401)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify({
        "message": "Connexion reussie.",
        "csrf_token": session["csrf_token"],
        "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])},
    })


@app.post("/api/logout")
@api_login_required
def api_logout():
    """Ferme la session courante depuis l'API."""
    session.clear()
    return jsonify({"message": "Deconnexion reussie."})


@app.get("/api/trips")
@api_login_required
def api_trips():
    """Liste les trajets avec les places disponibles."""
    rows = get_db().execute(
        """SELECT t.*, t.seat_count - COUNT(r.id) AS available_seats
           FROM trips t LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
           GROUP BY t.id ORDER BY t.departure_at"""
    ).fetchall()
    return jsonify({"trips": [serialize_trip(row) for row in rows]})


@app.get("/api/trips/<int:trip_id>")
@api_login_required
def api_trip_detail(trip_id: int):
    """Affiche un trajet et ses reservations actives."""
    db = get_db()
    trip = db.execute(
        """SELECT t.*, t.seat_count - COUNT(r.id) AS available_seats
           FROM trips t LEFT JOIN reservations r ON r.trip_id = t.id AND r.status != 'ANNULE'
           WHERE t.id = ? GROUP BY t.id""",
        (trip_id,),
    ).fetchone()
    if trip is None:
        abort(404)
    passengers = db.execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id
           WHERE r.trip_id = ? ORDER BY r.seat_number""",
        (trip_id,),
    ).fetchall()
    return jsonify({
        "trip": serialize_trip(trip),
        "reservations": [serialize_reservation(row) for row in passengers],
    })


@app.get("/api/reservations")
@api_login_required
def api_reservations():
    """Recherche les reservations en JSON."""
    query = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    trip_id = request.args.get("trip_id", type=int)
    clauses, parameters = [], []
    if query:
        clauses.append("(r.ticket_number LIKE ? OR r.customer_name LIKE ? OR r.customer_phone LIKE ?)")
        pattern = f"%{query}%"
        parameters.extend((pattern, pattern, pattern))
    if status in {"EN_ATTENTE", "PAYE", "CONFIRME", "ANNULE", "UTILISE"}:
        clauses.append("r.status = ?")
        parameters.append(status)
    if trip_id:
        clauses.append("r.trip_id = ?")
        parameters.append(trip_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = get_db().execute(
        f"""SELECT r.*, t.origin, t.destination, t.departure_at
            FROM reservations r JOIN trips t ON t.id = r.trip_id
            {where} ORDER BY r.created_at DESC LIMIT 500""",
        parameters,
    ).fetchall()
    return jsonify({"reservations": [serialize_reservation(row) for row in rows]})


@app.post("/api/reservations")
@api_login_required
def api_create_reservation():
    """Cree une reservation JSON avec attribution automatique du siege."""
    payload = json_payload()
    trip_id = payload.get("trip_id")
    customer_name = str(payload.get("customer_name", "")).strip()
    customer_phone = str(payload.get("customer_phone", "")).strip()
    if not isinstance(trip_id, int) or not customer_name or not customer_phone:
        return api_error("trip_id, customer_name et customer_phone sont obligatoires.")
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        abort(404)
    try:
        db.execute("BEGIN IMMEDIATE")
        occupied = {
            row["seat_number"] for row in db.execute(
                "SELECT seat_number FROM reservations WHERE trip_id = ? AND status != 'ANNULE'",
                (trip_id,),
            )
        }
        seat_number = next((number for number in range(1, trip["seat_count"] + 1) if number not in occupied), None)
        if seat_number is None:
            db.rollback()
            return api_error("Ce trajet est complet.", 409)
        cursor = db.execute(
            """INSERT INTO reservations
               (trip_id, customer_name, customer_phone, seat_number, amount, created_by, verification_token)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (trip_id, customer_name, customer_phone, seat_number, trip["price"], g.user["id"], secrets.token_urlsafe(24)),
        )
        reservation_id = cursor.lastrowid
        prefix = get_settings()["ticket_prefix"]
        db.execute("UPDATE reservations SET ticket_number = ? WHERE id = ?", (ticket_number(reservation_id, prefix), reservation_id))
        audit("API_RESERVATION_CREATED", f"Reservation {reservation_id}; siege {seat_number}")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return api_error("Ce siege vient d'etre reserve par un autre client.", 409)
    reservation = reservation_with_trip(reservation_id)
    return jsonify({"message": "Reservation creee.", "reservation": serialize_reservation(reservation)}), 201


@app.get("/api/reservations/<int:reservation_id>")
@api_login_required
def api_reservation_detail(reservation_id: int):
    """Affiche une reservation et ses paiements."""
    row = reservation_with_trip(reservation_id)
    if row is None:
        abort(404)
    payments = get_db().execute(
        """SELECT p.*, receiver.username AS receiver_name, voider.username AS voider_name
           FROM payments p
           LEFT JOIN users receiver ON receiver.id = p.received_by
           LEFT JOIN users voider ON voider.id = p.voided_by
           WHERE p.reservation_id = ? ORDER BY p.paid_at DESC""",
        (reservation_id,),
    ).fetchall()
    return jsonify({
        "reservation": serialize_reservation(row),
        "payments": [dict(payment) for payment in payments],
    })


@app.get("/api/verify/<token>")
@api_login_required
def api_verify_ticket(token: str):
    """Verifie un billet associe a un QR Code et repond en JSON."""
    row = get_db().execute(
        """SELECT r.*, t.origin, t.destination, t.departure_at
           FROM reservations r JOIN trips t ON t.id = r.trip_id
           WHERE r.verification_token = ?""",
        (token,),
    ).fetchone()
    if row is None:
        return jsonify({"valid": False, "error": "Billet introuvable."}), 404
    return jsonify({
        "valid": row["status"] in {"PAYE", "CONFIRME", "UTILISE"},
        "can_board": row["status"] == "CONFIRME",
        "already_used": row["status"] == "UTILISE",
        "reservation": serialize_reservation(row),
    })


@app.post("/api/verify/<token>/use")
@api_login_required
def api_use_verified_ticket(token: str):
    """Valide l'embarquement d'un billet depuis son token QR."""
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE verification_token = ?", (token,)).fetchone()
    if reservation is None:
        return jsonify({"valid": False, "error": "Billet introuvable."}), 404
    if reservation["status"] != "CONFIRME":
        return api_error("Seul un billet confirme peut etre marque comme utilise.", 409)
    db.execute("UPDATE reservations SET status = 'UTILISE' WHERE id = ?", (reservation["id"],))
    audit("API_TICKET_USED", reservation["ticket_number"])
    db.commit()
    row = reservation_with_trip(reservation["id"])
    return jsonify({
        "message": "Billet valide pour l'embarquement.",
        "reservation": serialize_reservation(row),
    })


@app.post("/api/reservations/<int:reservation_id>/payment")
@api_login_required
def api_record_payment(reservation_id: int):
    """Enregistre un paiement depuis l'API."""
    payload = json_payload()
    method = str(payload.get("payment_method", "")).strip()
    if method not in {"ESPECES", "ORANGE_MONEY", "AIRTEL_MONEY", "MPESA", "AFRIMONEY", "AUTRE"}:
        return api_error("Methode de paiement invalide.")
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        abort(404)
    active_payment = db.execute(
        "SELECT 1 FROM payments WHERE reservation_id = ? AND voided_at IS NULL", (reservation_id,)
    ).fetchone()
    if reservation["status"] != "EN_ATTENTE" or active_payment:
        return api_error("Ce billet possede deja un paiement actif ou ne peut plus etre paye.", 409)
    cursor = db.execute(
        "INSERT INTO payments (reservation_id, method, amount, received_by) VALUES (?, ?, ?, ?)",
        (reservation_id, method, reservation["amount"], g.user["id"]),
    )
    db.execute("UPDATE reservations SET status = 'PAYE' WHERE id = ?", (reservation_id,))
    audit("API_PAYMENT_RECORDED", f"{reservation['ticket_number']}; {method}; {reservation['amount']}")
    db.commit()
    return jsonify({"message": "Paiement enregistre.", "payment_id": cursor.lastrowid})


@app.post("/api/reservations/<int:reservation_id>/status")
@api_login_required
def api_update_status(reservation_id: int):
    """Change le statut d'un billet depuis l'API."""
    payload = json_payload()
    status = str(payload.get("status", "")).strip()
    if status not in {"PAYE", "CONFIRME", "ANNULE", "UTILISE"}:
        return api_error("Statut invalide.")
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        abort(404)
    current = reservation["status"]
    if status == "ANNULE":
        if not g.user["is_admin"]:
            return api_error("Droits administrateur requis.", 403)
        reason = str(payload.get("cancel_reason", "")).strip()
        active_payment = db.execute(
            "SELECT 1 FROM payments WHERE reservation_id = ? AND voided_at IS NULL", (reservation_id,)
        ).fetchone()
        if active_payment:
            return api_error("Annulez d'abord le paiement actif.", 409)
        if len(reason) < 3:
            return api_error("Motif d'annulation obligatoire.")
        allowed = current in {"EN_ATTENTE", "PAYE", "CONFIRME"}
    else:
        allowed = (current, status) in {("PAYE", "CONFIRME"), ("CONFIRME", "UTILISE")}
    if not allowed:
        return api_error("Ce changement de statut n'est pas autorise.", 409)
    if status == "ANNULE":
        db.execute(
            """UPDATE reservations SET status = ?, cancelled_at = CURRENT_TIMESTAMP,
                      cancel_reason = ?, cancelled_by = ? WHERE id = ?""",
            (status, reason, g.user["id"], reservation_id),
        )
    else:
        db.execute("UPDATE reservations SET status = ? WHERE id = ?", (status, reservation_id))
    audit("API_TICKET_STATUS_CHANGED", f"{reservation['ticket_number']}: {current} -> {status}")
    db.commit()
    row = reservation_with_trip(reservation_id)
    return jsonify({"message": "Statut mis a jour.", "reservation": serialize_reservation(row)})


@app.get("/api/payments")
@api_login_required
def api_payments():
    """Liste les paiements recents."""
    rows = get_db().execute(
        """SELECT p.*, r.ticket_number, r.customer_name, t.origin, t.destination,
                  receiver.username AS receiver_name
           FROM payments p JOIN reservations r ON r.id = p.reservation_id
           JOIN trips t ON t.id = r.trip_id
           LEFT JOIN users receiver ON receiver.id = p.received_by
           ORDER BY p.paid_at DESC LIMIT 500"""
    ).fetchall()
    return jsonify({"payments": [dict(row) for row in rows]})


@app.get("/api/settings")
@api_admin_required
def api_settings():
    """Expose les parametres utiles aux administrateurs."""
    fares = [
        {"origin": origin, "destination": destination, "price": price}
        for (origin, destination), price in get_fares().items()
    ]
    return jsonify({"settings": get_settings(), "cities": list(CITIES), "fares": fares})


@app.route("/help")
@login_required
def help_page():
    """Affiche le guide d'utilisation intégré de la billetterie."""
    return render_template("help.html")


@app.post("/reservations/<int:reservation_id>/status")
@login_required
def update_status(reservation_id: int):
    """Applique uniquement les transitions autorisées du cycle de vie d'un billet."""
    status = request.form.get("status")
    if status not in {"EN_ATTENTE", "PAYE", "CONFIRME", "ANNULE", "UTILISE"}:
        abort(400)
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        abort(404)
    current = reservation["status"]
    if status == "ANNULE":
        if not g.user["is_admin"]:
            abort(403)
        reason = request.form.get("cancel_reason", "").strip()
        active_payment = db.execute(
            "SELECT 1 FROM payments WHERE reservation_id = ? AND voided_at IS NULL", (reservation_id,)
        ).fetchone()
        if active_payment:
            flash("Annulez d'abord le paiement actif afin de conserver une comptabilité correcte.", "error")
            return redirect(url_for("reservation_detail", reservation_id=reservation_id))
        if len(reason) < 3:
            flash("Indiquez le motif d'annulation du billet.", "error")
            return redirect(url_for("reservation_detail", reservation_id=reservation_id))
        allowed = current in {"EN_ATTENTE", "PAYE", "CONFIRME"}
    else:
        allowed = (current, status) in {("PAYE", "CONFIRME"), ("CONFIRME", "UTILISE")}
    if not allowed:
        flash("Ce changement de statut n'est pas autorisé.", "error")
        return redirect(url_for("reservation_detail", reservation_id=reservation_id))
    if status == "ANNULE":
        db.execute(
            """UPDATE reservations SET status = ?, cancelled_at = CURRENT_TIMESTAMP,
                      cancel_reason = ?, cancelled_by = ? WHERE id = ?""",
            (status, reason, g.user["id"], reservation_id),
        )
    else:
        db.execute("UPDATE reservations SET status = ? WHERE id = ?", (status, reservation_id))
    audit("TICKET_STATUS_CHANGED", f"{reservation['ticket_number']}: {current} -> {status}")
    db.commit()
    flash("Statut du billet mis à jour.", "success")
    return redirect(url_for("reservation_detail", reservation_id=reservation_id))


@app.post("/reservations/<int:reservation_id>/payment")
@login_required
def record_payment(reservation_id: int):
    """Enregistre un paiement au montant du billet et marque celui-ci comme payé."""
    method = request.form.get("payment_method")
    # Seuls les moyens de paiement présentés par le formulaire sont acceptés.
    if method not in {"ESPECES", "ORANGE_MONEY", "AIRTEL_MONEY", "MPESA", "AFRIMONEY", "AUTRE"}:
        abort(400)
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        abort(404)
    active_payment = db.execute(
        "SELECT 1 FROM payments WHERE reservation_id = ? AND voided_at IS NULL", (reservation_id,)
    ).fetchone()
    if reservation["status"] != "EN_ATTENTE" or active_payment:
        flash("Ce billet possède déjà un paiement actif ou ne peut plus être payé.", "error")
        return redirect(url_for("reservation_detail", reservation_id=reservation_id))
    db.execute(
        "INSERT INTO payments (reservation_id, method, amount, received_by) VALUES (?, ?, ?, ?)",
        (reservation_id, method, reservation["amount"], g.user["id"]),
    )
    db.execute("UPDATE reservations SET status = 'PAYE' WHERE id = ?", (reservation_id,))
    audit("PAYMENT_RECORDED", f"{reservation['ticket_number']}; {method}; {reservation['amount']}")
    db.commit()
    flash("Paiement enregistré.", "success")
    return redirect(url_for("reservation_detail", reservation_id=reservation_id))


@app.get("/reservations/<int:reservation_id>/ticket.pdf")
@login_required
def reservation_ticket_pdf(reservation_id: int):
    """Telecharge le billet PDF depuis la fiche reservation."""
    reservation = reservation_with_trip(reservation_id)
    if reservation is None:
        abort(404)
    return send_file(
        BytesIO(build_ticket_pdf(reservation)),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{reservation['ticket_number']}.pdf",
    )


@app.post("/reservations/<int:reservation_id>/payments/shwary")
@login_required
def start_web_shwary_payment(reservation_id: int):
    """Lance une demande de paiement Shwary depuis le site web."""
    reservation = reservation_with_trip(reservation_id)
    if reservation is None:
        abort(404)
    if reservation["status"] != "EN_ATTENTE":
        flash("Shwary ne peut être lancé que pour un billet en attente.", "error")
        return redirect(url_for("reservation_detail", reservation_id=reservation_id))
    phone = request.form.get("phone", "").strip() or reservation["customer_phone"]
    try:
        payment_request = start_shwary_payment(reservation, phone)
    except RuntimeError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash(f"Demande Shwary impossible : {exc}", "error")
    else:
        flash(f"Demande Shwary envoyée. Référence : {payment_request['reference_id']}", "success")
    return redirect(url_for("reservation_detail", reservation_id=reservation_id))


@app.post("/payments/<int:payment_id>/void")
@admin_required
def void_payment(payment_id: int):
    """Annule un paiement actif avec un motif traçable, sans supprimer son historique."""
    reason = request.form.get("void_reason", "").strip()
    if len(reason) < 3:
        flash("Indiquez un motif d'annulation du paiement.", "error")
        return redirect(request.referrer or url_for("dashboard"))
    db = get_db()
    payment = db.execute(
        """SELECT p.*, r.status FROM payments p JOIN reservations r ON r.id = p.reservation_id
           WHERE p.id = ?""", (payment_id,)
    ).fetchone()
    if payment is None:
        abort(404)
    if payment["voided_at"] is not None or payment["status"] == "UTILISE":
        flash("Ce paiement ne peut pas être annulé.", "error")
    else:
        db.execute(
            "UPDATE payments SET voided_at = CURRENT_TIMESTAMP, void_reason = ?, voided_by = ? WHERE id = ?",
            (reason, g.user["id"], payment_id),
        )
        db.execute("UPDATE reservations SET status = 'EN_ATTENTE' WHERE id = ?", (payment["reservation_id"],))
        audit("PAYMENT_VOIDED", f"Paiement {payment_id}; {reason}")
        db.commit()
        flash("Paiement annulé et billet replacé en attente.", "success")
    return redirect(url_for("reservation_detail", reservation_id=payment["reservation_id"]))


@app.route("/payments/<int:payment_id>/receipt")
@login_required
def payment_receipt(payment_id: int):
    """Affiche un reçu de paiement autonome et imprimable."""
    payment = get_db().execute(
        """SELECT p.*, r.ticket_number, r.customer_name, r.seat_number,
                  t.origin, t.destination, u.username AS receiver_name
           FROM payments p JOIN reservations r ON r.id = p.reservation_id
           JOIN trips t ON t.id = r.trip_id LEFT JOIN users u ON u.id = p.received_by
           WHERE p.id = ?""", (payment_id,)
    ).fetchone()
    if payment is None:
        abort(404)
    return render_template("payment_receipt.html", payment=payment)


@app.cli.command("init-db")
def init_db_command():
    """Initialise la base SQLite."""
    init_db()
    print("Base de données initialisée.")


@app.cli.command("create-admin")
@click.argument("username")
@click.password_option(confirmation_prompt=True)
def create_admin_command(username: str, password: str):
    """Crée directement dans SQLite le compte administrateur initial."""
    if not valid_username(username) or len(password) < 8:
        raise click.ClickException("Identifiant invalide ou mot de passe de moins de 8 caractères.")
    database_path = Path(app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as db:
        try:
            db.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)", (username, generate_password_hash(password)))
            db.commit()
        except sqlite3.IntegrityError as error:
            raise click.ClickException("Cet identifiant existe déjà.") from error
    click.echo("Compte administrateur créé dans la base de données.")


@app.cli.command("ensure-admin")
def ensure_admin_command():
    """Cree le premier administrateur depuis ADMIN_USERNAME et ADMIN_PASSWORD."""
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        click.echo("ADMIN_USERNAME ou ADMIN_PASSWORD absent : aucun administrateur automatique cree.")
        return
    if not valid_username(username) or len(password) < 8:
        raise click.ClickException("ADMIN_USERNAME invalide ou ADMIN_PASSWORD trop court.")
    database_path = Path(app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as db:
        active_admin = db.execute("SELECT id FROM users WHERE is_admin = 1 AND is_active = 1 LIMIT 1").fetchone()
        if active_admin:
            click.echo("Un administrateur actif existe deja.")
            return
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            db.execute(
                "UPDATE users SET password_hash = ?, is_admin = 1, is_active = 1 WHERE id = ?",
                (generate_password_hash(password), existing[0]),
            )
        else:
            db.execute(
                "INSERT INTO users (username, password_hash, is_admin, is_active) VALUES (?, ?, 1, 1)",
                (username, generate_password_hash(password)),
            )
        db.commit()
    click.echo("Administrateur initial pret.")


@app.errorhandler(400)
def bad_request(_error):
    if request.path.startswith("/api/"):
        return api_error("Requete invalide.", 400)
    return render_template("error.html", code=400, title="Requête invalide", message="Le formulaire est invalide ou a expiré. Rechargez la page puis réessayez."), 400


@app.errorhandler(403)
def forbidden(_error):
    if request.path.startswith("/api/"):
        return api_error("Acces refuse.", 403)
    return render_template("error.html", code=403, title="Accès refusé", message="Vous ne disposez pas des droits nécessaires pour cette action."), 403


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return api_error("Ressource introuvable.", 404)
    return render_template("error.html", code=404, title="Page introuvable", message="La page ou la ressource demandée n'existe pas."), 404


@app.errorhandler(409)
def conflict(_error):
    if request.path.startswith("/api/"):
        return api_error("Conflit avec l'etat actuel des donnees.", 409)
    return render_template("error.html", code=400, title="Action impossible", message="Cette action entre en conflit avec l'etat actuel des donnees."), 409


@app.errorhandler(500)
def server_error(_error):
    if request.path.startswith("/api/"):
        return api_error("Erreur interne.", 500)
    return render_template("error.html", code=500, title="Erreur interne", message="Une erreur inattendue est survenue. Aucune donnée ne doit être ressaisie avant vérification."), 500


if __name__ == "__main__":
    # Lors d'un lancement direct, crée la base seulement si elle n'existe pas encore.
    if not DATABASE.exists():
        init_db()
    app.run(debug=True)
