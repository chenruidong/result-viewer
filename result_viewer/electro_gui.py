"""Hand units to electro_gui (analysis_imitation/electro_gui).

Three ways, none of which needs a change to electro_gui:

* **Deep link.**  electro_gui keeps its whole view in the address bar as
  ``#s=<base64url(UTF-8 JSON state)>`` (``static/core.js`` ``encodeState``) and
  fills every missing key with its defaults, so ``{"session", "unit"}`` opens a
  unit.  Its unit picker lists SUA and MUA only by default (``unitQ``); a link
  to any other unit widens that list so the unit stays selectable.
* **Config files.**  Every noted unit is mirrored to
  ``<data>/electro_gui/<sid>/electro_gui_configs/unit_NNNN.json`` in electro_gui's
  config-file format (``electro_gui/configs.py``).  Launch electro_gui with
  ``--config-dir <data>/electro_gui`` and the notes appear under Views ->
  Config files, one section per session.  Files this module did not write
  (no ``"generator": "result-viewer"``) are never changed or removed.
* **Starting it.**  When "open in electro_gui" finds nothing answering on this
  Mac, ``Launcher`` starts it from the analysis_imitation checkout, the way it
  is started by hand, with the config folder above added.
"""
from __future__ import annotations

import base64
import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .notebook import unit_heading, utc_now, write_atomic

GENERATOR = "result-viewer"
NESTED = "electro_gui_configs"
DEFAULT_QUALITIES = ("sua", "mua")
DEFAULT_REPO = "~/code/analysis_imitation"
PYTHON_CANDIDATES = ("~/miniconda/envs/imitation-phys/bin/python", "~/miniforge3/envs/imitation-phys/bin/python",
                     "~/miniconda3/envs/imitation-phys/bin/python", "~/mambaforge/envs/imitation-phys/bin/python",
                     "~/anaconda3/envs/imitation-phys/bin/python")
START_LOG = "~/Library/Logs/electro_gui.log"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def view_state(sid, unit_id, quality=None):
    state = {"session": sid, "unit": int(unit_id)}
    if quality and quality not in DEFAULT_QUALITIES:
        state["unitQ"] = list(DEFAULT_QUALITIES) + [quality]
    return state


def encode_state(state):
    """electro_gui's ``encodeState``: base64url of the UTF-8 JSON, no padding."""
    raw = json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def deep_link(base_url, sid, unit_id, quality=None):
    """``base_url`` may carry electro_gui's ``?token=`` (a --lan server); the state goes in the fragment."""
    parts = urlsplit(base_url)
    fragment = "s=" + encode_state(view_state(sid, unit_id, quality))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, fragment))


def config_path(configs_root, sid, unit_id):
    return Path(configs_root) / sid / NESTED / f"unit_{int(unit_id):04d}.json"


def build_config(sid, entry):
    note = (entry.get("note") or "").strip()
    first = note.splitlines()[0] if note else ""
    tags = entry.get("tags") or []
    hint = " · ".join(x for x in (", ".join(tags), first[:160]) if x)
    return {"electro_gui_config": 1,
            "title": unit_heading(entry) + (" ★" if entry.get("flag") else ""),
            "hint": hint,
            "group": f"Notes · {sid}",
            "slide": int(entry["unit_id"]),
            "note": note,
            "state": view_state(sid, entry["unit_id"], entry.get("quality")),
            "generator": GENERATOR,
            "tags": tags,
            "flag": bool(entry.get("flag"))}


def _ours(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle).get("generator") == GENERATOR
    except (OSError, ValueError, AttributeError):
        return False


def sync_unit(configs_root, sid, unit_id, entry):
    """Write (or, when ``entry`` is None, remove) this unit's config file."""
    path = config_path(configs_root, sid, unit_id)
    if entry is None:
        if path.exists() and _ours(path):
            path.unlink()
        return None
    if path.exists() and not _ours(path):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(build_config(sid, entry), indent=1, ensure_ascii=False) + "\n")
    return path


def sync_session(configs_root, sid, notebook):
    """Mirror a whole notebook: write every entry, remove our files for units no longer noted."""
    keep = set()
    for entry in notebook.get("units", {}).values():
        written = sync_unit(configs_root, sid, entry["unit_id"], entry)
        if written:
            keep.add(written.name)
    folder = Path(configs_root) / sid / NESTED
    if folder.is_dir():
        for path in folder.glob("unit_*.json"):
            if path.name not in keep and _ours(path):
                path.unlink()
    return sorted(keep)


def probe(base_url, timeout=2.0):
    """Is electro_gui up at ``base_url``?

    Any HTTP answer counts (a ``--token`` server answers 403) except a proxy's
    502-504 (``tailscale serve`` with nothing behind it); so does a connection
    that opens but answers slowly, which is electro_gui busy loading a session.
    """
    parts = urlsplit(base_url)
    try:
        with urllib.request.urlopen(urlunsplit((parts.scheme, parts.netloc, "/api/health", "", "")), timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code not in (502, 503, 504)
    except socket.timeout:
        return True
    except (OSError, ValueError):
        return False


class Launcher:
    """Start electro_gui on this Mac when "open in electro_gui" finds nothing answering.

    Runs ``<python> -m electro_gui --no-browser --port <port of url> --config-dir
    output/presentation --config-dir <configs_root>`` in the analysis_imitation
    checkout (its defaults otherwise, so caches and saved views land where a
    hand-started electro_gui puts them), in a session of its own so it outlives a
    viewer restart, with its output appended to ``~/Library/Logs/electro_gui.log``.
    Only a loopback ``url`` is started; the Python is the first imitation-phys
    environment found unless one is given.
    """

    def __init__(self, url, configs_root, repo=None, python=None, log_path=START_LOG):
        self.url = url or ""
        self.configs_root = Path(configs_root)
        self.repo = Path(repo or DEFAULT_REPO).expanduser()
        if python:
            self.python = Path(python).expanduser()
        else:
            self.python = next((Path(c).expanduser() for c in PYTHON_CANDIDATES if Path(c).expanduser().is_file()), None)
        self.log_path = Path(log_path).expanduser()
        self._proc = None
        self._lock = threading.Lock()

    def port(self):
        parts = urlsplit(self.url)
        if parts.hostname not in LOOPBACK:
            return None
        return parts.port or (443 if parts.scheme == "https" else 80)

    def problem(self):
        """Why electro_gui cannot be started from here, or None."""
        if not self.url:
            return "no electro_gui URL is configured"
        if self.port() is None:
            return f"{self.url} is not on this Mac"
        if not (self.repo / "electro_gui" / "__main__.py").is_file():
            return f"no electro_gui package in {self.repo}"
        if self.python is None or not self.python.is_file():
            return "no imitation-phys Python found (pass --electro-gui-python)"
        return None

    def command(self):
        return [str(self.python), "-m", "electro_gui", "--no-browser", "--port", str(self.port()),
                "--config-dir", "output/presentation", "--config-dir", str(self.configs_root)]

    def state(self):
        proc = self._proc
        out = {"can_start": self.problem() is None, "problem": self.problem(), "log": str(self.log_path)}
        if proc is not None:
            code = proc.poll()
            out.update(pid=proc.pid, starting=code is None, exit_code=code)
        return out

    def start(self):
        """Start electro_gui unless it answers already or our last start is still coming up."""
        with self._lock:
            problem = self.problem()
            if problem:
                return {"started": False, "error": problem}
            if probe(self.url, timeout=1.0):
                return {"started": False, "running": True}
            if self._proc is not None and self._proc.poll() is None:
                return {"started": False, "starting": True, "pid": self._proc.pid, "log": str(self.log_path)}
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n--- {utc_now()} started by result-viewer in {self.repo}: {' '.join(self.command())}\n")
                log.flush()
                self._proc = subprocess.Popen(self.command(), cwd=str(self.repo), stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            return {"started": True, "pid": self._proc.pid, "log": str(self.log_path)}
