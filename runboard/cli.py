import argparse
import signal
import socket
import sys
import time
import urllib.request
from datetime import datetime

from . import __version__, config
from .client import sync
from .server import Server
from .storage import Storage


def _notify(url, text):
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=text.encode(), method="POST"), timeout=10).close()
    except OSError as e:
        print(f"[runboard] notify failed: {e}", file=sys.stderr)


def _reachable_host():
    name = socket.gethostname()
    try:
        socket.getaddrinfo(name, None)
        return name
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def cmd_serve(args):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    token = config.get_or_create_token()
    try:
        srv = Server(args.dir, token, host=args.host, port=args.port)
    except OSError as e:
        if args.port_explicit:
            raise SystemExit(f"runboard: cannot bind port {args.port}: {e}")
        srv = Server(args.dir, token, host=args.host, port=0)
        print(f"[runboard] port {args.port} busy; using {srv.port}", file=sys.stderr)
    srv.start()
    advertise = args.advertise or f"http://{_reachable_host()}:{srv.port}"
    config.write_server_info({"url": advertise, "token": token, "dir": str(srv.storage.root)})
    print(f"runboard serving {srv.storage.root}")
    print(f"  local:   http://127.0.0.1:{srv.port}/?token={token}")
    print(f"  cluster: {advertise}/?token={token}")
    print(f"  (training jobs pick up the server from {config.home() / 'server.json'})")
    sys.stdout.flush()

    tunnel = None

    def open_tunnel():
        from .tunnel import Tunnel

        t = Tunnel(srv.port)
        url = t.start()
        public = f"{url}/?token={token}"
        print(f"  public:  {public}", flush=True)
        print("  (the public URL can take ~30s to start resolving)", flush=True)
        info = config.read_server_info()
        info["public_url"] = public
        config.write_server_info(info)
        if args.notify:
            _notify(args.notify, f"runboard dashboard: {public}")
        return t

    try:
        if args.tunnel:
            tunnel = open_tunnel()
        while True:
            time.sleep(5)
            if tunnel and tunnel.proc.poll() is not None:
                print("[runboard] tunnel exited; restarting", file=sys.stderr, flush=True)
                try:
                    tunnel = open_tunnel()
                except RuntimeError as e:
                    print(f"[runboard] {e}; retrying in 30s", file=sys.stderr, flush=True)
                    time.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.stop()
        srv.stop()


def cmd_ls(args):
    runs = Storage(args.dir).list_runs()
    for r in runs:
        upd = datetime.fromtimestamp(r["updated"]).strftime("%Y-%m-%d %H:%M") if r.get("updated") else "-"
        print(f"{r['project']:20} {r['run_id']:28} {str(r.get('status', '')):10} {upd}  {r.get('name', '')}")


def cmd_sync(args):
    n = sync(server=args.server, token=args.token, paths=args.files or None)
    print(f"uploaded {n} rows")


def cmd_url(args):
    info = config.read_server_info()
    token = config.get_or_create_token()
    print(info.get("public_url") or f"{info.get('url', 'http://127.0.0.1:8080')}/?token={token}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="runboard", description="Live training dashboards for any cluster.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the dashboard server")
    s.add_argument("--dir", default="./runboard-runs", help="directory where runs are stored")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--tunnel", action="store_true", help="expose publicly via a Cloudflare quick tunnel")
    s.add_argument("--advertise", help="URL training jobs use to reach this server")
    s.add_argument("--notify", help="POST the public URL to this webhook (e.g. https://ntfy.sh/<topic>)")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("ls", help="list runs")
    s.add_argument("--dir", default="./runboard-runs")
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("sync", help="upload spooled offline metrics")
    s.add_argument("files", nargs="*")
    s.add_argument("--server")
    s.add_argument("--token")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("url", help="print the dashboard URL")
    s.set_defaults(func=cmd_url)

    p.add_argument("--version", action="version", version=f"runboard {__version__}")
    args = p.parse_args(argv)
    if getattr(args, "cmd", None) == "serve":
        args.port_explicit = args.port is not None
        args.port = args.port or 8080
    args.func(args)


if __name__ == "__main__":
    main()
