# result-viewer

Flip through the per-unit figures of the Engaging results (unit rate rasters by default) with the
keyboard, mouse or touch, and keep a **notebook per session** — a note, number-key tags and a flag
per unit — in MIT Dropbox. Units you note show up in **electro_gui** (analysis_imitation), and every
unit opens there with one key.

Python 3.8+ standard library only; no build step.

```bash
./rv                      # = python3 -m result_viewer serve; opens http://127.0.0.1:8766/
python3 -m unittest discover -s tests -t .
```

## What it reads and writes

| | Path | |
|---|---|---|
| Results (read-only) | `/Volumes/T7/engaging-results`, then `/Volumes/Transfer/engaging_results` (`--root`, repeatable; first root holding a session wins) | `<sid>/analysis/figures/…`, `<sid>/preprocessing/figures/unit_summaries/`, a few `<sid>/analysis/tables/` files |
| Notebooks | `<MIT Dropbox>/projects/result-viewer/data/notes/<sid>.json` | source of truth, one file per session |
| | `…/notes/<sid>.md` | readable copy, regenerated on every save (edits to it are overwritten) |
| electro_gui configs | `…/data/electro_gui/<sid>/electro_gui_configs/unit_NNNN.json` | one per noted unit, mirrored on every save |
| Settings | `…/data/settings.json` | tag list and electro_gui URLs, shared by every machine |

The data folder is found from `~/.dropbox/info.json` (business account); override with `--data-dir`
or `RESULT_VIEWER_DATA`.

**Units.** A unit is an NWB unit id, defined by its rate raster
`analysis/figures/unit_rasters/unit_NNNN_<quality>_<Subject>_<yyyymmdd>_<area>_rates_raster.png`
(names from `imitation_phys.naming.unit_stem`). Figures are not redrawn when units are relabelled, so
a file name can carry an old label, and a relabelled unit can have rasters under several labels. The
current label, the firing rate (session mean) and the stable trials come from
`tables/stability_per_unit.csv`, which has one row per unit of the session NWB and is written by the
same run that assembles it: in 09_15 it agrees with the NWB on all 875 units, while 155 raster file
names still say MUA for units now labelled noise. Without that table the label is
`tables/unit_NNNN_rates_summary.json` → `unit.quality`, then the newest raster's. The page says
"relabelled · was MUA", marks a figure that still carries the old label, and strikes through a unit
the current table does not have at all (11 rasters in 09_15 are from an older build). A unit is
**full-session stable** when it is stable on every completed trial
(`n_stable_trials == n_completed_trials`). Other per-unit figures — every `analysis/figures/<kind>/unit_NNNN_*`,
`analysis/figures/<kind>_unit_NNNN_*` and the sorter's `unit_summaries/unit_NNNN_<q>.jpg` — appear as
extra figure tabs. Peak channel and depth come from the first `tables/*_units.csv` that has them
and was written after the session NWB; an older table can describe an earlier build of the units
(09_15's tables put unit 4 on channel 22 where the current NWB and electro_gui say 28), so such
sessions show no channel rather than a wrong one.

**Notebook entries** record what the unit looked like when noted (quality, area, peak channel,
depth, raster file name), because unit ids do not survive a re-sort:

```json
{"schema": "result-viewer.notebook/1", "session_id": "sub-Ravel_ses-2026_09_15", "updated_at_utc": "…",
 "units": {"4": {"unit_id": 4, "note": "dip after reward", "tags": ["interesting"], "flag": true,
                 "quality": "noise", "area": "acc", "peak_channel": 22, "depth_um": 220,
                 "figure": "unit_0004_noise_Ravel_20260915_acc_rates_raster.png",
                 "created_at_utc": "…", "updated_at_utc": "…", "updated_on": "imac"}}}
```

Each save re-reads the file and changes one unit, so notes taken on two machines merge once Dropbox
has synced. If Dropbox still makes a "conflicted copy", the page says so; merge it by hand.
`python3 -m result_viewer rebuild` regenerates every `.md` and electro_gui config from the JSON
(after renaming tags, say).

## Using it

| Keys | |
|---|---|
| `→` `←` · `j` `k` · `Space` | next / previous unit in the filtered list |
| `Home` `End` · `g` | first / last unit · go to unit # |
| `s` · `S` | sort by unit # → firing rate → stable trials · reverse the sort |
| `]` `[` · `r` | next / previous figure · back to the raster |
| `}` `{` | next / previous session |
| `1`–`9` · `f` | toggle tag · flag |
| `n` / `Enter` · `Esc` · `⌘↩` | write a note · done · save and go to the next unit |
| `z` / click | zoom to full resolution and back (drag to pan; zoom stays while you flip) |
| `e` | open this unit in electro_gui |
| `m` · `?` | hide the side panels · help |

The left column lists the units with their tags (dots), note (✎) and flag (★); the chips above it
filter by quality, full-session stability, area, flag, noted and tag, and the arrows then step
through the matches only. **Sort** (above the list) orders them by unit # (the default), firing rate
or stable trials, highest first; click the active sort again (or `S`) to reverse it, and the value
shows in each row. Units without a value sort last. The header shows the unit's rate and stable
trials. Notes save as you type. The notebook column lists every noted unit of the session.

On a phone the unit list and filters are behind ☰; swipe the figure for the next/previous unit, tap
it to zoom. Landscape shows the raster largest.

Tags live in `settings.json` (`"tags": [...]`, at most nine, keys 1–9 in order); reload the page
after editing.

## electro_gui

* **Open a unit** (`e` or the electro_gui button): electro_gui keeps its view in the address bar as
  `#s=<base64url JSON>`, so the viewer opens `http://127.0.0.1:8765/#s={"session":…,"unit":…}` in a
  tab named `electro_gui` (reused, and applied without a reload). For a unit labelled other than
  SUA/MUA the link also widens electro_gui's unit picker (`unitQ`) so the unit stays selectable.
* **Starting it**: when nothing answers at that URL, `e` shows "Starting electro_gui…" in that tab,
  the viewer starts it as you would by hand —
  `cd ~/code/analysis_imitation && <imitation-phys python> -m electro_gui --no-browser --port 8765
  --config-dir output/presentation --config-dir <data>/electro_gui` — and the tab moves on to the
  unit once electro_gui answers (the first load of a session then verifies its NWB, as usual). The
  Python is the first `imitation-phys` env under `~/miniconda`, `~/miniforge3`, `~/miniconda3`,
  `~/mambaforge` or `~/anaconda3`; override with `--electro-gui-python` / `--electro-gui-repo`.
  It runs in a session of its own, so it keeps running after the viewer restarts; its output goes
  to `~/Library/Logs/electro_gui.log`. Stop it with `kill $(lsof -t -iTCP:8765 -sTCP:LISTEN)`.
  Only the Mac running the viewer can start it.
* **Noted units as config files**: start electro_gui with the viewer's folder as a config directory,
  and Views → Config files lists every noted unit, one section per session, titled
  `Unit 4 · NOISE · ACC · ch 22 ★` with the tags and first line of the note as the hint:

  ```bash
  cd ~/code/analysis_imitation
  python -m electro_gui --config-dir output/presentation \
    --config-dir "$HOME/MIT Dropbox/Ruidong Chen/projects/result-viewer/data/electro_gui"
  ```

  The files carry `"generator": "result-viewer"`; the viewer never touches config files without it.

The button's dot is green when electro_gui answers. On the Mac running the viewer it opens the
electro_gui that is running there, on whatever port: the viewer asks the apps dashboard
(`http://127.0.0.1:8790/api/apps`, at most every 10 s) for an app that is up whose process runs
`-m electro_gui`. Only when the dashboard does not answer or sees none does it use `settings.json` →
`electro_gui.url` (default `http://127.0.0.1:8765`), which is also the port it starts one on.
`--apps-dashboard-url ''` turns the lookup off; `--electro-gui-url` overrides both.
`electro_gui.remote_url` is used on other devices (empty = no button there). For the phone, run electro_gui behind Tailscale with a fixed token
and set `remote_url` to match:

```bash
python -m electro_gui --port 8777 --token <secret> --config-dir …   # loopback; the token guards proxied requests
tailscale serve --bg --https=8777 http://127.0.0.1:8777
# settings.json: "remote_url": "https://window.tail62f972.ts.net:8777/?token=<secret>"
```

## iPhone over the tailnet

The server listens on loopback only. `tailscale serve` publishes it to the tailnet (not the internet):

```bash
tailscale serve --bg --https=8766 http://127.0.0.1:8766    # https://window.tail62f972.ts.net:8766/
tailscale serve --https=8766 off                           # to stop publishing
```

Requests through it carry `Tailscale-User-Login`; the server answers only this node's owner (read from
`tailscale status --json`, or `--owner`), only for this node's MagicDNS name or a loopback `Host`, and
refuses cross-site POSTs. A plain local browser (loopback `Host`, no Tailscale header) is always served.

**Home screen.** In Safari on the iPhone open the URL, then Share → Add to Home Screen: it is called
"Rasters", has its own icon (a raster card with a gold star on a blue tile), and opens full screen
without Safari's bars (`apple-mobile-web-app-capable`, `manifest.webmanifest` with
`display: standalone`). The icons are drawn by `scripts/make_icons.py` (needs `rsvg-convert`,
`brew install librsvg`): `icon.svg` and its PNG sizes for iOS (180 px) and Android/Chrome (192, 512,
maskable 512), plus a simpler `favicon.svg` for browser tabs.

Keep the server running with the LaunchAgent (`RunAtLoad` + `KeepAlive`, log in
`~/Library/Logs/result-viewer.log`):

```bash
python3 -m result_viewer launchagent install     # options after `--` go to serve
python3 -m result_viewer launchagent status
python3 -m result_viewer launchagent uninstall
```

It runs this checkout with the Python that installed it; reinstall after moving either. Python code
changes need `launchctl kickstart -k gui/$(id -u)/com.ruidong.result-viewer`; page changes need only a
reload.

## Layout

```
result_viewer/
  catalog.py      sessions, units and figures under the results roots
  notebook.py     <sid>.json read-modify-write, <sid>.md rendering
  electro_gui.py  deep links, config-file mirror, liveness probe
  settings.py     data folder, settings.json
  server.py       HTTP API + figure files, local/tailnet access rules
  launchagent.py  com.ruidong.result-viewer
  cli.py          serve | rebuild | launchagent
  static/         index.html, app.css, app.js, eg-start.html/.js, icons, manifest (no build step)
scripts/make_icons.py   draws icon.svg / favicon.svg and renders the PNGs
tests/            unittest, fake results tree in tests/helpers.py
```
