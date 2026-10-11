import json
import os
import secrets
from pathlib import Path


def home():
    return Path(os.environ.get("RUNBOARD_HOME", "~/.runboard")).expanduser()


def normalize_url(url):
    url = str(url).strip()
    if "://" not in url:
        url = "https://" + url
    return url.rstrip("/")


def write_private(path, text):
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
    write_private(p, t)
    return t


def write_server_info(info):
    write_private(home() / "server.json", json.dumps(info, indent=2))


def paired_token(server):
    target = normalize_url(server)
    env_server, env_token = os.environ.get("RUNBOARD_SERVER"), os.environ.get("RUNBOARD_TOKEN")
    if env_server and env_token and normalize_url(env_server) == target:
        return env_token, "RUNBOARD_TOKEN"
    info = read_server_info()
    if info.get("url") and info.get("token") and normalize_url(info["url"]) == target:
        return info["token"], str(home() / "server.json")
    return None, None


def read_server_info():
    p = home() / "server.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
