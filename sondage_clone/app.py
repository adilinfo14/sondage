from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SONDAGE_DB_PATH", str(BASE_DIR / "sondage.db")))

ALLOWED_CHOICES = {"yes", "maybe", "no"}
ALLOWED_POLL_TYPES = {"meeting", "opinion"}


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SONDAGE_SECRET_KEY", "change-me-in-production")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SONDAGE_COOKIE_SECURE", "0") == "1"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

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
                poll_type TEXT NOT NULL DEFAULT 'meeting',
                deadline_at TEXT,
                organizer_code_hash TEXT,
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
                choice TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (poll_id) REFERENCES polls (id),
                FOREIGN KEY (slot_id) REFERENCES slots (id)
            );
            """
        )

        poll_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(polls)").fetchall()
        }
        if "poll_type" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN poll_type TEXT NOT NULL DEFAULT 'meeting'")
        if "deadline_at" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN deadline_at TEXT")
        if "organizer_code_hash" not in poll_columns:
            db.execute("ALTER TABLE polls ADD COLUMN organizer_code_hash TEXT")

        vote_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(votes)").fetchall()
        }
        if "comment" not in vote_columns:
            db.execute("ALTER TABLE votes ADD COLUMN comment TEXT")

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

    def is_admin_authenticated(poll: sqlite3.Row) -> bool:
        return bool(session.get(admin_session_key(poll["id"]), False))

    def recommendation(summary_rows: list[sqlite3.Row]) -> sqlite3.Row | None:
        if not summary_rows:
            return None
        return max(summary_rows, key=lambda row: (row["yes_count"], -row["no_count"], row["maybe_count"]))

    @app.context_processor
    def inject_csrf() -> dict:
        return {"csrf_token": _csrf_token}

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:;"
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
                COALESCE(SUM(CASE WHEN v.choice = 'maybe' THEN 1 ELSE 0 END), 0) AS maybe_count,
                COALESCE(SUM(CASE WHEN v.choice = 'no' THEN 1 ELSE 0 END), 0) AS no_count
            FROM slots s
            LEFT JOIN votes v ON v.slot_id = s.id
            WHERE s.poll_id = ?
            GROUP BY s.id, s.label, s.position
            ORDER BY s.position ASC, s.id ASC
            """,
            (poll_id,),
        ).fetchall()

    def participant_rows(poll_id: int) -> tuple[list[str], dict[str, dict[int, str]]]:
        db = get_db()
        rows = db.execute(
            """
            SELECT participant_name, slot_id, choice
            FROM votes
            WHERE poll_id = ?
            ORDER BY participant_name COLLATE NOCASE ASC
            """,
            (poll_id,),
        ).fetchall()

        participants: list[str] = []
        matrix: dict[str, dict[int, str]] = {}
        for row in rows:
            name = row["participant_name"]
            if name not in matrix:
                matrix[name] = {}
                participants.append(name)
            matrix[name][row["slot_id"]] = row["choice"]
        return participants, matrix

    def participant_comments(poll_id: int) -> dict[str, str]:
        db = get_db()
        rows = db.execute(
            """
            SELECT participant_name, MAX(COALESCE(comment, '')) AS comment
            FROM votes
            WHERE poll_id = ?
            GROUP BY participant_name
            """,
            (poll_id,),
        ).fetchall()
        return {row["participant_name"]: row["comment"] for row in rows if row["comment"]}

    @app.get("/")
    def home() -> str:
        return render_template("home.html")

    @app.get("/create")
    def create_poll_page():
        flash("Utilise le formulaire d'accueil pour créer un sondage.", "error")
        return redirect(url_for("home"))

    @app.post("/create")
    def create_poll():
        if not validate_csrf():
            flash("Session invalide. Recharge la page puis réessaie.", "error")
            return redirect(url_for("home"))

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        creator_name = request.form.get("creator_name", "").strip()
        poll_type = request.form.get("poll_type", "meeting").strip().lower()
        deadline_input = request.form.get("deadline_at", "").strip()
        organizer_code = request.form.get("organizer_code", "")
        raw_slots = request.form.get("slots", "")

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

        token = secrets.token_urlsafe(6)
        now = datetime.utcnow().isoformat(timespec="seconds")

        db = get_db()
        db.execute(
            """
            INSERT INTO polls (token, title, description, creator_name, poll_type, deadline_at, organizer_code_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token, title, description, creator_name, poll_type, deadline_at, organizer_code_hash, now),
        )
        poll_id = db.execute("SELECT id FROM polls WHERE token = ?", (token,)).fetchone()["id"]

        for position, label in enumerate(slots, start=1):
            db.execute(
                "INSERT INTO slots (poll_id, label, position) VALUES (?, ?, ?)",
                (poll_id, label, position),
            )

        db.commit()
        session[admin_session_key(poll_id)] = True
        flash("Sondage créé avec succès. Partage le lien !", "success")
        return redirect(url_for("view_poll", token=token))

    @app.get("/poll/<token>")
    def view_poll(token: str):
        poll = get_poll_by_token(token)
        if poll is None:
            flash("Sondage introuvable.", "error")
            return redirect(url_for("home"))

        slots = get_poll_slots(poll["id"])
        summary = aggregate_results(poll["id"])
        participants, matrix = participant_rows(poll["id"])
        comments = participant_comments(poll["id"])
        top_choice = recommendation(summary)
        admin_mode = is_admin_authenticated(poll)
        closed = is_deadline_passed(poll["deadline_at"])

        return render_template(
            "poll.html",
            poll=poll,
            slots=slots,
            summary=summary,
            participants=participants if admin_mode else [],
            matrix=matrix if admin_mode else {},
            comments=comments if admin_mode else {},
            poll_url=url_for("view_poll", token=token, _external=True),
            admin_mode=admin_mode,
            closed=closed,
            top_choice=top_choice,
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
        comment = request.form.get("comment", "").strip()[:280]
        if not participant_name:
            flash("Ton nom est obligatoire pour voter.", "error")
            return redirect(url_for("view_poll", token=token))
        participant_name = participant_name[:80]

        slots = get_poll_slots(poll["id"])
        if not slots:
            flash("Ce sondage ne contient aucun créneau.", "error")
            return redirect(url_for("view_poll", token=token))

        now = datetime.utcnow().isoformat(timespec="seconds")
        db = get_db()

        db.execute(
            "DELETE FROM votes WHERE poll_id = ? AND participant_name = ?",
            (poll["id"], participant_name),
        )

        for slot in slots:
            choice_key = f"choice_{slot['id']}"
            choice = request.form.get(choice_key, "no").strip().lower()
            if choice not in ALLOWED_CHOICES:
                choice = "no"

            db.execute(
                """
                INSERT INTO votes (poll_id, slot_id, participant_name, choice, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (poll["id"], slot["id"], participant_name, choice, comment, now),
            )

        db.commit()
        flash("Ton vote a été enregistré ✅", "success")
        return redirect(url_for("view_poll", token=token))

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("PORT", "5050"))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=debug)
