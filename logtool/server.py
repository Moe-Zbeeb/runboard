import hmac
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .storage import Storage

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024 * 1024
COOKIE = "logtool_token"


def make_handler(storage, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "logtool"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _query(self):
            return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

        def _supplied_token(self):
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:].strip()
            q = self._query().get("token")
            if q:
                return q
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return ""

        def _authorized(self):
            return hmac.compare_digest(self._supplied_token().encode(), token.encode())

        def _send(self, code, body=b"", ctype="application/json", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _deny(self):
            if urlparse(self.path).path.startswith("/api/"):
                self._send(401, {"error": "unauthorized"})
            else:
                self._send(
                    401,
                    "<h3>logtool: unauthorized</h3><p>Open the URL printed by <code>logtool serve</code> "
                    "(it ends with <code>?token=...</code>).</p>",
                    "text/html; charset=utf-8",
                )

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._authorized():
                return self._deny()
            url = urlparse(self.path)
            path = url.path
            if path == "/" and "token" in self._query():
                return self._send(
                    302,
                    headers={
                        "Location": "/",
                        "Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000",
                    },
                )
            if path == "/":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(unquote(path[len("/static/"):]))
            if path == "/api/runs":
                return self._send(200, storage.list_runs())
            if path == "/api/metrics":
                q = self._query()
                try:
                    offset = max(0, int(q.get("offset", 0)))
                    rows, new_offset = storage.read_rows(q.get("project", ""), q.get("run", ""), offset)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, {"rows": rows, "offset": new_offset})
            if path == "/api/health":
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                return self._deny()
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "runs"]:
                return self._send(404, {"error": "not found"})
            project, run_id = unquote(parts[2]), unquote(parts[3])
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
                if isinstance(body.get("meta"), dict):
                    storage.update_meta(project, run_id, body["meta"])
                rows = body.get("rows") or []
                if rows:
                    storage.append_rows(project, run_id, rows)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            self._send(200, {"ok": True, "n": len(rows)})

        def _static(self, name):
            p = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in p.parents or not p.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, p.read_bytes(), ctype)

    return Handler


class Server:
    def __init__(self, root, token, host="0.0.0.0", port=8080):
        self.storage = Storage(root)
        self.token = token
        self.httpd = ThreadingHTTPServer((host, port), make_handler(self.storage, token))
        self.httpd.daemon_threads = True
        self._thread = None

    @property
    def port(self):
        return self.httpd.server_address[1]

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self):
        self.httpd.serve_forever()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
