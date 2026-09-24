import json
import os
import secrets
from pathlib import Path


def home():
    return Path(os.environ.get("RUNBOARD_HOME", "~/.runboard")).expanduser()


def get_or_create_token():
    p = home() / "token"
    if p.exists():
        t = p.read_text().strip()
        if t:
            return t
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    p.write_text(t)
    os.chmod(p, 0o600)
    return t


def write_server_info(info):
    p = home() / "server.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(info, indent=2))
    os.chmod(p, 0o600)


def read_server_info():
    p = home() / "server.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
