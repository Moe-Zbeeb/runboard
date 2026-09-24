import urllib.error
import urllib.request

import pytest

from helpers import http_get


def test_server_requires_token(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        http_get(server, "/api/runs", token=None)
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError):
        http_get(server, "/api/runs", token="wrong")
    assert http_get(server, "/api/runs")[0] == 200


def test_server_cookie_login(server):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as e:
        opener.open(f"http://127.0.0.1:{server.port}/?token=secret", timeout=5)
    assert e.value.code == 302
    cookie = e.value.headers["Set-Cookie"].split(";")[0]
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/", headers={"Cookie": cookie})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert b"<html" in r.read().lower()
