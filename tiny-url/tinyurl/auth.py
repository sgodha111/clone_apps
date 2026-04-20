from functools import wraps

from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from tinyurl.db import get_connection, row_to_dict


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="tinyurl-auth")


def create_token(user_id: int) -> str:
    return _serializer().dumps({"user_id": user_id})


def load_user_from_token(token: str):
    max_age = current_app.config["TOKEN_MAX_AGE_SECONDS"]
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

    with get_connection(current_app.config["DATABASE"]) as connection:
        row = connection.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?",
            (data.get("user_id"),),
        ).fetchone()
    return row_to_dict(row)


def current_user_optional():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ").strip()
    if not token:
        return None
    return load_user_from_token(token)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        g.current_user = current_user_optional()
        if g.current_user is None:
            return jsonify({"error": "Authentication required"}), 401
        return view(*args, **kwargs)

    return wrapped
