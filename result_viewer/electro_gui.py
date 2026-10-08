"""Hand units to electro_gui (analysis_imitation/electro_gui).

Two ways, neither of which needs a change to electro_gui:

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
"""
from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .notebook import unit_heading, write_atomic

GENERATOR = "result-viewer"
NESTED = "electro_gui_configs"
DEFAULT_QUALITIES = ("sua", "mua")


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
