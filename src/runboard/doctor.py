import json
import os
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config as _config
from .storage import Storage

USER_AGENT = "runboard/0.2"
PROJECT = "runboard"
RUN_ID = "doctor"
OFFLINE_FIX = "this machine may not allow outbound HTTPS; set RUNBOARD_DIR to a shared directory and run `runboard serve` (see `runboard guide`)"


class Failure(Exception):
    def __init__(self, cause, fix):
        super().__init__(cause)
        self.cause = cause
        self.fix = fix


def _pick(options):
    for value, source in options:
        if value:
            return value, source
    return None, None


def resolve(server=None, token=None):
    info = _config.read_server_info()
    saved = str(_config.home() / "server.json")
    url, url_from = _pick([(server, "argument"), (os.environ.get("RUNBOARD_SERVER"), "RUNBOARD_SERVER"), (info.get("url"), saved)])
    secret, secret_from = _pick([(token, "argument"), (os.environ.get("RUNBOARD_TOKEN"), "RUNBOARD_TOKEN"), (info.get("token"), saved)])
    directory = os.environ.get("RUNBOARD_DIR")
    if directory and not server:
        return {"mode": "file", "dir": directory, "dir_from": "RUNBOARD_DIR", "partial": False}
    if url and secret:
        return {"mode": "http", "server": url.rstrip("/"), "server_from": url_from, "token": secret, "token_from": secret_from}
    return {"mode": "file", "dir": "./runboard-runs", "dir_from": "default", "partial": bool(url or secret)}


def _http_failure(server, code):
    if code == 401:
        return Failure(f"{server} rejected the token (HTTP 401)", "run `runboard configure <url>` with the RUNBOARD_TOKEN stored in the Worker, or correct RUNBOARD_TOKEN")
    if code == 403:
        return Failure(f"request to {server} was blocked before reaching Runboard (HTTP 403)", "a proxy or Cloudflare security rule is blocking this machine; check proxy settings or allow it in Cloudflare")
    if code == 404:
        return Failure(f"{server} has no Runboard API (HTTP 404)", "check the URL; it should be the Worker or `runboard serve` address")
    if code == 413:
        return Failure(f"{server} rejected a minimal request as too large (HTTP 413)", "a proxy between this machine and the server limits request size")
    return Failure(f"{server} returned HTTP {code}", "check the Worker logs with `npx wrangler tail`, or the `runboard serve` output")


def _network_failure(server, reason):
    if isinstance(reason, ssl.SSLError):
        return Failure(f"TLS handshake with {server} failed ({reason})", "set SSL_CERT_FILE to your cluster's CA bundle, or ask your admin about HTTPS interception")
    if isinstance(reason, socket.gaierror):
        return Failure(f"cannot resolve the host in {server}", "check the URL; if it is correct, " + OFFLINE_FIX)
    if isinstance(reason, ConnectionRefusedError):
        return Failure(f"connection to {server} was refused", "check the URL and port; for a local server, make sure `runboard serve` is running")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return Failure(f"timed out connecting to {server}", OFFLINE_FIX)
    return Failure(f"cannot reach {server} ({reason})", OFFLINE_FIX)


def _request(server, token, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server + path, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise _http_failure(server, e.code) from None
    except urllib.error.URLError as e:
        raise _network_failure(server, e.reason) from None
    except (socket.timeout, TimeoutError) as e:
        raise _network_failure(server, e) from None
    except ValueError as e:
        raise Failure(f"{server} is not a valid Runboard URL or did not return JSON ({e})", "use the full https:// URL of the Worker or `runboard serve`") from None
    except OSError as e:
        raise _network_failure(server, e) from None


def _rows(sid, count=3):
    now = time.time()
    return [{"doctor/check": float(index), "_step": index, "_time": now, "_seq": index, "_sid": sid} for index in range(count)]


def _check_http(server, token):
    status, _ = _request(server, token, "/api/health")
    yield "health", f"ok (HTTP {status})"
    sid = secrets.token_hex(8)
    _request(server, token, f"/api/runs/{PROJECT}/{RUN_ID}", {"meta": {"name": "doctor", "status": "finished"}, "rows": _rows(sid)})
    found, offset = 0, 0
    for _ in range(1000):
        _, page = _request(server, token, f"/api/metrics?project={PROJECT}&run={RUN_ID}&offset={offset}")
        rows = page.get("rows") or []
        found += sum(1 for row in rows if isinstance(row, dict) and row.get("_sid") == sid)
        if not rows or page.get("offset") == offset:
            break
        offset = page.get("offset", offset)
    if found != 3:
        raise Failure(f"wrote 3 rows to {PROJECT}/{RUN_ID} but read back {found}", "the server accepted the write but did not store it; check the Worker's D1 binding or the server's --dir")
    yield "write/read", f"ok (3 rows written to {PROJECT}/{RUN_ID} and read back)"


def _check_file(directory):
    root = Path(directory).expanduser().resolve()
    sid = secrets.token_hex(8)
    try:
        storage = Storage(root)
        storage.update_meta(PROJECT, RUN_ID, {"name": "doctor", "status": "finished"})
        storage.append_rows(PROJECT, RUN_ID, _rows(sid))
        found, offset = 0, 0
        while True:
            rows, new_offset = storage.read_rows(PROJECT, RUN_ID, offset)
            found += sum(1 for row in rows if row.get("_sid") == sid)
            if new_offset == offset:
                break
            offset = new_offset
    except OSError as e:
        raise Failure(f"cannot write to {root} ({e})", "set RUNBOARD_DIR to a writable directory, ideally on storage shared with the dashboard server") from None
    if found != 3:
        raise Failure(f"wrote 3 rows to {root} but read back {found}", "check that the directory is not on a read-only or eventually consistent filesystem")
    yield "write/read", f"ok (3 rows written to {root}/{PROJECT}/{RUN_ID} and read back)"


def doctor(server=None, token=None, out=None):
    out = out or sys.stdout

    def line(label, text):
        print(f"  {label + ':':12} {text}", file=out)

    print("runboard doctor", file=out)
    target = resolve(server, token)
    try:
        if target["mode"] == "http":
            line("destination", f"{target['server']} (from {target['server_from']})")
            line("token", f"set (from {target['token_from']})")
            checks = _check_http(target["server"], target["token"])
        else:
            if target["partial"]:
                raise Failure("a server URL or a token is configured, but not both", "set both RUNBOARD_SERVER and RUNBOARD_TOKEN, or run `runboard configure <url>`")
            line("destination", f"local files in {Path(target['dir']).expanduser().resolve()} (from {target['dir_from']})")
            checks = _check_file(target["dir"])
        for label, text in checks:
            line(label, text)
    except Failure as failure:
        print(f"FAIL: {failure.cause}", file=out)
        print(f"fix:  {failure.fix}", file=out)
        return 1
    if target["mode"] == "file" and target["dir_from"] == "default":
        print("note: no server is configured, so runs are written to ./runboard-runs; run `runboard configure <url>` to use Cloudflare", file=out)
    print("PASS", file=out)
    return 0
