"""A tiny fake results tree in the analysis_imitation layout."""
from __future__ import annotations

import json
import os
from pathlib import Path

SID = "sub-Test_ses-2026_01_02"
TAG = "Test_20260102"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def touch(path, data=PNG, mtime=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def make_session(root, sid=SID, tag=TAG):
    s = Path(root) / sid
    rasters = s / "analysis" / "figures" / "unit_rasters"
    touch(rasters / f"unit_0001_sua_{tag}_acc_rates_raster.png")
    # unit 2 was relabelled mua -> noise; the rates summary names the current label
    touch(rasters / f"unit_0002_mua_{tag}_pmd_rates_raster.png", mtime=2_000_000_000)
    touch(rasters / f"unit_0002_noise_{tag}_pmd_rates_raster.png", mtime=1_000_000_000)
    # unit 3 has two rasters and no summary: the newest wins
    touch(rasters / f"unit_0003_mua_{tag}_acc_rates_raster.png", mtime=1_000_000_000)
    touch(rasters / f"unit_0003_sua_{tag}_acc_rates_raster.png", mtime=2_000_000_000)
    # an old figure of another kind left in unit_rasters, and an untagged-quality raster
    touch(rasters / f"unit_0009_mua_{tag}_acc.png")
    touch(rasters / f"unit_0004_{tag}_acc_rates_raster.png")
    touch(rasters / ".unit_0005_sua_{tag}_acc_rates_raster.png")
    touch(s / "analysis" / "nwb" / f"{sid}_session.nwb", b"", mtime=1_500_000_000)
    tables = s / "analysis" / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    (tables / "unit_0002_rates_summary.json").write_text(json.dumps({"unit": {"unit_id": 2, "quality": "noise"}}))
    (tables / "action_value_variance_units.csv").write_text(
        "unit_id,quality,area,peak_channel,depth_um\n1,sua,ACC,22.0,220.0\n2,mua,PMD,140,1400.5\n")
    figs = s / "analysis" / "figures"
    touch(figs / "selectivity_classes" / f"unit_0001_sua_{tag}_acc.png")
    touch(figs / "selectivity_classes" / f"unit_0002_mua_{tag}_pmd.png")
    touch(figs / "selectivity_classes_onset" / f"unit_0001_sua_{tag}_acc.png")
    touch(figs / "trial_video" / f"unit_0001_sua_{tag}_acc.png")
    touch(figs / f"intention_decoding_unit_0001_sua_{tag}_acc.png")
    touch(figs / "session_overview.png")
    summaries = s / "preprocessing" / "figures" / "unit_summaries"
    touch(summaries / "unit_0001_sua.jpg")
    touch(summaries / "unit_0002_mua.jpg")
    return s
