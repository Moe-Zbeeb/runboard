import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.request
from pathlib import Path

RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
URL_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")


def cache_dir():
    return Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "runboard"


def _asset():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if arch is None or system not in ("linux", "darwin"):
        raise RuntimeError(f"unsupported platform {system}/{machine}; install cloudflared manually")
    return f"cloudflared-{system}-{arch}" + (".tgz" if system == "darwin" else "")


def find_or_install():
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    target = cache_dir() / "cloudflared"
    if target.exists():
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    asset = _asset()
    print(f"[runboard] downloading {asset} ...", file=sys.stderr, flush=True)
    tmp = target.parent / (asset + ".part")
    urllib.request.urlretrieve(RELEASE + asset, tmp)
    if asset.endswith(".tgz"):
        with tarfile.open(tmp) as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith("cloudflared"))
            with tf.extractfile(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        tmp.unlink()
    else:
        tmp.replace(target)
    target.chmod(0o755)
    return str(target)


class Tunnel:
    def __init__(self, port, exe=None):
        self.port = port
        self.exe = exe or find_or_install()
        self.url = None
        self.proc = None
        self._ready = threading.Event()

    def start(self, timeout=60):
        self.proc = subprocess.Popen(
            [self.exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()
        self._ready.wait(timeout)
        if not self.url or self.proc.poll() is not None:
            self.stop()
            raise RuntimeError("cloudflared did not report a tunnel URL (is outbound HTTPS blocked?)")
        return self.url

    def _read(self):
        for line in self.proc.stderr:
            if self.url is None:
                m = URL_RE.search(line)
                if m:
                    self.url = m.group(0)
            elif "Registered tunnel connection" in line:
                self._ready.set()
        self._ready.set()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
