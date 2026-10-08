"""Find sessions, units and per-unit figures in Engaging result trees.

A results root holds one directory per session, laid out by analysis_imitation:

    <root>/<sid>/analysis/figures/unit_rasters/unit_NNNN_<q>_<tag>_<area>_rates_raster.png
    <root>/<sid>/analysis/figures/<kind>/unit_NNNN_<q>_<tag>_<area>.png
    <root>/<sid>/analysis/figures/<kind>_unit_NNNN_<q>_<tag>_<area>.png
    <root>/<sid>/preprocessing/figures/unit_summaries/unit_NNNN_<q>.jpg
    <root>/<sid>/analysis/tables/unit_NNNN_rates_summary.json

``<tag>`` is ``imitation_phys.naming.session_tag`` (``Ravel_20260915`` for
``sub-Ravel_ses-2026_09_15``).  A unit is one NWB unit id; its rate raster
defines it.  When a unit was relabelled (``mua`` -> ``noise``) the old figures
stay next to the new ones, so a unit can have several rasters that differ only
in the quality token: its rates summary JSON names the current label, and the
newest file wins when that JSON is missing.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
from pathlib import Path

DEFAULT_ROOTS = ("/Volumes/T7/engaging-results", "/Volumes/Transfer/engaging_results")

RASTER_DIR = ("analysis", "figures", "unit_rasters")
FIGURES_DIR = ("analysis", "figures")
SUMMARIES_DIR = ("preprocessing", "figures", "unit_summaries")
TABLES_DIR = ("analysis", "tables")
NWB_DIR = ("analysis", "nwb")
RASTER_SUFFIX = "_rates_raster.png"
IMAGE_EXTS = (".png", ".jpg", ".jpeg")
# Figure folders that hold no per-unit figures, or none of the raster's kind.
SKIP_FIGURE_DIRS = {"unit_rasters", "trial_video"}

RASTER_KIND = "raster"
SORTER_KIND = "sorter_summary"
KIND_LABELS = {RASTER_KIND: "Raster", SORTER_KIND: "Sorter summary", "object_outcome_unit_psth": "Object outcome PSTH",
               "selectivity_classes": "Selectivity"}

_SESSION_ID_RE = re.compile(r"^sub-(?P<subject>[^_]+)_ses-(?P<date>[0-9][0-9_\-]*)")
_SLUG = r"[a-z0-9-]+"


def _sanitize(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text).strip()).strip("-")


def session_tag(session_id):
    """``sub-Ravel_ses-2026_08_26`` -> ``Ravel_20260826`` (mirrors imitation_phys.naming.session_tag)."""
    match = _SESSION_ID_RE.match(session_id or "")
    if match:
        return f"{_sanitize(match['subject'])}_{re.sub(r'[^0-9]', '', match['date'])}"
    return _sanitize(session_id) or "unknown-session"


def kind_label(kind):
    """``selectivity_classes_onset_movelocked_peri`` -> ``Selectivity · onset movelocked peri``."""
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    for prefix, label in KIND_LABELS.items():
        if kind.startswith(prefix + "_"):
            return f"{label} · {kind[len(prefix) + 1:].replace('_', ' ')}"
    return kind.replace("__", " · ").replace("_", " ").capitalize()


class _Patterns:
    """File-name patterns for one session tag."""

    def __init__(self, sid):
        tag = re.escape(session_tag(sid))
        stem = rf"unit_(?P<id>\d+)(?:_(?P<q>{_SLUG}))?_{tag}(?:_(?P<area>{_SLUG}))?"
        self.raster = re.compile(rf"^{stem}{re.escape(RASTER_SUFFIX)}$")
        self.figure = re.compile(rf"^{stem}\.(?:png|jpe?g)$")
        self.prefixed = re.compile(rf"^(?P<kind>[A-Za-z0-9_-]+?)_{stem}\.(?:png|jpe?g)$")
        self.summary = re.compile(rf"^unit_(?P<id>\d+)(?:_(?P<q>{_SLUG}))?\.(?:png|jpe?g)$")


def _scan(directory):
    """``[(name, DirEntry)]`` for the files in ``directory``; empty when it is missing."""
    try:
        with os.scandir(directory) as it:
            return [(e.name, e) for e in it if not e.name.startswith(".") and e.is_file()]
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []


def _mtime(entry):
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


def _signature(session_dir):
    """Directory mtimes that change whenever a figure is added or removed."""
    dirs = [session_dir.joinpath(*RASTER_DIR), session_dir.joinpath(*FIGURES_DIR), session_dir.joinpath(*SUMMARIES_DIR)]
    try:
        with os.scandir(session_dir.joinpath(*FIGURES_DIR)) as it:
            dirs += [Path(e.path) for e in it if e.is_dir() and not e.name.startswith(".")]
    except OSError:
        pass
    out = []
    for d in dirs:
        try:
            out.append((str(d), d.stat().st_mtime_ns))
        except OSError:
            out.append((str(d), None))
    return tuple(out)


def _current_quality(session_dir, unit_id):
    path = session_dir.joinpath(*TABLES_DIR, f"unit_{unit_id:04d}_rates_summary.json")
    try:
        with path.open("r", encoding="utf-8") as handle:
            quality = json.load(handle).get("unit", {}).get("quality")
    except (OSError, ValueError, AttributeError):
        return None
    if quality is None:
        return None
    return str(quality).strip().lower() or None


def _channels(session_dir, sid):
    """``{unit_id: {"peak_channel", "depth_um"}}`` from the first per-unit table that has them.

    Only a table written after the session NWB counts: an older one can describe
    an earlier build of the units (09_15's tables put unit 4 on channel 22, the
    current NWB on 28), and a wrong channel is worse than none.
    """
    try:
        nwb_mtime = session_dir.joinpath(*NWB_DIR, f"{sid}_session.nwb").stat().st_mtime
    except OSError:
        return {}
    tables = session_dir.joinpath(*TABLES_DIR)
    names = sorted(name for name, _ in _scan(tables) if name.endswith("_units.csv"))
    # Prefer the table written for every unit; the others cover subsets.
    names.sort(key=lambda n: (not n.startswith("action_value_variance"), n))
    for name in names:
        try:
            if (tables / name).stat().st_mtime < nwb_mtime:
                continue
            with (tables / name).open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or "unit_id" not in reader.fieldnames or "peak_channel" not in reader.fieldnames:
                    continue
                out = {}
                for row in reader:
                    try:
                        uid = int(float(row["unit_id"]))
                    except (TypeError, ValueError):
                        continue
                    out[uid] = {"peak_channel": _number(row.get("peak_channel")), "depth_um": _number(row.get("depth_um"))}
                return out
        except OSError:
            continue
    return {}


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return int(value) if value.is_integer() else value


def _pick(candidates, quality):
    """The figure labelled ``quality``, else the newest one; ``candidates`` are ``(q, DirEntry)``."""
    if len(candidates) == 1:
        return candidates[0]
    pool = [c for c in candidates if quality and c[0] == quality] or candidates
    return pool[0] if len(pool) == 1 else max(pool, key=lambda c: _mtime(c[1]))


def scan_session(root_index, root, sid):
    """Every unit of one session with its figures, in unit-id order."""
    session_dir = Path(root) / sid
    pat = _Patterns(sid)

    rasters = {}
    for name, entry in _scan(session_dir.joinpath(*RASTER_DIR)):
        m = pat.raster.match(name)
        if m:
            rasters.setdefault(int(m["id"]), []).append((m["q"], m["area"], entry))

    units = {}
    for uid, found in rasters.items():
        # Only a relabelled unit has more than one raster; its rates summary names the current label.
        current = _current_quality(session_dir, uid) if len(found) > 1 else None
        q, entry = _pick([(fq, e) for fq, _a, e in found], current)
        area = next(a for _q, a, e in found if e is entry)
        units[uid] = {"id": uid, "quality": q, "area": area, "peak_channel": None, "depth_um": None,
                      "relabelled_from": sorted({qq for qq, _a, _e in found if qq and qq != q}),
                      "figures": {RASTER_KIND: _figure(root_index, root, entry, q, q)}}

    def attach(kind, uid, cands):
        unit = units.get(uid)
        if unit is not None:
            fq, entry = _pick(cands, unit["quality"])
            unit["figures"][kind] = _figure(root_index, root, entry, fq, unit["quality"])

    grouped = {}
    figures_dir = session_dir.joinpath(*FIGURES_DIR)
    try:
        with os.scandir(figures_dir) as it:
            subdirs = sorted(e.name for e in it if e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_FIGURE_DIRS)
    except OSError:
        subdirs = []
    for kind in subdirs:
        for name, entry in _scan(figures_dir / kind):
            m = pat.figure.match(name)
            if m:
                grouped.setdefault((kind, int(m["id"])), []).append((m["q"], entry))
    for name, entry in _scan(figures_dir):
        m = pat.prefixed.match(name)
        if m and name.lower().endswith(IMAGE_EXTS):
            grouped.setdefault((m["kind"], int(m["id"])), []).append((m["q"], entry))
    for name, entry in _scan(session_dir.joinpath(*SUMMARIES_DIR)):
        m = pat.summary.match(name)
        if m:
            grouped.setdefault((SORTER_KIND, int(m["id"])), []).append((m["q"], entry))
    for (kind, uid), cands in grouped.items():
        attach(kind, uid, cands)

    for uid, info in _channels(session_dir, sid).items():
        if uid in units:
            units[uid].update(info)

    kinds = {}
    for unit in units.values():
        for kind in unit["figures"]:
            kinds[kind] = kinds.get(kind, 0) + 1
    order = [RASTER_KIND] + ([SORTER_KIND] if SORTER_KIND in kinds else [])
    order += sorted(k for k in kinds if k not in order)
    return {"id": sid, "root": str(root), "root_index": root_index,
            "views": [{"kind": k, "label": kind_label(k), "n": kinds[k]} for k in order if k in kinds],
            "units": [units[u] for u in sorted(units)]}


def _figure(root_index, root, entry, figure_quality, unit_quality):
    rel = Path(entry.path).relative_to(root).as_posix()
    out = {"url": f"/fig/{root_index}/{rel}", "name": entry.name}
    if figure_quality and unit_quality and figure_quality != unit_quality:
        out["label_in_figure"] = figure_quality
    return out


class Catalog:
    """Sessions across result roots; the first root holding a session wins."""

    def __init__(self, roots):
        self.roots = [Path(r).expanduser() for r in roots]
        self._lock = threading.Lock()
        self._cache = {}

    def sessions(self):
        seen = {}
        for index, root in enumerate(self.roots):
            try:
                with os.scandir(root) as it:
                    names = [e.name for e in it if e.is_dir() and not e.name.startswith(".")]
            except OSError:
                continue
            for sid in sorted(names):
                if sid not in seen and root.joinpath(sid, *RASTER_DIR).is_dir():
                    seen[sid] = (index, root)
        return seen

    def session(self, sid):
        found = self.sessions().get(sid)
        if found is None:
            return None
        index, root = found
        sig = _signature(root / sid)
        with self._lock:
            hit = self._cache.get(sid)
            if hit and hit[0] == sig and hit[1]["root_index"] == index:
                return hit[1]
        scanned = scan_session(index, root, sid)
        with self._lock:
            self._cache[sid] = (sig, scanned)
        return scanned

    def resolve_figure(self, root_index, rel):
        """The file under root ``root_index`` named by ``rel``, or None if it escapes the root or is not an image."""
        try:
            root = self.roots[int(root_index)].resolve()
        except (IndexError, ValueError, OSError):
            return None
        if not rel.lower().endswith(IMAGE_EXTS):
            return None
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path if path.is_file() else None
