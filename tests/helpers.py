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
    # the current units: 3 is MUA now (its newest raster says SUA), 4 is not in this build, 5 has no raster
    (tables / "stability_per_unit.csv").write_text(
        "unit_id,quality,stable,n_spikes,n_completed_trials,n_stable_trials,stable_fraction,mean_rate_hz,"
        "stable_mean_rate_hz,stable_cv,stable_intervals,reason\n"
        '1,sua,True,9000,300,300,1.0,5.5123,5.5,0.2,"[[0, 300]]",1 stable block(s) covering 300 trials\n'
        '2,noise,True,4000,300,150,0.5,1.2,1.4,0.3,"[[150, 300]]",1 stable block(s) covering 150 trials\n'
        '3,mua,False,800,300,0,0.0,0.4,,,[],no block of 150+ trials survived\n'
        '5,mua,True,100,300,300,1.0,0.1,0.1,0.1,"[[0, 300]]",1 stable block(s) covering 300 trials\n')
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
