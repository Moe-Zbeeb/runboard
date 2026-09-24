import time
import urllib.request


def http_get(srv, path, token="secret"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read()


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()
