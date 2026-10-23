"""Tailnet access log: one JSON line per HTTP request an app answers, for its owner's usage page.

The rule is *identity in, content out*.  An event says who reached which app, from where, when, and what
kind of request it was; it never says what was asked for or sent:

* **recorded**: the start time (UTC, to the second), the app, the method (from a closed set), a route
  class the app names itself (``routes``, never the path), the status, the duration, ``via`` (``serve``:
  through ``tailscale serve``, which sets ``X-Forwarded-For`` and the caller's ``Tailscale-User-Login``
  and deletes client copies of the identity headers; ``local``: straight to loopback from a process on
  this Mac), the validated login, the caller's tailnet address, the request kind (page, api, asset,
  websocket, from the ``Sec-Fetch-*`` and ``Upgrade`` headers), ``Sec-Fetch-Site``, a coarse client
  family (``safari/ios``, ``curl``, ...; a closed set), the class of the ``Host`` header, and reason
  codes (``notes``) for anything odd.
* **never recorded**: the path or query string, the body, cookies, ``Authorization``, ``Referer``, the
  User-Agent string, or any other header value.  A login that is not plain printable ASCII and an
  ``X-Forwarded-For`` outside the tailnet ranges are replaced by null and a note.  Errors name only an
  exception class and errno.

Local processes can forge every header: this is usage visibility, not a tamper-proof audit.

Files (``<dir>`` is ``resolve_root()``: ``$TAILNET_ACCESS_LOG_DIR``, else ``~/Library/Logs/tailnet-access``;
``TAILNET_ACCESS_LOG=off`` turns logging off)::

    <dir>/<app>/2026-10-22.jsonl   one event per line, by the UTC day of the request start
    <dir>/<app>/status.json        this process: module version, pid, start, counters, last error

**An app logs only where the host opted in.**  ``<dir>`` is made by the dashboard (1.4 and later, which reads,
rolls up and prunes it) or by the owner, never by an app: ``AccessLog(app)`` (``create=False``) writes nothing,
not even ``status.json`` or a line on stderr, while ``<dir>`` is not a directory; it counts those events as
``skipped`` and looks again at most every ``ROOT_RECHECK`` seconds of its clock (one ``stat``), so a directory
made later is picked up within a minute and one removed stops the logging again.  An app can so carry this file
in its ``main`` to every Mac that runs it, and it leaves no files where nothing reads them.  The dashboard's own
log passes ``create=True``, which makes ``<dir>``.

**Each app bounds its own files.**  Once a UTC day (at the first write of the process and the first write of
each new day) it deletes its own day files older than ``KEEP_DAYS`` days: regular ``YYYY-MM-DD.jsonl`` files
directly in ``<dir>/<app>/``, never ``status.json``, nothing through a symbolic link (directory descriptor
opened with ``O_NOFOLLOW``), nothing outside that directory.  The dashboard normally deletes them first (30
days, after rolling them up); this backstop only bounds the growth on a Mac whose dashboard stopped pruning.
A failure only sets ``last_error``.

Directories are made 0o700, files 0o600.  Each line is appended with one ``os.write`` under a lock, so
concurrent requests never interleave.  A day file stops growing at ``max_day_bytes`` (the rest of that
day is counted as dropped).  ``status.json`` is replaced atomically on the first write of a process and
then at most every ``STATUS_EVERY`` seconds of the log's clock, when a counter changed, so it can lag the
log by that much (``flush()`` brings it up to date).

Attaching it to a ``BaseHTTPRequestHandler`` (it wraps ``handle_one_request``, ``parse_request`` and
``send_response_only``, so every request the handler answers is recorded, error pages included, and timed
from its request line, also on a kept-alive connection)::

    from accesslog import AccessLog, attach
    attach(Handler, AccessLog("grid-planner", routes=[("/", "page"), ("/api/*", "api"),
                                                    ("/static/*", "static")]))

Failure never reaches the app: every write catches its error, counts it in ``stats()`` and the status
file, prints at most one line an hour to stderr, and moves on.

This file is vendorable: standard library only, nothing from the ``apps_dashboard`` package, so another
app copies it verbatim.  A copy keeps ``MODULE_VERSION``: it is written into ``status.json``, and the
dashboard flags copies older than its own.
"""
from __future__ import annotations

import errno
import functools
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = 1
MODULE_VERSION = "1.1"
DEFAULT_DIR = "~/Library/Logs/tailnet-access"
ENV_DIR = "TAILNET_ACCESS_LOG_DIR"
ENV_SWITCH = "TAILNET_ACCESS_LOG"  # "off" disables logging
MAX_DAY_BYTES = 32 << 20  # per app per UTC day
MAX_MS = 3_600_000
STATUS_EVERY = 60  # seconds between status.json updates
WARN_EVERY = 3600  # seconds between stderr notices
ROOT_RECHECK = 60  # seconds between looks at whether the log root exists (create=False)
KEEP_DAYS = 60  # the backstop: an app deletes its own day files older than this
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
BROWSERS = ("safari", "chrome", "edge", "firefox", "browser")
INTERNAL_AGENTS = ("apps-dashboard", "apps-dashboard-peer", "headless-chrome")
APP_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ROUTE_CLASS = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,39}$")

METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
SITES = ("same-origin", "same-site", "cross-site", "none")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
PAGE_DESTS = ("document", "iframe", "frame", "embed", "object")
ASSET_DESTS = ("script", "style", "image", "font", "manifest", "audio", "video", "track", "worker",
               "sharedworker", "serviceworker", "xslt", "audioworklet", "paintworklet")
API_MODES = ("cors", "same-origin", "no-cors")
_LOGIN = re.compile(r'[!#-\[\]-~]{1,254}')  # printable ASCII without space, " and \
_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_BOT = re.compile(r"bot|spider|crawler", re.I)
_OPEN = (os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
         | getattr(os, "O_NOFOLLOW", 0))  # never append through a planted symlink
_DAY_FILE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl")
_DIR_OPEN = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
# Pruning through a directory descriptor; without one (never on macOS) it checks each step with lstat instead.
_DIR_FD = (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.listdir in os.supports_fd
           and os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks
           and os.unlink in os.supports_dir_fd)


def resolve_root(directory=None):
    """The log root, expanded: ``directory``, else ``$TAILNET_ACCESS_LOG_DIR``, else ``DEFAULT_DIR`` (an
    empty value counts as none).  The dashboard finds the apps' directory with the same rule."""
    return Path(directory or os.environ.get(ENV_DIR) or DEFAULT_DIR).expanduser()


def iso(ts):
    """Epoch seconds as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _getter(headers):
    """``get(name)``: a header's value without surrounding whitespace, or None.  ``headers`` is an
    ``http.client.HTTPMessage`` (case-insensitive already), anything else with ``.get``, or a plain dict
    (looked up case-insensitively)."""
    if headers is None:
        lookup = lambda name: None  # noqa: E731
    elif isinstance(headers, dict):
        lowered = {str(key).lower(): value for key, value in headers.items()}
        lookup = lambda name: lowered.get(name.lower())  # noqa: E731
    else:
        lookup = headers.get

    def get(name):
        value = lookup(name)
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        return (value if isinstance(value, str) else str(value)).strip(" \t")
    return get


def _has(text, *needles):
    return any(needle in text for needle in needles)


def agent_family(user_agent):
    """The client family of a User-Agent, from a closed set (the string itself is never kept):
    ``none``, ``apps-dashboard``, ``apps-dashboard-peer``, ``headless-chrome``, ``curl``, ``python``,
    ``go``, ``node``, ``bot``, ``other``, or ``<browser>/<platform>`` (``safari/ios``, ``chrome/mac``,
    ``browser/linux``, ...).  An iPad's Safari in desktop mode says Macintosh, so it is ``safari/mac``:
    the device name tells them apart."""
    ua = user_agent.strip() if isinstance(user_agent, str) else ""
    if not ua:
        return "none"
    if "apps-dashboard" in ua:
        return "apps-dashboard-peer" if "(peer)" in ua else "apps-dashboard"
    if "HeadlessChrome" in ua:
        return "headless-chrome"
    if ua.startswith("curl/"):
        return "curl"
    if ua.startswith(("Python-urllib", "python-requests", "python-httpx", "aiohttp")):
        return "python"
    if "Go-http-client" in ua:
        return "go"
    if ua.startswith(("node", "undici", "axios")):
        return "node"
    if _BOT.search(ua):
        return "bot"
    if _has(ua, "iPhone", "iPad", "iPod"):
        platform = "ios"
    elif "Android" in ua:
        platform = "android"
    elif "Windows" in ua:
        platform = "windows"
    elif _has(ua, "Macintosh", "Mac OS X"):
        platform = "mac"
    elif _has(ua, "Linux", "X11", "CrOS"):
        platform = "linux"
    else:
        platform = "other"
    if _has(ua, "Edg/", "EdgiOS", "EdgA"):
        browser = "edge"
    elif _has(ua, "Firefox/", "FxiOS"):
        browser = "firefox"
    elif _has(ua, "CriOS", "Chrome/", "Chromium/"):
        browser = "chrome"
    elif ("Safari/" in ua and "Version/" in ua) or (platform == "ios" and "AppleWebKit" in ua):
        browser = "safari"
    elif "Mozilla/" in ua:
        browser = "browser"
    else:
        return "other"
    return f"{browser}/{platform}"


def host_class(host):
    """The ``Host`` header's class: ``tailnet`` (a ``.ts.net`` name), ``loopback``, ``ip`` or ``other``."""
    if not isinstance(host, str) or not host:
        return "other"
    try:
        name = (urlsplit("//" + host).hostname or "").rstrip(".")  # lowercased, brackets and port gone
    except ValueError:
        return "other"
    if name.endswith(".ts.net"):
        return "tailnet"
    if name in LOOPBACK:
        return "loopback"
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return "other"
    return "ip"


def _kind(get):
    if (get("Upgrade") or "").lower() == "websocket":
        return "websocket"
    mode, dest = (get("Sec-Fetch-Mode") or "").lower(), (get("Sec-Fetch-Dest") or "").lower()
    if mode == "navigate" or dest in PAGE_DESTS:
        return "page"
    if dest in ASSET_DESTS:
        return "asset"
    if mode in API_MODES:
        return "api"
    return "other"


def _tailnet_ip(forwarded):
    """The caller's address from ``X-Forwarded-For`` (serve sets it; the last item is the one serve saw),
    normalized, or None when it is not inside the tailnet ranges."""
    item = forwarded.split(",")[-1].strip()
    if "%" in item:  # a zone index: never one of serve's
        return None
    try:
        addr = ipaddress.ip_address(item)
    except ValueError:
        return None
    return str(addr) if addr in (TAILNET_V4 if addr.version == 4 else TAILNET_V6) else None


def _status(code):
    if isinstance(code, bool) or not isinstance(code, int):
        return 0
    return int(code) if 100 <= code <= 599 else 0


def _ms(duration):
    ms = float(duration) * 1000.0
    if not ms > 0:  # also NaN
        return 0
    return MAX_MS if ms >= MAX_MS else int(round(ms))


def _routes(routes):
    """``[(pattern, class), ...]`` -> ``((prefix, is_prefix, class), ...)``; raises ValueError."""
    if isinstance(routes, dict):
        routes = routes.items()
    out = []
    for item in routes:
        if isinstance(item, str) or not hasattr(item, "__len__") or len(item) != 2:
            raise ValueError(f"a route is a (pattern, class) pair, not {item!r}")
        pattern, name = item
        if not isinstance(pattern, str) or not pattern.startswith("/") or "*" in pattern[:-1]:
            raise ValueError(f"bad route pattern {pattern!r}: an exact path or a prefix ending in *")
        if not isinstance(name, str) or not ROUTE_CLASS.fullmatch(name):
            raise ValueError(f"bad route class {name!r}")
        out.append((pattern[:-1], True, name) if pattern.endswith("*") else (pattern, False, name))
    return tuple(out)


def _describe(exc):
    """``<ExcName>:<errno name>`` (no file names, no messages)."""
    code = errno.errorcode.get(getattr(exc, "errno", None) or 0)
    return f"{type(exc).__name__}:{code}" if code else type(exc).__name__


class _RootGone(Exception):
    """The log root went away after it was found (``create=False``): the host opted out again."""


class AccessLog:
    """One app's access log.  ``event`` is pure; ``write`` and ``record`` never raise.

    ``create=False`` (what apps use) writes only while the log root is a directory already; ``create=True``
    (the dashboard's own log) makes it."""

    def __init__(self, app_id, directory=None, routes=(), max_day_bytes=MAX_DAY_BYTES, clock=time.time,
                 enabled=None, create=False):
        if not isinstance(app_id, str) or not APP_ID.fullmatch(app_id):
            raise ValueError(f"bad app id {app_id!r}")
        if enabled is None:
            enabled = os.environ.get(ENV_SWITCH, "").strip().lower() != "off"
        self.app_id = app_id
        self.root = resolve_root(directory)
        self.dir = self.root / app_id
        self.routes = _routes(routes)
        self.max_day_bytes = max_day_bytes
        self.clock = clock
        self.enabled = bool(enabled)
        self.create = bool(create)
        self._lock = threading.Lock()
        self._started = iso(clock())
        self._written = 0
        self._dropped = {"cap": 0, "error": 0}
        self._skipped = 0  # events not written because the log root is missing (not lost data: not wanted)
        self._waiting = False  # the log root was missing at the last look
        self._root_at = None  # log clock of the last look, None before the first
        self._pruned = None  # the UTC day of the last backstop prune
        self._last_error = None
        self._sizes = {}  # day -> bytes in its file
        self._status_at = None  # log clock of the last status.json, None before the first
        self._status_counts = None
        self._warned = None  # time.monotonic() of the last stderr notice

    # -- events (pure) --

    def route_class(self, path):
        """The route class of a request path (query ignored; first match wins; else ``other``)."""
        try:
            path = urlsplit(path).path if isinstance(path, str) else ""
        except ValueError:
            return "other"
        for pattern, prefix, name in self.routes:
            if path.startswith(pattern) if prefix else path == pattern:
                return name
        return "other"

    def event(self, method, path, headers, status, started, duration):
        """The event for one request (``started`` in epoch seconds, ``duration`` in seconds); no I/O."""
        get = _getter(headers)
        login, forwarded, funnel = get("Tailscale-User-Login"), get("X-Forwarded-For"), get("Tailscale-Funnel-Request")
        serve = login is not None or forwarded is not None or funnel is not None
        ip, notes = None, set()
        if serve:
            if login is None:
                notes.add("no-login")
            elif not _LOGIN.fullmatch(login):
                login = None
                notes.add("login-invalid")
            if forwarded is not None:
                ip = _tailnet_ip(forwarded)
                if ip is None:
                    notes.add("xff-not-tailnet")
            if funnel is not None:
                notes.add("funnel")
        else:
            login = None
        site = (get("Sec-Fetch-Site") or "").lower()
        out = {"v": SCHEMA, "t": iso(started), "app": self.app_id, "method": method if method in METHODS else "OTHER",
               "route": self.route_class(path), "status": _status(status), "ms": _ms(duration),
               "via": "serve" if serve else "local", "login": login, "ip": ip, "kind": _kind(get),
               "site": site if site in SITES else None, "agent": agent_family(get("User-Agent")),
               "host": host_class(get("Host"))}
        if notes:
            out["notes"] = sorted(notes)
        return out

    # -- writing (never raises) --

    def record(self, method, path, headers, status, started=None, duration=0.0):
        """Log one request; returns the event written, or None (disabled, no log root, over the day's cap,
        an error)."""
        if not self.enabled:
            return None
        with self._lock:
            if not self._opted_in():
                return None
        try:
            event = self.event(method, path, headers, status, self.clock() if started is None else started, duration)
        except Exception as exc:  # noqa: BLE001 - a bad argument must not break the request
            with self._lock:
                self._failed(exc)
                self._maybe_status()
            return None
        return event if self.write(event) else None

    def write(self, event):
        """Append one event to its day's file; True if it was written."""
        if not self.enabled:
            return False
        with self._lock:
            if not self._opted_in():
                return False
            try:
                ok = self._append(event)
            except _RootGone:
                self._lost_root()
                self._skipped += 1
                return False
            except Exception as exc:  # noqa: BLE001 - logging never breaks the app
                self._failed(exc)
                ok = False
            self._maybe_prune()
            self._maybe_status()
        return ok

    def flush(self):
        """Bring ``status.json`` up to date now (an app may call this at exit); never raises."""
        with self._lock:
            if self.enabled and not self._waiting and self._counts() != (self._status_counts or (0, 0, 0)):
                self._status_at = None
                self._maybe_status()

    def stats(self):
        """Counters: ``dropped`` is lost data the owner should see; ``skipped`` counts events not wanted
        (no log root on this host), and ``waiting`` says the root was missing at the last look."""
        with self._lock:
            return {"written": self._written, "dropped": dict(self._dropped), "skipped": self._skipped,
                    "waiting": self._waiting, "started": self._started, "last_error": self._last_error}

    def _opted_in(self):
        """Whether lines may be written (lock held): always with ``create``, else while the log root is a
        directory, looked up at most every ROOT_RECHECK s of the log's clock (a clock that went back counts
        as due).  An event that may not be written is counted as skipped."""
        if self.create:
            return True
        try:
            now = self.clock()
            due = self._root_at is None or not 0 <= now - self._root_at < ROOT_RECHECK
        except Exception:  # noqa: BLE001 - a broken clock: look every time
            now, due = None, True
        if due:
            self._root_at = now
            self._waiting = not os.path.isdir(str(self.root))  # one stat; False on any error
        if self._waiting:
            self._skipped += 1
            return False
        return True

    def _lost_root(self):
        """The root went away since the last look: wait for it again, look at the next write, and write
        ``status.json`` at the first write once it is back (the old one went with it)."""
        self._waiting, self._root_at, self._status_at = True, None, None

    def _append(self, event):
        stamp = event.get("t") if isinstance(event, dict) else None
        if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
            raise ValueError("an event needs t as YYYY-MM-DDTHH:MM:SSZ")
        line = (json.dumps(event, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        day = stamp[:10]
        path = self.dir / f"{day}.jsonl"
        size = self._sizes.get(day)
        if size is None:
            try:
                size = os.stat(str(path)).st_size
            except FileNotFoundError:
                size = 0
            if len(self._sizes) >= 4:  # only the latest days are written to
                del self._sizes[min(self._sizes)]
            self._sizes[day] = size
        if size + len(line) > self.max_day_bytes:
            self._dropped["cap"] += 1
            return False
        fd = self._in_dir(lambda: os.open(str(path), _OPEN, 0o600))
        try:
            view = memoryview(line)
            while view:  # one write in practice; finish a short one rather than leave half a line
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError(errno.EIO, "short write")
                view = view[count:]
        finally:
            os.close(fd)
        self._sizes[day] = size + len(line)
        self._written += 1
        return True

    def _in_dir(self, action):
        """``action()``, making the app's directory (0o700) first if it is missing, and with ``create`` the
        log root too; without, a missing root raises ``_RootGone``."""
        try:
            return action()
        except FileNotFoundError:
            if self.create:
                os.makedirs(str(self.root), 0o700, exist_ok=True)
                os.makedirs(str(self.dir), 0o700, exist_ok=True)
            else:
                try:
                    os.mkdir(str(self.dir), 0o700)
                except FileExistsError:
                    pass
                except FileNotFoundError:
                    raise _RootGone() from None
            return action()

    def _maybe_prune(self):
        """The backstop, on the first write of the process and of each UTC day (lock held)."""
        try:
            now = self.clock()
            today = time.strftime("%Y-%m-%d", time.gmtime(now))
        except Exception:  # noqa: BLE001 - a broken clock: no pruning
            return
        if today != self._pruned:
            self._pruned = today
            self._prune(now)

    def _prune(self, now):
        """Delete this app's own day files older than KEEP_DAYS days; never raises (a failure only sets
        ``last_error``).  A day file is kept for exactly KEEP_DAYS days, as the dashboard keeps raw_days."""
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(now - KEEP_DAYS * 86400))  # days before it go
        try:
            if _DIR_FD:
                fd = os.open(str(self.dir), _DIR_OPEN)  # fails on a symlinked app directory (ENOTDIR, ELOOP)
                try:
                    for name in sorted(os.listdir(fd)):
                        if _DAY_FILE.fullmatch(name) and name[:10] < cutoff:
                            self._unlink_day(name, fd)
                finally:
                    os.close(fd)
            else:
                if stat.S_ISLNK(os.lstat(str(self.dir)).st_mode):
                    raise OSError(errno.ELOOP, "a symlinked app directory")
                for name in sorted(os.listdir(str(self.dir))):
                    if _DAY_FILE.fullmatch(name) and name[:10] < cutoff:
                        self._unlink_day(name, None)
        except FileNotFoundError:
            pass  # nothing written yet
        except Exception as exc:  # noqa: BLE001 - the backstop never breaks a write
            self._last_error = _describe(exc)

    def _unlink_day(self, name, fd):
        """Delete one day file of this app if it is a regular file (never through a symlink)."""
        try:
            if fd is None:
                path = str(self.dir / name)
                if stat.S_ISREG(os.lstat(path).st_mode):
                    os.unlink(path)
            elif stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass  # the dashboard deleted it meanwhile
        except OSError as exc:  # the other files are still tried
            self._last_error = _describe(exc)

    def _failed(self, exc):
        self._dropped["error"] += 1
        self._error(exc)

    def _error(self, exc):
        self._last_error = _describe(exc)
        now = time.monotonic()
        if self._warned is None or now - self._warned >= WARN_EVERY:
            self._warned = now
            try:
                print(f"accesslog: cannot write {self.app_id} log: {type(exc).__name__}", file=sys.stderr, flush=True)
            except Exception:  # noqa: BLE001 - no stderr is fine too
                pass

    def _counts(self):
        return self._written, self._dropped["cap"], self._dropped["error"]

    def _maybe_status(self):
        """Replace ``status.json`` on the first write, then at most every STATUS_EVERY s when a counter
        changed (a clock that went back counts as due).  Called with the lock held."""
        counts = self._counts()
        try:
            now = self.clock()
            if self._status_at is not None and (counts == self._status_counts
                                                or 0 <= now - self._status_at < STATUS_EVERY):
                return
            self._status_at, self._status_counts = now, counts
            data = {"v": SCHEMA, "app": self.app_id, "module": MODULE_VERSION, "pid": os.getpid(),
                    "started": self._started, "updated": iso(now), "written": self._written,
                    "dropped": dict(self._dropped), "last_error": self._last_error}
            body = (json.dumps(data, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
            fd, tmp = self._in_dir(lambda: tempfile.mkstemp(prefix=".status-", suffix=".tmp", dir=str(self.dir)))
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(body)
                os.replace(tmp, str(self.dir / "status.json"))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except _RootGone:
            self._lost_root()
        except Exception as exc:  # noqa: BLE001 - a missing status file only delays the dashboard's view
            self._error(exc)


def _record_request(handler, status, headers_before, started, duration):
    log = getattr(handler, "_access_log", None)
    if log is None:
        return
    command = getattr(handler, "command", None)
    # A kept-alive handler still holds the previous request's path and headers when this request's line or
    # headers could not be parsed: never attribute them to this one.  ``parse_request`` sets ``command``
    # and ``path`` together and makes a new headers object for every request it parses.
    path = getattr(handler, "path", "") if command else ""
    headers = getattr(handler, "headers", None)
    if headers is headers_before:
        headers = None
    log.record(command or "OTHER", path, headers or {}, status, started, duration)


def _now():
    return time.time(), time.monotonic()


def attach(handler_cls, log):
    """Record every request ``handler_cls`` (a ``BaseHTTPRequestHandler``) answers into ``log``; returns
    the same class.  Attaching again (to it or a subclass) replaces the log; requests are still recorded once.
    A request is recorded when it got a status line (its first one other than 100 Continue); an empty or
    blank connection is not.

    A request starts when its request line has been read (``parse_request`` is called right after), not
    when ``handle_one_request`` begins: on a kept-alive connection that waits, idle, for the next request,
    which would date it to the end of the one before and count the wait in ``ms``."""
    handler_cls._access_log = log
    if getattr(handler_cls.handle_one_request, "_accesslog", False):
        return handler_cls
    send_response_only, handle_one_request = handler_cls.send_response_only, handler_cls.handle_one_request
    parse_request = handler_cls.parse_request

    @functools.wraps(parse_request)
    def parse_request_logged(self):
        self._access_start = _now()
        return parse_request(self)

    @functools.wraps(send_response_only)
    def send_response_only_logged(self, code, message=None):
        if getattr(self, "_access_status", None) is None and code != 100:
            self._access_status = code
        if getattr(self, "_access_start", None) is None:  # answered before parse_request: a line too long (414)
            self._access_start = _now()
        return send_response_only(self, code, message)

    @functools.wraps(handle_one_request)
    def handle_one_request_logged(self):
        self._access_status = self._access_start = None
        headers_before = getattr(self, "headers", None)
        try:
            return handle_one_request(self)
        finally:
            status = getattr(self, "_access_status", None)
            if status is not None:
                try:
                    started, start = getattr(self, "_access_start", None) or _now()
                    _record_request(self, status, headers_before, started, time.monotonic() - start)
                except Exception:  # noqa: BLE001 - logging never breaks the app
                    pass

    for wrapper in (parse_request_logged, send_response_only_logged, handle_one_request_logged):
        wrapper._accesslog = True
    handler_cls.parse_request = parse_request_logged
    handler_cls.send_response_only = send_response_only_logged
    handler_cls.handle_one_request = handle_one_request_logged
    return handler_cls
