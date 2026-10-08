"""HTTP server: the page, the catalog API, notebook writes and the figure files.

It listens on loopback only.  Two kinds of request are served:

* **local** — a browser on this Mac: ``Host`` is a loopback name and no
  Tailscale identity header is present;
* **tailnet** — a device reaching this Mac through ``tailscale serve``
  (``https://<node>.ts.net:8766`` -> ``http://127.0.0.1:8766``): Tailscale adds
  ``Tailscale-User-Login``, which must be this node's owner.

Anything else (a foreign ``Host`` from DNS rebinding, another tailnet user, a
cross-site POST) gets 403.
"""
from __future__ import annotations

import gzip
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, electro_gui
from .notebook import host_tag
from .settings import load_settings

STATIC = Path(__file__).with_name("static")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".json": "application/json"}
_STATIC_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_BODY = 256 * 1024
TAILSCALE_CANDIDATES = ("tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
                        "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


class Tailnet:
    """This node's MagicDNS name and owner login, from ``tailscale status --json``.

    Read lazily and retried every 30 s until found, so a server started at boot
    before Tailscale is up still learns them.  ``owner``/``names`` given on the
    command line win.
    """

    def __init__(self, owner=None, names=()):
        self._fixed_owner = owner
        self._extra = {n.lower().rstrip(".") for n in names if n}
        self._owner = owner
        self._dns = None
        self._checked = None  # time.monotonic() may start near 0 at process start, so no 0.0 sentinel
        self._lock = threading.Lock()

    def _read(self):
        exe = next(filter(None, (shutil.which(c) for c in TAILSCALE_CANDIDATES)), None)
        if not exe:
            print("tailnet: no tailscale CLI found; tailnet requests are refused", file=sys.stderr, flush=True)
            return None
        try:
            done = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5,
                                  stdin=subprocess.DEVNULL)
            data = json.loads(done.stdout)
            me = data["Self"]
            return me.get("DNSName", "").rstrip(".").lower() or None, data["User"][str(me["UserID"])]["LoginName"]
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            detail = locals().get("done")
            extra = f" (exit {detail.returncode}: {detail.stderr.strip()[:200] or detail.stdout.strip()[:200]})" if detail else ""
            print(f"tailnet: `{exe} status --json` failed: {exc!r}{extra}", file=sys.stderr, flush=True)
            return None

    def refresh(self, force=False):
        with self._lock:
            if self._dns and self._owner and not force:
                return
            if not force and self._checked is not None and time.monotonic() - self._checked < 30:
                return
            self._checked = time.monotonic()
            found = self._read()
            if found:
                self._dns = found[0]
                self._owner = self._fixed_owner or found[1]

    @property
    def owner(self):
        self.refresh()
        return self._owner

    @property
    def names(self):
        self.refresh()
        return self._extra | ({self._dns} if self._dns else set())


class App:
    def __init__(self, catalog, notebooks, data_dir, configs_root, tailnet, electro_gui_url=None,
                 electro_gui_remote_url=None):
        self.catalog = catalog
        self.notebooks = notebooks
        self.data_dir = Path(data_dir)
        self.configs_root = Path(configs_root)
        self.tailnet = tailnet
        self.eg_override = {"local": electro_gui_url, "tailnet": electro_gui_remote_url}
        self.started = time.time()
        self._probe = {}

    def settings(self):
        return load_settings(self.data_dir)

    def electro_gui_url(self, where, settings=None):
        settings = settings or self.settings()
        override = self.eg_override.get(where)
        if override is not None:
            return override
        return settings["electro_gui"]["url" if where == "local" else "remote_url"]

    def electro_gui_running(self, url):
        now = time.monotonic()
        hit = self._probe.get(url)
        if hit and now - hit[0] < 5:
            return hit[1]
        running = electro_gui.probe(url)
        self._probe[url] = (now, running)
        return running

    def snapshot(self, sid, unit_id):
        """What the catalog says about a unit right now; None when the session is not mounted."""
        session = self.catalog.session(sid)
        if session is None:
            return None
        unit = next((u for u in session["units"] if u["id"] == unit_id), None)
        if unit is None:
            raise LookupError(f"unit {unit_id} is not in {sid}")
        return {"quality": unit["quality"], "area": unit["area"], "peak_channel": unit["peak_channel"],
                "depth_um": unit["depth_um"], "figure": unit["figures"]["raster"]["name"]}


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"result-viewer/{__version__}"
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------ plumbing
        def log_request(self, code="-", size="-"):
            try:
                quiet = int(code) < 400 and self.command != "POST"
            except (TypeError, ValueError):
                quiet = False
            if not quiet:
                super().log_request(code, size)

        def _send(self, status, body, ctype, headers=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            if (len(body) > 1024 and ctype.startswith(("application/json", "text/"))
                    and "gzip" in self.headers.get("Accept-Encoding", "")):
                body = gzip.compress(body, compresslevel=5)
                headers = dict(headers or {}, **{"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                       "application/json; charset=utf-8", {"Cache-Control": "no-store"})

        def _where(self):
            """``"local"`` or ``"tailnet"``; raises HTTPError(403) for anything else."""
            host = (urlsplit("//" + self.headers.get("Host", "")).hostname or "").lower()
            login = self.headers.get("Tailscale-User-Login")
            if host in LOOPBACK and login is None:
                return "local"
            if host not in LOOPBACK and host not in app.tailnet.names:
                raise HTTPError(403, "Unknown Host header.")
            owner = app.tailnet.owner
            if not owner or login is None or not hmac.compare_digest(login.encode(), owner.encode()):
                raise HTTPError(403, "Only this tailnet node's owner may use the viewer.")
            return "tailnet"

        def _check_post(self, where):
            origin = self.headers.get("Origin")
            if origin:
                allowed = LOOPBACK if where == "local" else (LOOPBACK | app.tailnet.names)
                if (urlsplit(origin).hostname or "").lower() not in allowed:
                    raise HTTPError(403, "Cross-site request refused.")
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise HTTPError(415, "Send JSON.")

        def _body(self):
            if self._raw is None:
                raise HTTPError(413, "Request too large.")
            try:
                data = json.loads(self._raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                raise HTTPError(400, "Body is not JSON.")
            if not isinstance(data, dict):
                raise HTTPError(400, "Body must be a JSON object.")
            return data

        def _dispatch(self, method):
            try:
                where = self._where()
                url = urlsplit(self.path)
                query = {k: v[-1] for k, v in parse_qs(url.query).items()}
                if method == "POST":
                    self._check_post(where)
                    if url.path == "/api/note":
                        return self._json(self.post_note(self._body()))
                    raise HTTPError(404, "Not found.")
                path = url.path
                if path in ("/", "/index.html"):
                    return self.static("index.html")
                if path.startswith("/static/"):
                    return self.static(path[len("/static/"):])
                if path.startswith("/fig/"):
                    return self.figure(path[len("/fig/"):])
                route = {"/api/health": self.health, "/api/config": self.config, "/api/sessions": self.sessions,
                         "/api/session": self.session, "/api/notebook": self.notebook,
                         "/api/electro_gui": self.electro_gui_status}.get(path)
                if route is None:
                    raise HTTPError(404, "Not found.")
                return self._json(route(query, where))
            except HTTPError as exc:
                self._json({"error": str(exc)}, exc.status)
            except (ValueError, LookupError) as exc:
                self._json({"error": str(exc)}, 400 if isinstance(exc, ValueError) else 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # keep serving; the log has the traceback
                self.log_error("%s %s failed: %r", method, self.path, exc)
                import traceback
                traceback.print_exc(file=sys.stderr)
                try:
                    self._json({"error": f"server error: {exc}"}, 500)
                except OSError:
                    pass

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            # Read the body before any check: a refused request must not leave it on a keep-alive connection.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if 0 <= length <= MAX_BODY:
                self._raw = self.rfile.read(length)
            else:
                self._raw = None
                self.close_connection = True
            self._dispatch("POST")

        # -------------------------------------------------------------- files
        def static(self, name):
            if not _STATIC_NAME.match(name):
                raise HTTPError(404, "Not found.")
            path = STATIC / name
            if not path.is_file():
                raise HTTPError(404, "Not found.")
            self._send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                       {"Cache-Control": "no-cache"})

        def figure(self, rest):
            index, _, rel = unquote(rest).partition("/")
            path = app.catalog.resolve_figure(index, rel)
            if path is None:
                raise HTTPError(404, "No such figure.")
            st = path.stat()
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            headers = {"ETag": etag, "Cache-Control": "private, max-age=300"}
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"), headers)

        # ---------------------------------------------------------------- API
        def health(self, query, where):
            return {"ok": True, "version": __version__, "pid": os.getpid(), "started": app.started}

        def config(self, query, where):
            settings = app.settings()
            url = app.electro_gui_url(where, settings)
            return {"version": __version__, "host": host_tag(), "where": where, "tags": settings["tags"],
                    "electro_gui": {"url": url,
                                    "launch": "python -m electro_gui --config-dir output/presentation "
                                              f"--config-dir '{app.configs_root}'"},
                    "roots": [str(r) for r in app.catalog.roots],
                    "paths": {"data": str(app.data_dir), "notes": str(app.notebooks.notes_dir),
                              "configs": str(app.configs_root)}}

        def sessions(self, query, where):
            noted = app.notebooks.summary()
            out = []
            for sid in app.catalog.sessions():
                session = app.catalog.session(sid)
                if session is not None:
                    out.append({"id": sid, "root": session["root"], "n_units": len(session["units"]),
                                **noted.get(sid, {"noted": 0, "flagged": 0})})
            return {"sessions": out}

        def _sid(self, query):
            sid = query.get("sid", "")
            app.notebooks.path(sid)  # validates
            return sid

        def session(self, query, where):
            sid = self._sid(query)
            session = app.catalog.session(sid)
            if session is None:
                raise HTTPError(404, f"{sid} is not under any results root.")
            return {"session": session, **self.notebook(query, where)}

        def notebook(self, query, where):
            sid = self._sid(query)
            path = app.notebooks.path(sid)
            mtime = app.notebooks.mtime(sid)
            return {"notebook": app.notebooks.load(sid), "mtime": str(mtime) if mtime else None,
                    "conflicts": app.notebooks.conflicts(sid),
                    "files": {"json": str(path), "markdown": str(path.with_suffix(".md")),
                              "configs": str(electro_gui.config_path(app.configs_root, sid, 0).parent)}}

        def electro_gui_status(self, query, where):
            url = app.electro_gui_url(where)
            return {"url": url, "running": bool(url) and app.electro_gui_running(url)}

        def post_note(self, body):
            sid = body.get("sid", "")
            app.notebooks.path(sid)
            try:
                unit_id = int(body.get("unit"))
            except (TypeError, ValueError):
                raise HTTPError(400, "unit must be an integer")
            changes = {k: body[k] for k in ("note", "tags", "flag") if k in body}
            snapshot = app.snapshot(sid, unit_id)
            notebook, entry = app.notebooks.update(sid, unit_id, changes, snapshot, app.settings()["tags"])
            warning = None
            try:
                electro_gui.sync_unit(app.configs_root, sid, unit_id, entry)
            except OSError as exc:
                warning = f"electro_gui config not written: {exc}"
            mtime = app.notebooks.mtime(sid)
            return {"entry": entry, "notebook": notebook, "mtime": str(mtime) if mtime else None, "warning": warning}

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(app, host="127.0.0.1", port=8766):
    if host not in LOOPBACK:
        raise ValueError("result-viewer listens on loopback only; reach it from other devices with tailscale serve.")
    return Server((host, port), make_handler(app))
