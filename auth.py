import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt

_SECRET = os.environ.get("JWT_SECRET", "dev-secret-not-for-production")
_ALGORITHM = "HS256"
_EXPIRE_HOURS = 24

_USERS_FILE = Path(__file__).parent / "users.json"


def _load_users() -> dict:
    if not _USERS_FILE.exists():
        return {}
    return json.loads(_USERS_FILE.read_text())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def authenticate(password: str) -> str | None:
    """Returns username if password matches any user, else None."""
    for username, hashed in _load_users().items():
        if bcrypt.checkpw(password.encode(), hashed.encode()):
            return username
    return None


def create_token(username: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": exp}, _SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> str:
    """Returns username, or raises jwt.PyJWTError if invalid/expired."""
    payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    return payload["sub"]
