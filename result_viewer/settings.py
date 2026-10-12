"""Where the data lives, and ``<data>/settings.json`` (shared through Dropbox).

``settings.json`` holds what every machine should agree on::

    {"tags": ["interesting", "block", "intention", "joystick", "eye", "noise"],
     "electro_gui": {"url": "http://127.0.0.1:8765", "remote_url": ""}}

``tags`` are toggled by the number keys in this order (at most nine).
``electro_gui.url`` is opened from the Mac running the viewer when the apps
dashboard sees no electro_gui up there on another port; ``remote_url``
from any other device (the iPhone over the tailnet) and may carry electro_gui's
``?token=`` — leave it empty to hide the button there.  It is re-read on every
page load, so an edit needs only a reload.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .notebook import write_atomic

PROJECT = "result-viewer"
MAX_TAGS = 9
DEFAULTS = {
    "tags": ["interesting", "block", "intention", "joystick", "eye", "noise"],
    "electro_gui": {"url": "http://127.0.0.1:8765", "remote_url": ""},
}


def dropbox_root():
    """The MIT (business) Dropbox folder from ``~/.dropbox/info.json``, else None."""
    try:
        with Path("~/.dropbox/info.json").expanduser().open("r", encoding="utf-8") as handle:
            info = json.load(handle)
        return Path(info["business"]["path"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def find_data_dir(explicit=None):
    """``--data-dir``, else ``$RESULT_VIEWER_DATA``, else ``<MIT Dropbox>/projects/result-viewer/data``."""
    for candidate in (explicit, os.environ.get("RESULT_VIEWER_DATA")):
        if candidate:
            return Path(candidate).expanduser()
    root = dropbox_root()
    if root is not None:
        return root / "projects" / PROJECT / "data"
    found = sorted(Path("~").expanduser().glob(f"MIT Dropbox/*/projects/{PROJECT}/data"))
    return found[0] if found else None


def load_settings(data_dir):
    """Settings with defaults filled in; writes the defaults the first time."""
    path = Path(data_dir) / "settings.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raw = None
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if raw is None:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(DEFAULTS, indent=1) + "\n")
        raw = {}
    out = copy.deepcopy(DEFAULTS)
    if isinstance(raw, dict):
        tags = raw.get("tags")
        if isinstance(tags, list):
            clean = []
            for tag in tags:
                if isinstance(tag, str) and tag.strip() and tag.strip() not in clean:
                    clean.append(tag.strip()[:40])
            out["tags"] = clean[:MAX_TAGS]
        eg = raw.get("electro_gui")
        if isinstance(eg, dict):
            for key in ("url", "remote_url"):
                if isinstance(eg.get(key), str):
                    out["electro_gui"][key] = eg[key].strip()
    return out
