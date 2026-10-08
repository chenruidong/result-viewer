"""Draw the app icon and render the PNGs the page links to (needs rsvg-convert: brew install librsvg).

    python3 scripts/make_icons.py

Writes result_viewer/static/icon.svg (the source) and apple-touch-icon.png (180 px,
iOS "Add to Home Screen"), icon-192.png, icon-512.png, icon-maskable-512.png
(Android/Chrome manifest; content inside the 80 % safe circle).  The square is
full-bleed: iOS and Android round the corners.  The browser-tab icon is a simpler
mark (the rate peak, the event line and the star's gold), favicon.svg and
favicon-32.png, since the raster ticks turn to noise at 16-32 px.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "result_viewer" / "static"

# A raster: six trials, ticks sparse before the event line, a burst after it.
EVENT_X = 470
ROWS = [  # (colour, tick x positions)
    ("#2f9e44", (250, 322, 404, 494, 519, 546, 577, 640)),  # the star sits over the end of the top rows
    ("#2f9e44", (282, 362, 437, 486, 507, 533, 566, 606, 690)),
    ("#6741d9", (238, 302, 388, 450, 498, 527, 555, 590, 668, 748)),
    ("#6741d9", (266, 342, 420, 490, 514, 541, 572, 621, 690, 790)),
    ("#e03131", (254, 330, 410, 482, 510, 537, 561, 598, 655, 760)),
    ("#e03131", (290, 372, 444, 500, 525, 552, 584, 631, 720, 784)),
]
ROW_Y0, ROW_DY, TICK_W, TICK_H = 270, 54, 17, 40
# Firing rate: flat, a sharp peak just after the event, back to baseline.
PSTH = [(224, 744), (300, 736), (380, 746), (448, 738), (490, 700), (522, 628), (550, 610),
        (582, 638), (622, 694), (680, 724), (740, 732), (800, 728)]


def smooth_path(points):
    """Catmull-Rom through ``points`` as cubic Bezier segments."""
    d = [f"M{points[0][0]},{points[0][1]}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]},{p2[1]}")
    return " ".join(d)


def star(cx, cy, outer, inner):
    import math
    pts = []
    for k in range(10):
        r = outer if k % 2 == 0 else inner
        a = -math.pi / 2 + k * math.pi / 5
        pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    return " ".join(pts)


def svg(scale=1.0):
    """The icon; ``scale`` < 1 shrinks the foreground around the centre (maskable variant)."""
    ticks = []
    for row, (colour, xs) in enumerate(ROWS):
        y = ROW_Y0 + row * ROW_DY - TICK_H / 2
        ticks += [f'<rect x="{x - TICK_W / 2}" y="{y}" width="{TICK_W}" height="{TICK_H}" rx="{TICK_W / 2}" fill="{colour}"/>'
                  for x in xs]
    curve = smooth_path(PSTH)
    area = f"{curve} L800,790 L224,790 Z"
    fg = "\n    ".join([
        # card with a soft shadow
        '<rect x="152" y="196" width="720" height="672" rx="76" fill="#0b1f4d" opacity=".35" filter="url(#blur)"/>',
        '<rect x="152" y="176" width="720" height="672" rx="76" fill="#ffffff"/>',
        # event line, raster, rate curve with its area, axis
        f'<line x1="{EVENT_X}" y1="232" x2="{EVENT_X}" y2="796" stroke="#f59f00" stroke-width="11" stroke-linecap="round"/>',
        *ticks,
        f'<path d="{area}" fill="url(#area)"/>',
        f'<path d="{curve}" fill="none" stroke="#3b5bdb" stroke-width="17" stroke-linecap="round" stroke-linejoin="round"/>',
        '<line x1="224" y1="796" x2="800" y2="796" stroke="#dee2e6" stroke-width="7" stroke-linecap="round"/>',
        # the notebook's star
        '<circle cx="826" cy="206" r="104" fill="#0b1f4d" opacity=".28" filter="url(#blur)"/>',
        '<circle cx="826" cy="198" r="104" fill="url(#gold)" stroke="#ffffff" stroke-width="16"/>',
        f'<polygon points="{star(826, 202, 56, 24)}" fill="#ffffff" stroke="#ffffff" stroke-width="6" stroke-linejoin="round"/>',
    ])
    if scale != 1.0:
        off = 512 * (1 - scale)
        fg = f'<g transform="translate({off:.1f},{off:.1f}) scale({scale})">\n    {fg}\n  </g>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4263eb"/><stop offset=".55" stop-color="#1c7ed6"/><stop offset="1" stop-color="#0c8599"/>
    </linearGradient>
    <radialGradient id="glow" cx=".22" cy=".12" r=".9">
      <stop offset="0" stop-color="#ffffff" stop-opacity=".28"/><stop offset=".6" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#4c6ef5" stop-opacity=".30"/><stop offset="1" stop-color="#4c6ef5" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffd43b"/><stop offset="1" stop-color="#f59f00"/>
    </linearGradient>
    <filter id="blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="22"/></filter>
  </defs>
  <rect width="1024" height="1024" fill="url(#bg)"/>
  <rect width="1024" height="1024" fill="url(#glow)"/>
  {fg}
</svg>
'''


def favicon_svg():
    return '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4263eb"/><stop offset=".55" stop-color="#1c7ed6"/><stop offset="1" stop-color="#0c8599"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="url(#bg)"/>
  <line x1="27" y1="11" x2="27" y2="53" stroke="#ffb703" stroke-width="4" stroke-linecap="round"/>
  <path d="M9,45 C15,43 21,46 25,42 C29,38 30,19 34,19 C38,19 39,37 45,40 C49,42 52,41 55,41" fill="none"
        stroke="#ffffff" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="51" cy="13" r="7.5" fill="#fcc419" stroke="#ffffff" stroke-width="2.5"/>
</svg>
'''


def render(source, out, size):
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(out), str(source)], check=True)


def main():
    if not shutil.which("rsvg-convert"):
        sys.exit("rsvg-convert not found (brew install librsvg)")
    (STATIC / "icon.svg").write_text(svg(), encoding="utf-8")
    for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        render(STATIC / "icon.svg", STATIC / name, size)
    (STATIC / "favicon.svg").write_text(favicon_svg(), encoding="utf-8")
    render(STATIC / "favicon.svg", STATIC / "favicon-32.png", 32)
    with tempfile.TemporaryDirectory() as tmp:
        maskable = Path(tmp) / "maskable.svg"
        maskable.write_text(svg(scale=0.78), encoding="utf-8")
        render(maskable, STATIC / "icon-maskable-512.png", 512)
    for name in ("icon.svg", "apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png",
                 "favicon.svg", "favicon-32.png"):
        print(f"{STATIC / name}  {(STATIC / name).stat().st_size} B")


if __name__ == "__main__":
    main()
