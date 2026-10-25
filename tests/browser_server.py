"""Scratch-only server for browser_smoke.cjs; never reads real results or notes."""
import struct
import zlib
import json
import sys
from pathlib import Path

from result_viewer.catalog import Catalog
from result_viewer.notebook import Notebooks
from result_viewer.server import App, Tailnet, make_server
from tests.helpers import SID, make_session


class FixedTailnet(Tailnet):
    def _read(self):
        return None


def main():
    root = Path(sys.argv[1])
    results, data = root / "results", root / "data"
    data.mkdir(parents=True)
    make_session(results)
    make_session(results, "sub-Test_ses-2026_01_03", "Test_20260103")
    # Valid wide PNG placeholders match a raster's 2.2:1 aspect ratio.
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)
    width, height = 220, 100
    pixels = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
    for p in results.rglob("*"):
        if p.suffix.lower() in (".png", ".jpg"):
            p.write_bytes(png)
    notebooks = Notebooks(data)
    notebooks.update(SID, 1, {"note": "Existing synthetic note", "tags": ["interesting"], "flag": True},
                     {"quality": "sua", "area": "acc"}, ["interesting"])
    app = App(Catalog([results]), notebooks, data, data / "electro_gui",
              FixedTailnet("browser@example.invalid", []), electro_gui_url="http://127.0.0.1:9",
              electro_gui_repo=root / "no-electrogui", apps_dashboard_url="")
    server = make_server(app, "127.0.0.1", 0)
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
