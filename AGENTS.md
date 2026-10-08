# Agent notes for result-viewer

- Python 3.8+ standard library only (the default `python3` here is miniconda 3.8): no `match`,
  no `Path.is_relative_to`, no `str.removeprefix`, no runtime `list[int]` annotations.
- The results roots are read-only. Never write, move or delete anything under
  `/Volumes/*/engaging*results*`; the fetch LaunchAgent owns them.
- Notes live in MIT Dropbox (`projects/result-viewer/data/`), never in this repo. Test against a
  scratch `--data-dir`, never the real notes folder.
- The figure naming contract is `imitation_phys.naming` in analysis_imitation; the electro_gui
  contracts are its `static/core.js` `encodeState` (deep links) and `electro_gui/configs.py`
  (config files). Change `catalog.py` / `electro_gui.py` with those in view.
- The server listens on loopback only; tailnet access goes through `tailscale serve` and the
  owner check in `server.py`. Do not add a LAN/0.0.0.0 mode.
- Run `python3 -m unittest discover -s tests -t .` before committing.
