import json
import os
import secrets
from pathlib import Path


def home():
    return Path(os.environ.get("RUNBOARD_HOME", "~/.runboard")).expanduser()


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)


def get_or_create_token():
    p = home() / "token"
    if p.exists():
        t = p.read_text().strip()
        if t:
            return t
    t = secrets.token_urlsafe(24)
    _write_private(p, t)
    return t


def write_server_info(info):
    _write_private(home() / "server.json", json.dumps(info, indent=2))


def read_server_info():
    p = home() / "server.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
