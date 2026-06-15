#!/usr/bin/env python3
"""
Manage users.json — add, list, or remove users.

Usage:
  python3 manage_users.py add <username> <password>
  python3 manage_users.py remove <username>
  python3 manage_users.py list
"""
import json
import sys
from pathlib import Path

import bcrypt

USERS_FILE = Path(__file__).parent / "users.json"


def _load():
    return json.loads(USERS_FILE.read_text()) if USERS_FILE.exists() else {}


def _save(users):
    USERS_FILE.write_text(json.dumps(users, indent=2) + "\n")


def add(username, password):
    users = _load()
    users[username] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    _save(users)
    print(f"added: {username}")


def remove(username):
    users = _load()
    if username not in users:
        print(f"not found: {username}")
        return
    del users[username]
    _save(users)
    print(f"removed: {username}")


def list_users():
    users = _load()
    if not users:
        print("no users")
    for u in users:
        print(u)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd, *rest = args
    if cmd == "add" and len(rest) == 2:
        add(*rest)
    elif cmd == "remove" and len(rest) == 1:
        remove(rest[0])
    elif cmd == "list":
        list_users()
    else:
        print(__doc__)
        sys.exit(1)
