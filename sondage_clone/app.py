from __future__ import annotations

import re
import smtplib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent


def _load_local_env_file() -> None:
    env_candidates = [BASE_DIR / ".env.local", BASE_DIR / ".env"]

    for env_path in env_candidates:
        if not env_path.exists():
            continue

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_key = key.strip()
            env_value = value.strip().strip('"').strip("'")
            if env_key and env_key not in os.environ:
                os.environ[env_key] = env_value


_load_local_env_file()

DB_PATH = Path(os.environ.get("SONDAGE_DB_PATH", str(BASE_DIR / "sondage.db")))

ALLOWED_CHOICES = {"yes", "no"}
ALLOWED_POLL_TYPES = {"meeting", "opinion", "event", "training", "shift", "meal", "trip"}
ALLOWED_RESPONSE_MODES = {"single", "multiple"}
POLL_TYPE_LABELS = {
    "meeting": "📅 Réunion / RDV",
    "opinion": "💡 Prise d’avis / décision",
    "event": "🎉 Événement",
    "training": "🎓 Formation",
    "shift": "🕒 Planning / permanence",
    "meal": "🍽️ Sortie / repas",
    "trip": "✈️ Voyage / déplacement",
}
RESPONSE_MODE_LABELS = {
    "single": "Choix unique (1 option parmi n)",
    "multiple": "Choix multiple (plusieurs options)",
    "availability": "Choix multiple (plusieurs options)",
}
FEEDBACK_COMPONENTS = {
    "navigation",
    "creation",
    "vote",
    "results",
    "account",
    "performance",
    "other",
}
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DEFAULT_CONSENT_VERSION = "v1.0-2026-02-15"


def create_app() -> Flask:
    app = Flask(__name__)
    secret_key = os.environ.get("SONDAGE_SECRET_KEY", "change-me-in-production")
    app.config["SECRET_KEY"] = secret_key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    cookie_samesite = os.environ.get("SONDAGE_COOKIE_SAMESITE", "Lax").strip().title()
    if cookie_samesite not in {"Lax", "Strict", "None"}:
        cookie_samesite = "Lax"
    app.config["SESSION_COOKIE_SAMESITE"] = cookie_samesite
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SONDAGE_COOKIE_SECURE", "0") == "1"
    app.config["SESSION_COOKIE_NAME"] = "sondage_session"
    session_timeout_raw = os.environ.get("SONDAGE_SESSION_TIMEOUT_MINUTES", "720").strip()
    try:
        session_timeout_minutes = int(session_timeout_raw)
    except ValueError:
        session_timeout_minutes = 720
    session_timeout_minutes = max(15, min(43200, session_timeout_minutes))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=session_timeout_minutes)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    if secret_key == "change-me-in-production":
        app.logger.warning("SONDAGE_SECRET_KEY utilise une valeur par défaut: change-la en production.")

    auth_enabled = os.environ.get("SONDAGE_AUTH_ENABLED", "0") == "1"
    auth_allow_registration = os.environ.get("SONDAGE_AUTH_ALLOW_REGISTRATION", "1") == "1"
    bootstrap_admin_email = os.environ.get("SONDAGE_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    consent_version = os.environ.get("SONDAGE_CONSENT_VERSION", DEFAULT_CONSENT_VERSION).strip() or DEFAULT_CONSENT_VERSION

    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASS", "")
    smtp_use_tls = os.environ.get("SMTP_USE_TLS", "1") == "1"
    smtp_use_ssl = os.environ.get("SMTP_USE_SSL", "0") == "1"
    smtp_from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_user).strip()
    smtp_from_name = os.environ.get("SMTP_FROM_NAME", "Sondage-noschoixpourvous").strip() or "Sondage-noschoixpourvous"
    feedback_to_email = os.environ.get("FEEDBACK_TO_EMAIL", smtp_from_email).strip().lower()

    def parse_email_list(raw: str) -> list[str]:
        parts = re.split(r"[\n,;]+", raw)
        emails: list[str] = []
        seen: set[str] = set()
        for part in parts:
            email = part.strip().lower()
            if not email:
                continue
            if not EMAIL_REGEX.match(email):
                continue
            if email in seen:
                continue
            seen.add(email)
            emails.append(email)
        return emails

    def smtp_configured() -> bool:
        return bool(smtp_host and smtp_from_email)

    def get_user_by_id(user_id: int) -> sqlite3.Row | None:
        db = get_db()
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def get_user_by_email(email: str) -> sqlite3.Row | None:
        db = get_db()
        return db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def get_current_user() -> sqlite3.Row | None:
        cached = g.get("current_user")
        if cached is not None:
            return cached

        raw_user_id = session.get("app_user_id")
        if not raw_user_id:
            return None

        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            session.pop("app_user_id", None)
            return None

        user = get_user_by_id(user_id)
        if user is None:
            session.pop("app_user_id", None)
            return None

        if not bool(user["is_active"]):
            session.pop("app_user_id", None)
            return None

        g.current_user = user
        return user

    def app_session_authenticated() -> bool:
        return get_current_user() is not None

    def send_poll_invitations(recipients: list[str], poll_title: str, poll_url: str, creator_name: str) -> tuple[int, int, list[str]]:
        if not recipients:
            return 0, 0, []
        if not smtp_configured():
            return 0, len(recipients), recipients

        sender_display = f"{smtp_from_name} <{smtp_from_email}>"
        sender_label = creator_name or "L'organisateur"
        failed: list[str] = []
        sent_count = 0

        try:
            smtp_client = smtplib.SMTP_SSL if smtp_use_ssl else smtplib.SMTP
            with smtp_client(smtp_host, smtp_port, timeout=20) as server:
                if smtp_use_tls and not smtp_use_ssl:
                    server.starttls()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)

                for email in recipients:
                    message = EmailMessage()
                    message["Subject"] = f"Invitation sondage : {poll_title}"
                    message["From"] = sender_display
                    message["To"] = email
                    message.set_content(
                        f"Bonjour,\n\n"
                        f"{sender_label} t'invite à participer au sondage : {poll_title}\n\n"
                        f"Lien : {poll_url}\n\n"
                        f"Merci.\n"
                    )
                    try:
                        server.send_message(message)
                        sent_count += 1
                    except Exception:
                        failed.append(email)
        except Exception as exc:
            app.logger.exception("Erreur envoi SMTP: %s", exc)
            return 0, len(recipients), recipients

        return sent_count, len(failed), failed

    def send_feedback_email(
        component: str,
        message_text: str,
        sender_name: str,
        sender_email: str,
        page_url: str,
    ) -> bool:
        if not smtp_configured() or not feedback_to_email:
            return False

        sender_display = f"{smtp_from_name} <{smtp_from_email}>"
        component_label = component or "other"
        feedback_time = datetime.utcnow().isoformat(timespec="seconds")

        message = EmailMessage()
        message["Subject"] = f"Nouveau feedback ({component_label})"
        message["From"] = sender_display
        message["To"] = feedback_to_email
        message.set_content(
            f"Date UTC: {feedback_time}\n"
            f"Composant: {component_label}\n"
            f"Page: {page_url or '-'}\n"
            f"Nom: {sender_name or '-'}\n"
            f"Email: {sender_email or '-'}\n\n"
            f"Message:\n{message_text}\n"
        )

        try:
            smtp_client = smtplib.SMTP_SSL if smtp_use_ssl else smtplib.SMTP
            with smtp_client(smtp_host, smtp_port, timeout=20) as server:
                if smtp_use_tls and not smtp_use_ssl:
                    server.starttls()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                server.send_message(message)
            return True
        except Exception as exc:
            app.logger.exception("Erreur envoi feedback SMTP: %s", exc)
            return False

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            connection = sqlite3.connect(DB_PATH)
            connection.row_factory = sqlite3.Row
            g.db = connection
        return g.db

    @app.teardown_appcontext
    def close_db(_: object) -> None:
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def init_db() -> None:
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                creator_name TEXT,
                created_by_user_id INTEGER,
                poll_type TEXT NOT NULL DEFAULT 'meeting',
                response_mode TEXT NOT NULL DEFAULT 'availability',
                deadline_at TEXT,
                archived_at TEXT,
                archived_by_user_id INTEGER,
                organizer_code_hash TEXT,
                rgpd_creator_consent_at TEXT,
                rgpd_consent_version TEXT,
                rgpd_email_rights_confirmed INTEGER NOT NULL DEFAULT 0,
                rgpd_email_rights_confirmed_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                position INTEGER NOT NULL,
                FOREIGN KEY (poll_id) REFERENCES polls (id)
            );

            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                slot_id INTEGER NOT NULL,
                participant_name TEXT NOT NULL,
                participant_email TEXT,
                choice TEXT NOT NULL,
                comment TEXT,
                rgpd_vote_consent_at TEXT,
                rgpd_consent_version TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (poll_id) REFERENCES polls (id),
                FOREIGN KEY (slot_id) REFERENCES slots (id)
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_admin INTEGER NOT NULL DEFAULT 0,
                consent_auth_at TEXT,
                consent_auth_version TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        poll_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(polls)").fetchall()
        }
        if "poll_type" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN poll_type TEXT NOT NULL DEFAULT 'meeting'")
        if "created_by_user_id" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN created_by_user_id INTEGER")
        if "response_mode" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN response_mode TEXT NOT NULL DEFAULT 'availability'")
        if "deadline_at" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN deadline_at TEXT")
        if "archived_at" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN archived_at TEXT")
        if "archived_by_user_id" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN archived_by_user_id INTEGER")
        if "organizer_code_hash" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN organizer_code_hash TEXT")
        if "rgpd_creator_consent_at" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN rgpd_creator_consent_at TEXT")
        if "rgpd_consent_version" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN rgpd_consent_version TEXT")
        if "rgpd_email_rights_confirmed" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN rgpd_email_rights_confirmed INTEGER NOT NULL DEFAULT 0")
        if "rgpd_email_rights_confirmed_at" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN rgpd_email_rights_confirmed_at TEXT")

        vote_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(votes)").fetchall()
        }
        if "participant_email" not in vote_columns:
            db.execute("ALTER TABLE votes ADD COLUMN participant_email TEXT")
        if "comment" not in vote_columns:
            db.execute("ALTER TABLE votes ADD COLUMN comment TEXT")
        if "rgpd_vote_consent_at" not in vote_columns:
            db.execute("ALTER TABLE votes ADD COLUMN rgpd_vote_consent_at TEXT")
        if "rgpd_consent_version" not in vote_columns:
            db.execute("ALTER TABLE votes ADD COLUMN rgpd_consent_version TEXT")

        user_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "is_active" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "is_admin" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "consent_auth_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN consent_auth_at TEXT")
        if "consent_auth_version" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN consent_auth_version TEXT")

        if bootstrap_admin_email and EMAIL_REGEX.match(bootstrap_admin_email):
            db.execute(
                "UPDATE users SET is_admin = 1, is_active = 1 WHERE email = ?",
                (bootstrap_admin_email,),
            )

        db.commit()

    def _csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(24)
            session["csrf_token"] = token
        return token

    def validate_csrf() -> bool:
        form_token = request.form.get("csrf_token", "")
        session_token = session.get("csrf_token", "")
        return bool(form_token and session_token and secrets.compare_digest(form_token, session_token))

    def parse_deadline(value: str) -> str | None:
        if not value:
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
            return parsed.isoformat(timespec="minutes")
        except ValueError:
            return None

    def normalize_response_mode(raw_mode: str | None) -> str:
        mode = (raw_mode or "").strip().lower()
        if mode == "availability":
            return "multiple"
        if mode in ALLOWED_RESPONSE_MODES:
            return mode
        return "single"

    def is_deadline_passed(deadline_at: str | None) -> bool:
        if not deadline_at:
            return False
        try:
            deadline = datetime.fromisoformat(deadline_at)
            return datetime.utcnow() > deadline
        except ValueError:
            return False

    def admin_session_key(poll_id: int) -> str:
        return f"admin_poll_{poll_id}"

    def voter_session_key(poll_id: int) -> str:
        return f"voter_poll_{poll_id}"

    def is_admin_authenticated(poll: sqlite3.Row) -> bool:
        return bool(session.get(admin_session_key(poll["id"]), False))

    def recommendation(summary_rows: list[sqlite3.Row]) -> sqlite3.Row | None:
        if not summary_rows:
            return None
        return max(summary_rows, key=lambda row: (row["yes_count"], -row["no_count"]))

    @app.context_processor
    def inject_csrf() -> dict:
        current_user = get_current_user()
        return {
            "csrf_token": _csrf_token,
            "app_auth_enabled": auth_enabled,
            "app_auth_logged_in": app_session_authenticated(),
            "app_auth_user_email": current_user["email"] if current_user else "",
            "app_auth_user_is_admin": bool(current_user["is_admin"]) if current_user else False,
            "app_auth_allow_registration": auth_allow_registration,
            "consent_version": consent_version,
            "poll_type_labels": POLL_TYPE_LABELS,
            "response_mode_labels": RESPONSE_MODE_LABELS,
        }

    @app.before_request
    def enforce_optional_app_auth():
        if not auth_enabled:
            return None

        public_endpoints = {
            "auth_login",
            "auth_register",
            "privacy_policy",
            "view_poll",
            "vote",
            "vote_status",
            "feedback_submit",
            "admin_login",
            "admin_logout",
            "static",
        }

        if request.endpoint in public_endpoints:
            return None

        if app_session_authenticated():
            return None

        next_url = request.full_path if request.method == "GET" and request.query_string else request.path
        return redirect(url_for("auth_login", next=next_url))

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:;"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def get_poll_by_token(token: str) -> sqlite3.Row | None:
        db = get_db()
        return db.execute("SELECT * FROM polls WHERE token = ?", (token,)).fetchone()

    def get_poll_slots(poll_id: int) -> list[sqlite3.Row]:
        db = get_db()
        return db.execute(
            "SELECT * FROM slots WHERE poll_id = ? ORDER BY position ASC, id ASC",
            (poll_id,),
        ).fetchall()

    def aggregate_results(poll_id: int) -> list[sqlite3.Row]:
        db = get_db()
        return db.execute(
            """
            SELECT
                s.id,
                s.label,
                COALESCE(SUM(CASE WHEN v.choice = 'yes' THEN 1 ELSE 0 END), 0) AS yes_count,
                COALESCE(SUM(CASE WHEN v.choice = 'no' THEN 1 ELSE 0 END), 0) AS no_count
            FROM slots s
            LEFT JOIN votes v ON v.slot_id = s.id
            WHERE s.poll_id = ?
            GROUP BY s.id, s.label, s.position
            ORDER BY s.position ASC, s.id ASC
            """,
            (poll_id,),
        ).fetchall()

    def participant_rows(poll_id: int) -> tuple[list[str], dict[str, dict[int, str]], dict[str, str]]:
        db = get_db()
        rows = db.execute(
            """
            SELECT participant_name, participant_email, slot_id, choice
            FROM votes
            WHERE poll_id = ?
            ORDER BY participant_name COLLATE NOCASE ASC, participant_email COLLATE NOCASE ASC
            """,
            (poll_id,),
        ).fetchall()

        participants: list[str] = []
        matrix: dict[str, dict[int, str]] = {}
        labels: dict[str, str] = {}
        for row in rows:
            name = (row["participant_name"] or "").strip()
            email = (row["participant_email"] or "").strip().lower()
            key = email or name
            if not key:
                continue
            if key not in matrix:
                matrix[key] = {}
                participants.append(key)
            if key not in labels:
                if name:
                    labels[key] = name
                elif email:
                    labels[key] = email.split("@", 1)[0]
                else:
                    labels[key] = "Participant"
            matrix[key][row["slot_id"]] = row["choice"]
        return participants, matrix, labels

    def participant_comments(poll_id: int) -> dict[str, str]:
        db = get_db()
        rows = db.execute(
            """
            SELECT participant_name, participant_email, MAX(COALESCE(comment, '')) AS comment
            FROM votes
            WHERE poll_id = ?
            GROUP BY participant_name, participant_email
            """,
            (poll_id,),
        ).fetchall()
        comments: dict[str, str] = {}
        for row in rows:
            key = (row["participant_email"] or "").strip().lower() or (row["participant_name"] or "").strip()
            if key and row["comment"]:
                comments[key] = row["comment"]
        return comments

    @app.get("/")
    def home() -> str:
        return render_template("home.html")

    @app.get("/privacy")
    def privacy_policy() -> str:
        return render_template("privacy.html")

    @app.route("/auth/login", methods=["GET", "POST"])
    def auth_login():
        if not auth_enabled:
            return redirect(url_for("home"))

        if app_session_authenticated():
            return redirect(url_for("home"))

        if request.method == "POST":
            if not validate_csrf():
                flash("Session invalide. Recharge la page puis réessaie.", "error")
                return redirect(url_for("auth_login"))

            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            next_url = request.form.get("next", "")
            rgpd_auth_login_ok = request.form.get("rgpd_auth_login") == "on"
            user = get_user_by_email(email) if EMAIL_REGEX.match(email) else None

            if not rgpd_auth_login_ok:
                flash("Tu dois accepter la politique de confidentialité pour te connecter.", "error")
                return redirect(url_for("auth_login", next=next_url))

            if user is not None and check_password_hash(user["password_hash"], password):
                if not bool(user["is_active"]):
                    flash("Compte désactivé. Contacte un administrateur.", "error")
                    return redirect(url_for("auth_login", next=next_url))
                now = datetime.utcnow().isoformat(timespec="seconds")
                db = get_db()
                db.execute(
                    "UPDATE users SET consent_auth_at = ?, consent_auth_version = ? WHERE id = ?",
                    (now, consent_version, user["id"]),
                )
                db.commit()
                session.clear()
                session["app_user_id"] = user["id"]
                session.permanent = True
                if not next_url.startswith("/"):
                    next_url = url_for("home")
                return redirect(next_url)

            flash("Identifiants invalides.", "error")

        next_url = request.args.get("next", "")
        if not next_url.startswith("/"):
            next_url = url_for("home")
        return render_template("login.html", next_url=next_url)

    @app.route("/auth/register", methods=["GET", "POST"])
    def auth_register():
        if not auth_enabled:
            return redirect(url_for("home"))

        if app_session_authenticated():
            return redirect(url_for("home"))

        if not auth_allow_registration:
            flash("La création de compte est désactivée.", "error")
            return redirect(url_for("auth_login"))

        if request.method == "POST":
            if not validate_csrf():
                flash("Session invalide. Recharge la page puis réessaie.", "error")
                return redirect(url_for("auth_register"))

            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")
            next_url = request.form.get("next", "")
            rgpd_auth_register_ok = request.form.get("rgpd_auth_register") == "on"

            if not rgpd_auth_register_ok:
                flash("Tu dois accepter la politique de confidentialité pour créer un compte.", "error")
                return redirect(url_for("auth_register", next=next_url))

            if not EMAIL_REGEX.match(email):
                flash("Email invalide.", "error")
                return redirect(url_for("auth_register", next=next_url))

            if len(password) < 8:
                flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
                return redirect(url_for("auth_register", next=next_url))

            if password != password_confirm:
                flash("Les mots de passe ne correspondent pas.", "error")
                return redirect(url_for("auth_register", next=next_url))

            if get_user_by_email(email) is not None:
                flash("Un compte existe déjà avec cet email.", "error")
                return redirect(url_for("auth_login", next=next_url))

            db = get_db()
            now = datetime.utcnow().isoformat(timespec="seconds")
            user_count = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
            is_first_user_admin = 1 if user_count == 0 else 0
            db.execute(
                "INSERT INTO users (email, password_hash, is_active, is_admin, consent_auth_at, consent_auth_version, created_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
                (email, generate_password_hash(password), is_first_user_admin, now, consent_version, now),
            )
            db.commit()

            user = get_user_by_email(email)
            if user is not None:
                session.clear()
                session["app_user_id"] = user["id"]
                session.permanent = True

            if not next_url.startswith("/"):
                next_url = url_for("home")

            flash("Compte créé avec succès.", "success")
            return redirect(next_url)

        next_url = request.args.get("next", "")
        if not next_url.startswith("/"):
            next_url = url_for("home")
        return render_template("register.html", next_url=next_url)

    @app.post("/auth/logout")
    def auth_logout():
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("home"))

        session.clear()
        flash("Déconnecté.", "success")
        return redirect(url_for("auth_login"))

    @app.get("/admin/users")
    def admin_users():
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_login", next=request.path))

        if not bool(current_user["is_admin"]):
            flash("Accès refusé: droits administrateur requis.", "error")
            return redirect(url_for("home"))

        db = get_db()
        users = db.execute(
            "SELECT id, email, is_active, is_admin, consent_auth_at, consent_auth_version, created_at FROM users ORDER BY created_at ASC, id ASC"
        ).fetchall()
        return render_template("admin_users.html", users=users)

    @app.post("/admin/users/create")
    def admin_create_user():
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("admin_users"))

        current_user = get_current_user()
        if current_user is None or not bool(current_user["is_admin"]):
            flash("Accès refusé: droits administrateur requis.", "error")
            return redirect(url_for("home"))

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        is_admin_new = request.form.get("is_admin") == "on"
        is_active_new = request.form.get("is_active") != "off"

        if not EMAIL_REGEX.match(email):
            flash("Email invalide.", "error")
            return redirect(url_for("admin_users"))

        if len(password) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
            return redirect(url_for("admin_users"))

        if get_user_by_email(email) is not None:
            flash("Un compte existe déjà avec cet email.", "error")
            return redirect(url_for("admin_users"))

        db = get_db()
        now = datetime.utcnow().isoformat(timespec="seconds")
        db.execute(
            "INSERT INTO users (email, password_hash, is_active, is_admin, consent_auth_at, consent_auth_version, created_at) VALUES (?, ?, ?, ?, NULL, NULL, ?)",
            (email, generate_password_hash(password), 1 if is_active_new else 0, 1 if is_admin_new else 0, now),
        )
        db.commit()
        flash("Utilisateur ajouté.", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:user_id>/toggle-active")
    def admin_toggle_user_active(user_id: int):
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("admin_users"))

        current_user = get_current_user()
        if current_user is None or not bool(current_user["is_admin"]):
            flash("Accès refusé: droits administrateur requis.", "error")
            return redirect(url_for("home"))

        db = get_db()
        target_user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target_user is None:
            flash("Compte introuvable.", "error")
            return redirect(url_for("admin_users"))

        if target_user["id"] == current_user["id"]:
            flash("Tu ne peux pas désactiver ton propre compte.", "error")
            return redirect(url_for("admin_users"))

        new_value = 0 if bool(target_user["is_active"]) else 1

        if new_value == 0 and bool(target_user["is_admin"]):
            admins_count = db.execute("SELECT COUNT(*) AS total FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()["total"]
            if admins_count <= 1:
                flash("Impossible: il doit rester au moins un administrateur actif.", "error")
                return redirect(url_for("admin_users"))

        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_value, user_id))
        db.commit()
        flash("Statut du compte mis à jour.", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:user_id>/toggle-admin")
    def admin_toggle_user_admin(user_id: int):
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("admin_users"))

        current_user = get_current_user()
        if current_user is None or not bool(current_user["is_admin"]):
            flash("Accès refusé: droits administrateur requis.", "error")
            return redirect(url_for("home"))

        db = get_db()
        target_user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target_user is None:
            flash("Compte introuvable.", "error")
            return redirect(url_for("admin_users"))

        if target_user["id"] == current_user["id"] and bool(target_user["is_admin"]):
            flash("Tu ne peux pas retirer ton propre rôle administrateur.", "error")
            return redirect(url_for("admin_users"))

        new_value = 0 if bool(target_user["is_admin"]) else 1

        if new_value == 0 and bool(target_user["is_active"]):
            active_admins_count = db.execute("SELECT COUNT(*) AS total FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()["total"]
            if active_admins_count <= 1:
                flash("Impossible: il doit rester au moins un administrateur actif.", "error")
                return redirect(url_for("admin_users"))

        db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (new_value, user_id))
        db.commit()
        flash("Rôle administrateur mis à jour.", "success")
        return redirect(url_for("admin_users"))

    @app.get("/create")
    def create_poll_page():
        flash("Utilise le formulaire d'accueil pour créer un sondage.", "error")
        return redirect(url_for("home"))

    @app.get("/my-polls")
    def my_polls():
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_login", next=request.path))

        db = get_db()
        polls = db.execute(
            """
            SELECT
                p.*,
                COALESCE(
                    (
                        SELECT COUNT(*)
                        FROM (
                            SELECT DISTINCT
                                CASE
                                    WHEN COALESCE(TRIM(v.participant_email), '') <> '' THEN LOWER(TRIM(v.participant_email))
                                    ELSE 'name:' || LOWER(TRIM(v.participant_name))
                                END AS voter_key
                            FROM votes v
                            WHERE v.poll_id = p.id
                        ) u
                    ),
                    0
                ) AS votes_count
            FROM polls p
            WHERE p.created_by_user_id = ?
            ORDER BY COALESCE(p.archived_at, ''), p.created_at DESC
            """,
            (current_user["id"],),
        ).fetchall()

        active_polls = [poll for poll in polls if not poll["archived_at"]]
        archived_polls = [poll for poll in polls if poll["archived_at"]]
        return render_template("my_polls.html", active_polls=active_polls, archived_polls=archived_polls)

    @app.post("/poll/<token>/archive")
    def archive_poll(token: str):
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("my_polls"))

        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_login", next=url_for("my_polls")))

        poll = get_poll_by_token(token)
        if poll is None:
            flash("Sondage introuvable.", "error")
            return redirect(url_for("my_polls"))

        can_manage = bool(current_user["is_admin"]) or (
            poll["created_by_user_id"] is not None and int(poll["created_by_user_id"]) == int(current_user["id"])
        )
        if not can_manage:
            flash("Accès refusé pour ce sondage.", "error")
            return redirect(url_for("my_polls"))

        action = request.form.get("action", "archive").strip().lower()
        db = get_db()
        if action == "unarchive":
            db.execute("UPDATE polls SET archived_at = NULL, archived_by_user_id = NULL WHERE id = ?", (poll["id"],))
            flash("Sondage désarchivé.", "success")
        else:
            now = datetime.utcnow().isoformat(timespec="seconds")
            db.execute(
                "UPDATE polls SET archived_at = ?, archived_by_user_id = ? WHERE id = ?",
                (now, current_user["id"], poll["id"]),
            )
            flash("Sondage archivé.", "success")
        db.commit()
        return redirect(url_for("my_polls"))

    @app.post("/create")
    def create_poll():
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("home"))

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        creator_name = request.form.get("creator_name", "").strip()
        poll_type = request.form.get("poll_type", "meeting").strip().lower()
        response_mode = normalize_response_mode(request.form.get("response_mode", "single"))
        deadline_input = request.form.get("deadline_at", "").strip()
        organizer_code = request.form.get("organizer_code", "")
        participant_emails_raw = request.form.get("participant_emails", "")
        rgpd_creator_ok = request.form.get("rgpd_creator") == "on"
        rgpd_email_rights_ok = request.form.get("rgpd_email_rights") == "on"
        raw_slots = request.form.get("slots", "")

        if not rgpd_creator_ok:
            flash("Tu dois accepter la politique de confidentialité pour créer un sondage.", "error")
            return redirect(url_for("home"))

        slots = [line.strip()[:120] for line in raw_slots.splitlines() if line.strip()]

        if poll_type not in ALLOWED_POLL_TYPES:
            poll_type = "meeting"
        deadline_at = parse_deadline(deadline_input)

        if deadline_input and deadline_at is None:
            flash("Date limite invalide.", "error")
            return redirect(url_for("home"))

        if not title:
            flash("Le titre du sondage est obligatoire.", "error")
            return redirect(url_for("home"))

        title = title[:120]
        description = description[:600]
        creator_name = creator_name[:80]

        if not creator_name:
            flash("Le nom et prénom de l'organisateur est obligatoire.", "error")
            return redirect(url_for("home"))

        if len(slots) < 2:
            flash("Ajoute au moins 2 créneaux pour créer le sondage.", "error")
            return redirect(url_for("home"))

        if len(slots) > 30:
            flash("Maximum 30 créneaux/choix par sondage.", "error")
            return redirect(url_for("home"))

        organizer_code = organizer_code.strip()
        if len(organizer_code) < 8:
            flash("Le code organisateur doit contenir au moins 8 caractères.", "error")
            return redirect(url_for("home"))

        organizer_code_hash = generate_password_hash(organizer_code)
        participant_emails = parse_email_list(participant_emails_raw)
        current_user = get_current_user()
        created_by_user_id = current_user["id"] if current_user is not None else None

        if participant_emails and not rgpd_email_rights_ok:
            flash("Tu dois confirmer que les participants ont donné leur accord pour recevoir des emails.", "error")
            return redirect(url_for("home"))

        token = secrets.token_urlsafe(6)
        now = datetime.utcnow().isoformat(timespec="seconds")
        rgpd_email_rights_confirmed = 1 if bool(participant_emails and rgpd_email_rights_ok) else 0
        rgpd_email_rights_confirmed_at = now if rgpd_email_rights_confirmed else None

        db = get_db()
        db.execute(
            """
            INSERT INTO polls (
                token,
                title,
                description,
                creator_name,
                created_by_user_id,
                poll_type,
                response_mode,
                deadline_at,
                organizer_code_hash,
                rgpd_creator_consent_at,
                rgpd_consent_version,
                rgpd_email_rights_confirmed,
                rgpd_email_rights_confirmed_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                title,
                description,
                creator_name,
                created_by_user_id,
                poll_type,
                response_mode,
                deadline_at,
                organizer_code_hash,
                now,
                consent_version,
                rgpd_email_rights_confirmed,
                rgpd_email_rights_confirmed_at,
                now,
            ),
        )
        poll_id = db.execute("SELECT id FROM polls WHERE token = ?", (token,)).fetchone()["id"]

        for position, label in enumerate(slots, start=1):
            db.execute(
                "INSERT INTO slots (poll_id, label, position) VALUES (?, ?, ?)",
                (poll_id, label, position),
            )

        db.commit()
        session[admin_session_key(poll_id)] = True
        poll_link = url_for("view_poll", token=token, _external=True)
        sent_count, failed_count, _ = send_poll_invitations(participant_emails, title, poll_link, creator_name)

        if participant_emails and not smtp_configured():
            flash("Sondage créé. SMTP non configuré: invitations non envoyées.", "error")
        elif participant_emails and sent_count > 0 and failed_count == 0:
            flash(f"Sondage créé. Invitations envoyées ({sent_count}).", "success")
        elif participant_emails and sent_count > 0 and failed_count > 0:
            flash(f"Sondage créé. Invitations envoyées: {sent_count}, échecs: {failed_count}.", "error")
        else:
            flash("Sondage créé avec succès. Partage le lien !", "success")

        return redirect(url_for("view_poll", token=token))

    @app.get("/poll/<token>")
    def view_poll(token: str):
        poll = get_poll_by_token(token)
        if poll is None:
            flash("Sondage introuvable.", "error")
            return redirect(url_for("home"))

        current_user = get_current_user()
        slots = get_poll_slots(poll["id"])
        summary = aggregate_results(poll["id"])
        summary_sorted = sorted(summary, key=lambda row: (-int(row["yes_count"]), int(row["no_count"]), row["label"].lower()))
        participants, matrix, participant_labels = participant_rows(poll["id"])
        comments = participant_comments(poll["id"])
        top_choice = recommendation(summary_sorted)
        admin_mode = is_admin_authenticated(poll)
        closed = is_deadline_passed(poll["deadline_at"])

        organizer_prefill_name = ""
        organizer_prefill_email = ""
        organizer_has_voted = False
        is_poll_owner = False
        voter_has_voted = False
        voter_identity_name = ""
        voter_identity_email = ""
        edit_vote_mode = request.args.get("edit") == "1"
        existing_choice_by_slot: dict[int, str] = {}
        existing_comment = ""

        if current_user is not None and poll["created_by_user_id"] is not None:
            is_poll_owner = int(poll["created_by_user_id"]) == int(current_user["id"])

        if is_poll_owner:
            admin_mode = True

        if is_poll_owner and current_user is not None:
            organizer_prefill_name = (poll["creator_name"] or "").strip()
            organizer_prefill_email = (current_user["email"] or "").strip().lower()
            if not organizer_prefill_name and organizer_prefill_email:
                organizer_prefill_name = organizer_prefill_email.split("@", 1)[0]

            db = get_db()
            organizer_has_voted = db.execute(
                "SELECT 1 FROM votes WHERE poll_id = ? AND participant_email = ? LIMIT 1",
                (poll["id"], organizer_prefill_email),
            ).fetchone() is not None

            if organizer_has_voted:
                organizer_rows = db.execute(
                    """
                    SELECT slot_id, choice, comment
                    FROM votes
                    WHERE poll_id = ? AND participant_email = ?
                    ORDER BY id ASC
                    """,
                    (poll["id"], organizer_prefill_email),
                ).fetchall()
                for organizer_row in organizer_rows:
                    existing_choice_by_slot[int(organizer_row["slot_id"])] = organizer_row["choice"]
                    if not existing_comment and (organizer_row["comment"] or "").strip():
                        existing_comment = (organizer_row["comment"] or "").strip()

        voter_state = session.get(voter_session_key(poll["id"]), {})
        if isinstance(voter_state, dict):
            voter_identity_name = (voter_state.get("name") or "").strip()[:80]
            voter_identity_email = (voter_state.get("email") or "").strip().lower()

        if voter_identity_email or voter_identity_name:
            db = get_db()
            if voter_identity_email:
                voter_rows = db.execute(
                    """
                    SELECT slot_id, choice, comment
                    FROM votes
                    WHERE poll_id = ? AND participant_email = ?
                    ORDER BY id ASC
                    """,
                    (poll["id"], voter_identity_email),
                ).fetchall()
            else:
                voter_rows = db.execute(
                    """
                    SELECT slot_id, choice, comment
                    FROM votes
                    WHERE poll_id = ?
                      AND COALESCE(participant_email, '') = ''
                      AND participant_name = ? COLLATE NOCASE
                    ORDER BY id ASC
                    """,
                    (poll["id"], voter_identity_name),
                ).fetchall()

            if voter_rows:
                voter_has_voted = True
                for voter_row in voter_rows:
                    existing_choice_by_slot[int(voter_row["slot_id"])] = voter_row["choice"]
                    if not existing_comment and (voter_row["comment"] or "").strip():
                        existing_comment = (voter_row["comment"] or "").strip()

        if not organizer_prefill_name and voter_identity_name:
            organizer_prefill_name = voter_identity_name
        if not organizer_prefill_email and voter_identity_email:
            organizer_prefill_email = voter_identity_email

        has_existing_vote = organizer_has_voted or voter_has_voted
        show_vote_form = (not has_existing_vote) or edit_vote_mode
        replace_vote_default_checked = has_existing_vote and edit_vote_mode
        voter_identity_label = voter_identity_email or voter_identity_name

        poll_response_mode = normalize_response_mode(poll["response_mode"])

        return render_template(
            "poll.html",
            poll=poll,
            slots=slots,
            summary=summary_sorted,
            participants=participants if admin_mode else [],
            participant_labels=participant_labels if admin_mode else {},
            matrix=matrix if admin_mode else {},
            comments=comments if admin_mode else {},
            poll_url=url_for("view_poll", token=token, _external=True),
            admin_mode=admin_mode,
            is_poll_owner=is_poll_owner,
            closed=closed,
            top_choice=top_choice,
            organizer_prefill_name=organizer_prefill_name,
            organizer_prefill_email=organizer_prefill_email,
            organizer_has_voted=organizer_has_voted,
            show_vote_form=show_vote_form,
            voter_has_voted=voter_has_voted,
            voter_identity_label=voter_identity_label,
            edit_vote_mode=edit_vote_mode,
            replace_vote_default_checked=replace_vote_default_checked,
            existing_choice_by_slot=existing_choice_by_slot,
            existing_comment=existing_comment,
            poll_response_mode=poll_response_mode,
            poll_response_mode_label=RESPONSE_MODE_LABELS.get(poll_response_mode, poll_response_mode),
        )

    @app.post("/poll/<token>/admin-login")
    def admin_login(token: str):
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("view_poll", token=token))

        poll = get_poll_by_token(token)
        if poll is None:
            flash("Sondage introuvable.", "error")
            return redirect(url_for("home"))

        code = request.form.get("organizer_code", "").strip()
        code_hash = poll["organizer_code_hash"] or ""
        if not code or not code_hash or not check_password_hash(code_hash, code):
            flash("Code organisateur incorrect.", "error")
            return redirect(url_for("view_poll", token=token))

        session[admin_session_key(poll["id"])] = True
        flash("Mode organisateur activé.", "success")
        return redirect(url_for("view_poll", token=token))

    @app.post("/poll/<token>/admin-logout")
    def admin_logout(token: str):
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("view_poll", token=token))

        poll = get_poll_by_token(token)
        if poll is None:
            flash("Sondage introuvable.", "error")
            return redirect(url_for("home"))

        session.pop(admin_session_key(poll["id"]), None)
        flash("Mode organisateur désactivé.", "success")
        return redirect(url_for("view_poll", token=token))

    @app.post("/poll/<token>/vote")
    def vote(token: str):
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("view_poll", token=token))

        poll = get_poll_by_token(token)
        if poll is None:
            flash("Sondage introuvable.", "error")
            return redirect(url_for("home"))

        if is_deadline_passed(poll["deadline_at"]):
            flash("Le sondage est clôturé (date limite dépassée).", "error")
            return redirect(url_for("view_poll", token=token))

        participant_name = request.form.get("participant_name", "").strip()
        participant_email = request.form.get("participant_email", "").strip().lower()
        comment = request.form.get("comment", "").strip()[:280]
        rgpd_vote_ok = request.form.get("rgpd_vote") == "on"
        replace_existing_vote = request.form.get("replace_existing_vote") == "on"

        if not rgpd_vote_ok:
            flash("Tu dois accepter la politique de confidentialité pour voter.", "error")
            return redirect(url_for("view_poll", token=token))

        if not participant_name:
            flash("Ton nom est obligatoire pour voter.", "error")
            return redirect(url_for("view_poll", token=token))
        participant_name = participant_name[:80]

        if participant_email and not EMAIL_REGEX.match(participant_email):
            flash("Email invalide.", "error")
            return redirect(url_for("view_poll", token=token))

        slots = get_poll_slots(poll["id"])
        if not slots:
            flash("Ce sondage ne contient aucun créneau.", "error")
            return redirect(url_for("view_poll", token=token))

        now = datetime.utcnow().isoformat(timespec="seconds")
        db = get_db()

        if participant_email:
            duplicate_vote = db.execute(
                """
                SELECT 1
                FROM votes
                WHERE poll_id = ? AND participant_email = ?
                LIMIT 1
                """,
                (poll["id"], participant_email),
            ).fetchone()
        else:
            duplicate_vote = db.execute(
                """
                SELECT 1
                FROM votes
                WHERE poll_id = ?
                  AND COALESCE(participant_email, '') = ''
                  AND participant_name = ? COLLATE NOCASE
                LIMIT 1
                """,
                (poll["id"], participant_name),
            ).fetchone()

        if duplicate_vote is not None:
            if not replace_existing_vote:
                flash("Vote déjà enregistré pour cette personne. Coche « Modifier mon vote » pour le remplacer.", "error")
                return redirect(url_for("view_poll", token=token))

            if participant_email:
                db.execute(
                    "DELETE FROM votes WHERE poll_id = ? AND participant_email = ?",
                    (poll["id"], participant_email),
                )
            else:
                db.execute(
                    """
                    DELETE FROM votes
                    WHERE poll_id = ?
                      AND COALESCE(participant_email, '') = ''
                      AND participant_name = ? COLLATE NOCASE
                    """,
                    (poll["id"], participant_name),
                )

        response_mode = normalize_response_mode(poll["response_mode"])

        allowed_slot_ids = {slot["id"] for slot in slots}
        selected_slot_id: int | None = None
        selected_multiple: set[int] = set()

        if response_mode == "single":
            selected_slot_raw = request.form.get("selected_slot", "").strip()
            if not selected_slot_raw.isdigit():
                flash("Choisis une option pour voter.", "error")
                return redirect(url_for("view_poll", token=token))
            selected_slot_id = int(selected_slot_raw)
            if selected_slot_id not in allowed_slot_ids:
                flash("Option de vote invalide.", "error")
                return redirect(url_for("view_poll", token=token))

        if response_mode == "multiple":
            selected_raw = request.form.getlist("selected_slots")
            for raw_id in selected_raw:
                if raw_id.isdigit():
                    slot_id = int(raw_id)
                    if slot_id in allowed_slot_ids:
                        selected_multiple.add(slot_id)

        for slot in slots:
            if response_mode == "single":
                if slot["id"] != selected_slot_id:
                    continue
                choice = "yes"
            else:
                if slot["id"] not in selected_multiple:
                    continue
                choice = "yes"

            db.execute(
                """
                INSERT INTO votes (poll_id, slot_id, participant_name, participant_email, choice, comment, rgpd_vote_consent_at, rgpd_consent_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (poll["id"], slot["id"], participant_name, participant_email, choice, comment, now, consent_version, now),
            )

        db.commit()
        session[voter_session_key(poll["id"])] = {
            "name": participant_name,
            "email": participant_email,
        }
        if duplicate_vote is not None and replace_existing_vote:
            flash("Ton vote a été mis à jour ✅", "success")
        else:
            flash("Ton vote a été enregistré ✅", "success")
        return redirect(url_for("view_poll", token=token))

    @app.post("/feedback")
    def feedback_submit():
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("home"))

        return_to = request.form.get("return_to", "").strip()
        safe_redirect = return_to if return_to.startswith("/") and not return_to.startswith("//") else url_for("home")

        component = request.form.get("feedback_component", "other").strip().lower()
        sender_name = request.form.get("feedback_name", "").strip()[:80]
        sender_email = request.form.get("feedback_email", "").strip().lower()
        message_text = request.form.get("feedback_message", "").strip()[:1200]
        page_url = request.form.get("feedback_page", "").strip()[:250]

        if component not in FEEDBACK_COMPONENTS:
            component = "other"

        if sender_email and not EMAIL_REGEX.match(sender_email):
            flash("Email feedback invalide.", "error")
            return redirect(safe_redirect)

        if len(message_text) < 8:
            flash("Décris un peu plus ton feedback (minimum 8 caractères).", "error")
            return redirect(safe_redirect)

        sent = send_feedback_email(
            component=component,
            message_text=message_text,
            sender_name=sender_name,
            sender_email=sender_email,
            page_url=page_url,
        )

        if sent:
            flash("Merci 🙌 Ton feedback a bien été envoyé.", "success")
        else:
            flash("Feedback non envoyé: vérifie la configuration SMTP/FEEDBACK_TO_EMAIL.", "error")

        return redirect(safe_redirect)

    @app.get("/poll/<token>/vote-status")
    def vote_status(token: str):
        poll = get_poll_by_token(token)
        if poll is None:
            return jsonify({"exists": False, "error": "poll_not_found"}), 404

        email = request.args.get("email", "").strip().lower()
        name = request.args.get("name", "").strip()

        db = get_db()
        exists = None

        if email and EMAIL_REGEX.match(email):
            exists = db.execute(
                "SELECT 1 FROM votes WHERE poll_id = ? AND participant_email = ? LIMIT 1",
                (poll["id"], email),
            ).fetchone()
        elif name:
            exists = db.execute(
                """
                SELECT 1
                FROM votes
                WHERE poll_id = ?
                  AND COALESCE(participant_email, '') = ''
                  AND participant_name = ? COLLATE NOCASE
                LIMIT 1
                """,
                (poll["id"], name),
            ).fetchone()

        return jsonify({"exists": exists is not None})

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("PORT", "5050"))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=debug)
