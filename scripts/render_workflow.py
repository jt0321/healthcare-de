"""Render the architecture diagram: workflow.svg and workflow.png.

    uv run --with cairosvg --with pillow python scripts/render_workflow.py

This script is the source of truth for the diagram; the SVG and PNG are build
outputs. Poppins must be installed where fontconfig can find it, because
cairosvg substitutes silently rather than failing when a family is missing:

    curl -sLO https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf
    curl -sLO https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf
    mv Poppins-*.ttf ~/.local/share/fonts/ && fc-cache -f
"""
import pathlib
import subprocess
import html
from functools import lru_cache

from PIL import ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _find_font(style):
    """Ask fontconfig where Poppins lives, so the measured and rendered metrics agree."""
    match = subprocess.run(
        ["fc-match", "-f", "%{file}", f"Poppins:style={style}"],
        capture_output=True, text=True, check=True,
    ).stdout
    if "Poppins" not in match:
        raise SystemExit(f"Poppins ({style}) is not installed - see the module docstring.")
    return match


FONTS = {"bold": _find_font("Bold"), "normal": _find_font("Regular")}


@lru_cache(maxsize=None)
def _font(weight, size):
    return ImageFont.truetype(FONTS[weight], size)


def measure(t, size, weight="normal"):
    return _font(weight, int(size)).getlength(t)


def fit(lines, avail, size, weight="normal", floor=15):
    """Largest size <= `size` at which every line fits in `avail`."""
    while size > floor and max((measure(l, size, weight) for l in lines), default=0) > avail:
        size -= 1
    return size

W, H = 2400, 1400
WHITE, GREY, DIM, FAINT = "#ffffff", "#9a9a9a", "#6a6a6a", "#4f4f4f"
FB = FR = "Poppins"
out = []
add = out.append


def text(x, y, t, size=22, fill=GREY, weight="normal", anchor="start", ls=None):
    extra = f' letter-spacing="{ls}"' if ls else ""
    add(f'<text x="{x}" y="{y}" font-family="{FB}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{extra}>{html.escape(t)}</text>')


def box(x, y, w, h, stroke=WHITE, sw=2.5, r=14):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="none" '
        f'stroke="{stroke}" stroke-width="{sw}"/>')


def path(d, stroke=WHITE, sw=2.5):
    add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>')


def poly(pts, stroke=WHITE, sw=2.5, dash=None, marker=None):
    d = " ".join(f"{p[0]},{p[1]}" for p in pts)
    da = f' stroke-dasharray="{dash}"' if dash else ""
    mk = f' marker-end="url(#{marker})"' if marker else ""
    add(f'<polyline points="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{da}{mk} '
        f'stroke-linejoin="round" stroke-linecap="round"/>')


def arrow(pts, stroke=WHITE, sw=2.5, dash=None):
    poly(pts, stroke, sw, dash, marker="arrow" if stroke == WHITE else "arrowdim")


CARD_PAD_TOP, CARD_LINE, CARD_PAD_BOT = 60, 31, 20


def card_height(lines):
    return CARD_PAD_TOP + CARD_LINE * len(lines) + CARD_PAD_BOT


def card(x, y, w, title, lines, tsize=27, lsize=19, stroke=FAINT):
    h = card_height(lines)
    avail = w - 52
    box(x, y, w, h, stroke=stroke, sw=1.8, r=10)
    text(x + 26, y + 46, title, size=fit([title], avail, tsize, "bold", 20), fill=WHITE, weight="bold")
    lsize = fit(lines, avail, lsize)
    for i, ln in enumerate(lines):
        text(x + 26, y + 84 + i * CARD_LINE, ln, size=lsize)
    return h


def stack(x, w, cards, top, bottom, gap=30, **kw):
    """Lay cards out vertically, centred in [top, bottom]. Returns their rects."""
    total = sum(card_height(c[1]) for c in cards) + gap * (len(cards) - 1)
    y = top + (bottom - top - total) / 2
    rects = []
    for title, lines in cards:
        h = card(x, y, w, title, lines, **kw)
        rects.append((x, y, w, h))
        y += h + gap
    return rects


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add('<defs>'
    '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M 0 1 L 9 5 L 0 9 z" fill="#ffffff"/></marker>'
    '<marker id="arrowdim" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M 0 1 L 9 5 L 0 9 z" fill="#6a6a6a"/></marker>'
    '</defs>')
add(f'<rect width="{W}" height="{H}" fill="#000000"/>')

text(120, 128, "healthcare-de", size=74, fill=WHITE, weight="bold")
text(124, 180, "End-to-End Healthcare Lakehouse", size=31)

C1X, C1W = 120, 320
C2X, C2W = 545, 320
C3X, C3W = 970, 400
C4X, C4W = 1475, 420
C5X, C5W = 2000, 280
TOP, BOT = 350, 1000
INNER_TOP, INNER_BOT = 432, 985

for x, label in ((C1X, "SOURCE SYSTEMS"), (C2X, "INGEST"), (C3X, "LAKEHOUSE"),
                 (C4X, "TRANSFORM"), (C5X, "SERVE")):
    text(x + 2, 300, label, size=19, fill=DIM, weight="bold", ls="4.5")

# --- source systems: three feeds that arrive on their own schedule
src = stack(C1X, C1W, [
    ("EHR extract", ["patients · encounters", "conditions · CSV, nightly"]),
    ("HIE feed", ["FHIR R4 bundles in the", "sender's own id space", "demographics drift"]),
    ("Corrections", ["data/delta/*.csv", "repriced · recoded · voided"]),
], TOP, BOT, gap=44, tsize=28, stroke=WHITE, lsize=18)

text(C1X, 1065, "Run by hand, behind a Compose profile. Airflow never", size=19, fill=DIM)
text(C1X, 1094, "starts them — it only notices what they leave behind.", size=19, fill=DIM)

# --- ingest
box(C2X, TOP, C2W, BOT - TOP)
text(C2X + 26, TOP + 52, "Airflow tasks", size=30, fill=WHITE, weight="bold")
ing = stack(C2X + 22, C2W - 44, [
    ("File sensor", ["watches mtime across", "the shared volume,", "deltas included"]),
    ("Stage ×6", ["every version of a row,", "not just the newest", "Parquet into MinIO", "s3://healthcare/staging/"]),
], INNER_TOP, INNER_BOT)

# bus: the three feeds converge on the sensor
BUS = C1X + C1W + 42
ys = [y + h / 2 for _, y, _, h in src]
for y in ys:
    poly([(C1X + C1W, y), (BUS, y)])
poly([(BUS, ys[0]), (BUS, ys[-1])])
sensor_mid = ing[0][1] + ing[0][3] / 2
poly([(BUS, sensor_mid), (C2X - 46, sensor_mid)])
arrow([(C2X - 46, sensor_mid), (C2X, sensor_mid)])

# --- lakehouse
box(C3X, TOP, C3W, BOT - TOP)
text(C3X + 26, TOP + 52, "Trino · Polaris · Iceberg", size=28, fill=WHITE, weight="bold")
lake = stack(C3X + 22, C3W - 44, [
    ("MERGE INTO", ["patients · encounters", "revision · is_voided", "source_updated_at decides", "voids flagged, never deleted"]),
    ("CREATE OR REPLACE", ["fhir_patients · fhir_encounters", "fhir_conditions · conditions", "full snapshot, no corrections"]),
], INNER_TOP, INNER_BOT)

# fan out of staging into the two load patterns
stage_mid = ing[1][1] + ing[1][3] / 2
FAN = C2X + C2W + 52
poly([(C2X + C2W, stage_mid), (FAN, stage_mid)])
lake_mids = [y + h / 2 for _, y, _, h in lake]
poly([(FAN, min(lake_mids)), (FAN, max(lake_mids))])
for m in lake_mids:
    arrow([(FAN, m), (C3X, m)])

# --- transform
box(C4X, TOP, C4W, BOT - TOP)
text(C4X + 26, TOP + 52, "dbt", size=30, fill=WHITE, weight="bold")
tr = stack(C4X + 22, C4W - 44, [
    ("snapshot", ["scd_patients — type-2 history", "of patient demographics"]),
    ("intermediate — EMPI", ["standardize · block · score", "mutual-best · survivorship"]),
    ("marts", ["dim_patient_master", "fct_patient_encounters", "rpt_identity_conflicts", "rpt_encounter_restatements"]),
], INNER_TOP, INNER_BOT, gap=26)

mid4 = C4X + C4W / 2
arrow([(C3X + C3W, (TOP + BOT) / 2), (C4X, (TOP + BOT) / 2)])
for a, b in zip(tr, tr[1:]):
    arrow([(mid4, a[1] + a[3]), (mid4, b[1])], sw=2)

# --- serve
sh = card_height(["Population", "Clinical · FHIR", "Patient Identity", "Restatements"])
sy = (TOP + BOT) / 2 - sh / 2
card(C5X, sy, C5W, "Streamlit",
     ["Population", "Clinical · FHIR", "Patient Identity", "Restatements"],
     tsize=28, stroke=WHITE)
arrow([(C4X + C4W, (TOP + BOT) / 2), (C5X, (TOP + BOT) / 2)])

# --- orchestration band
BAND = 1175
poly([(C2X + C2W / 2, BAND), (C4X + C4W / 2, BAND)], stroke=DIM, dash="12 12")
for cx in (C2X + C2W / 2, C3X + C3W / 2, C4X + C4W / 2):
    arrow([(cx, BAND), (cx, BOT + 16)], stroke=DIM, dash="12 12")

# pinwheel
px, py, r = 1180, 1278, 36
for rot in (0, 90, 180, 270):
    bx, by = r * 0.5, -r * 0.866  # 60 degrees of arc per blade
    path(f"M 0 0 L 0 -{r} A {r} {r} 0 0 1 {bx:.2f} {by:.2f} Z", sw=2.2)
    out[-1] = out[-1].replace("<path ", f'<path transform="translate({px},{py}) rotate({rot})" ')
text(px + 66, py + 2, "Apache Airflow", size=29, fill=WHITE, weight="bold")
text(px + 66, py + 36, "sensor-driven DAG · one run per batch that lands", size=20)

add("</svg>")

svg = "\n".join(out)
(ROOT / "workflow.svg").write_text(svg)

import cairosvg

cairosvg.svg2png(url=str(ROOT / "workflow.svg"), write_to=str(ROOT / "workflow.png"),
                 output_width=W, output_height=H, background_color="#000000")
print(f"wrote {ROOT / 'workflow.svg'} and {ROOT / 'workflow.png'}")
