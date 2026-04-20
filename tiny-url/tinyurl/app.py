import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, current_app, g, jsonify, redirect, render_template, request, url_for
from sqlite3 import IntegrityError

from tinyurl.auth import (
    create_token,
    current_user_optional,
    hash_password,
    login_required,
    verify_password,
)
from tinyurl.base62 import ALPHABET, encode_base62, is_valid_key
from tinyurl.cache import LRUCache
from tinyurl.db import get_connection, init_db, row_to_dict


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE=os.environ.get("DATABASE", os.path.abspath("instance/tinyurl.sqlite3")),
        BASE_URL=os.environ.get("BASE_URL", "http://127.0.0.1:5000"),
        TOKEN_MAX_AGE_SECONDS=int(os.environ.get("TOKEN_MAX_AGE_SECONDS", "86400")),
        CACHE_SIZE=int(os.environ.get("CACHE_SIZE", "1024")),
    )
    if test_config:
        app.config.update(test_config)

    init_db(app.config["DATABASE"])
    app.url_cache = LRUCache(app.config["CACHE_SIZE"])  # type: ignore[attr-defined]

    @app.before_request
    def attach_user():
        g.current_user = current_user_optional()

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/api/auth/register")
    def register():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        if not email or "@" not in email:
            return jsonify({"error": "A valid email is required"}), 400
        if len(password) < 8:
            return jsonify({"error": "Password must be at least 8 characters"}), 400

        try:
            with get_connection(current_app.config["DATABASE"]) as connection:
                cursor = connection.execute(
                    "INSERT INTO users(email, password_hash) VALUES (?, ?)",
                    (email, hash_password(password)),
                )
                user_id = cursor.lastrowid
        except IntegrityError:
            return jsonify({"error": "Email is already registered"}), 409

        return jsonify({"access_token": create_token(user_id), "user": {"id": user_id, "email": email}}), 201

    @app.post("/api/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""

        with get_connection(current_app.config["DATABASE"]) as connection:
            row = connection.execute(
                "SELECT id, email, password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()

        if row is None or not verify_password(row["password_hash"], password):
            return jsonify({"error": "Invalid email or password"}), 401

        return jsonify({"access_token": create_token(row["id"]), "user": {"id": row["id"], "email": row["email"]}})

    @app.post("/api/shorten")
    def shorten_url():
        data = request.get_json(silent=True) or {}
        long_url = (data.get("long_url") or "").strip()
        custom_alias = (data.get("custom_alias") or "").strip() or None
        expires_at = (data.get("expires_at") or "").strip() or None
        user = g.current_user

        validation_error = validate_long_url(long_url)
        if validation_error:
            return jsonify({"error": validation_error}), 400
        if custom_alias and not validate_alias(custom_alias):
            return jsonify({"error": f"Custom alias can only contain {ALPHABET} and must be 3-32 characters"}), 400
        if expires_at and not parse_iso_datetime(expires_at):
            return jsonify({"error": "expires_at must be a valid ISO-8601 datetime"}), 400

        with get_connection(current_app.config["DATABASE"]) as connection:
            existing = find_existing_url(connection, long_url, user)
            if existing and not custom_alias:
                return jsonify(url_payload(existing)), 200

            try:
                row = create_url_mapping(connection, long_url, custom_alias, user, expires_at)
            except IntegrityError:
                return jsonify({"error": "Custom alias is already in use"}), 409

        current_app.url_cache.set(row["short_key"], row["long_url"])  # type: ignore[attr-defined]
        return jsonify(url_payload(row)), 201

    @app.get("/api/urls")
    @login_required
    def list_urls():
        with get_connection(current_app.config["DATABASE"]) as connection:
            rows = connection.execute(
                """
                SELECT id, short_key, long_url, user_id, clicks, expires_at, created_at, updated_at
                FROM urls
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (g.current_user["id"],),
            ).fetchall()
        return jsonify({"urls": [url_payload(row_to_dict(row)) for row in rows]})

    @app.get("/api/url/<short_key>")
    def get_url(short_key):
        row = lookup_url(short_key)
        if row is None:
            return jsonify({"error": "Short URL not found"}), 404
        return jsonify(url_payload(row))

    @app.delete("/api/url/<short_key>")
    @login_required
    def delete_url(short_key):
        with get_connection(current_app.config["DATABASE"]) as connection:
            cursor = connection.execute(
                "DELETE FROM urls WHERE short_key = ? AND user_id = ?",
                (short_key, g.current_user["id"]),
            )
        if cursor.rowcount == 0:
            return jsonify({"error": "Short URL not found for this user"}), 404
        current_app.url_cache.delete(short_key)  # type: ignore[attr-defined]
        return "", 204

    @app.get("/<short_key>")
    def follow_short_url(short_key):
        if not is_valid_key(short_key):
            return jsonify({"error": "Invalid short key"}), 404

        cached = current_app.url_cache.get(short_key)  # type: ignore[attr-defined]
        if cached:
            increment_clicks(short_key)
            return redirect(cached, code=302)

        row = lookup_url(short_key)
        if row is None:
            return jsonify({"error": "Short URL not found"}), 404
        if is_expired(row):
            return jsonify({"error": "Short URL has expired"}), 410

        current_app.url_cache.set(short_key, row["long_url"])  # type: ignore[attr-defined]
        increment_clicks(short_key)
        return redirect(row["long_url"], code=302)

    return app


def validate_long_url(long_url: str) -> str | None:
    parsed = urlparse(long_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "long_url must be a valid http or https URL"
    return None


def validate_alias(alias: str) -> bool:
    return 3 <= len(alias) <= 32 and is_valid_key(alias)


def parse_iso_datetime(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_expired(row: dict) -> bool:
    if not row.get("expires_at"):
        return False
    expires_at = parse_iso_datetime(row["expires_at"])
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def find_existing_url(connection, long_url: str, user: dict | None):
    if user:
        row = connection.execute(
            """
            SELECT id, short_key, long_url, user_id, clicks, expires_at, created_at, updated_at
            FROM urls
            WHERE long_url = ? AND user_id = ?
            """,
            (long_url, user["id"]),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT id, short_key, long_url, user_id, clicks, expires_at, created_at, updated_at
            FROM urls
            WHERE long_url = ? AND user_id IS NULL
            """,
            (long_url,),
        ).fetchone()
    return row_to_dict(row)


def create_url_mapping(connection, long_url: str, custom_alias: str | None, user: dict | None, expires_at: str | None):
    cursor = connection.execute(
        "INSERT INTO urls(short_key, long_url, user_id, expires_at) VALUES (?, ?, ?, ?)",
        (custom_alias, long_url, user["id"] if user else None, expires_at),
    )
    row_id = cursor.lastrowid
    short_key = custom_alias or encode_base62(row_id)
    connection.execute("UPDATE urls SET short_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (short_key, row_id))
    row = connection.execute(
        """
        SELECT id, short_key, long_url, user_id, clicks, expires_at, created_at, updated_at
        FROM urls
        WHERE id = ?
        """,
        (row_id,),
    ).fetchone()
    return row_to_dict(row)


def lookup_url(short_key: str):
    with get_connection(current_app.config["DATABASE"]) as connection:
        row = connection.execute(
            """
            SELECT id, short_key, long_url, user_id, clicks, expires_at, created_at, updated_at
            FROM urls
            WHERE short_key = ?
            """,
            (short_key,),
        ).fetchone()
    return row_to_dict(row)


def increment_clicks(short_key: str) -> None:
    with get_connection(current_app.config["DATABASE"]) as connection:
        connection.execute(
            "UPDATE urls SET clicks = clicks + 1, updated_at = CURRENT_TIMESTAMP WHERE short_key = ?",
            (short_key,),
        )


def url_payload(row: dict) -> dict:
    short_url = url_for("follow_short_url", short_key=row["short_key"], _external=True)
    return {
        "id": row["id"],
        "short_key": row["short_key"],
        "short_url": short_url,
        "long_url": row["long_url"],
        "user_id": row["user_id"],
        "clicks": row["clicks"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
