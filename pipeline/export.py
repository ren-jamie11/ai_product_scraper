"""
Export an extraction run as a PowerPoint deck.

One title slide with the run's name, then one slide per live ASIN:

    +------------------+------------------+
    |   +----------+   |  +-----+-----+   |
    |   |   main   |   |  |  1  |  2  |   |
    |   | 4.8 in sq|   |  +-----+-----+   |
    |   +----------+   |  |  3  |  4  |   |
    |  price           |  +-----+-----+   |
    |  ASIN·brand      |                  |
    |  notes           |                  |
    +------------------+------------------+

Each half of the slide is laid out on its own centre line: the main image and
the text under it on the left, the 2x2 grid of 3.2 in squares on the right.
The main image's top and the text's bottom line up with the grid's edges.

Images are fitted inside their squares, never cropped. A missing or
unreachable image leaves its square blank rather than failing the export.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import requests
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

SLIDE_W, SLIDE_H = 13.333, 7.5
HALF = SLIDE_W / 2
MAIN = 4.8
SMALL = 3.2
TOP = (SLIDE_H - 2 * SMALL) / 2           # grid centred vertically; main image shares its top
BOTTOM = TOP + 2 * SMALL
GRID_LEFT = HALF + (HALF - 2 * SMALL) / 2
MAIN_LEFT = (HALF - MAIN) / 2
TEXT_W = 5.4
TEXT_LEFT = (HALF - TEXT_W) / 2
TEXT_TOP = TOP + MAIN + 0.15
GREY = RGBColor(0x6B, 0x72, 0x80)


def _gallery(p: dict) -> list[str]:
    """Main image then the rest, deduped by file stem — same rule as listingGallery()."""
    seen, out = set(), []
    for url in [p.get("main_image"), *(p.get("images") or [])]:
        if not url:
            continue
        stem = str(url).split("/")[-1].split(".")[0]
        if stem not in seen:
            seen.add(stem)
            out.append(url)
    return out[:5]


def _fetch(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def _place(slide, blob: bytes | None, left: float, top: float, box: float) -> None:
    """Fit the image inside a `box`-inch square at (left, top), centred."""
    if not blob:
        return
    try:
        pic = slide.shapes.add_picture(io.BytesIO(blob), Inches(left), Inches(top))
    except Exception:  # not an image python-pptx can read (e.g. webp)
        return
    k = min(Inches(box) / pic.width, Inches(box) / pic.height)
    pic.width, pic.height = int(pic.width * k), int(pic.height * k)
    pic.left = Inches(left) + (Inches(box) - pic.width) // 2
    pic.top = Inches(top) + (Inches(box) - pic.height) // 2


def _line(frame, text: str, size: int, bold: bool = False, color=None, first: bool = False):
    para = frame.paragraphs[0] if first else frame.add_paragraph()
    run = para.add_run()
    run.text = text
    run.font.size, run.font.bold = Pt(size), bold
    if color is not None:
        run.font.color.rgb = color
    return run


def build_deck(run: dict, meta: dict) -> bytes:
    """`run` from storage.load_run (removed ASINs already gone), `meta` from read_run_meta."""
    products = run.get("products") or []
    notes = meta.get("product_notes") or {}
    domain = run.get("amazon_domain") or "amazon.com"

    urls = [u for p in products for u in _gallery(p)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        blobs = dict(zip(urls, pool.map(_fetch, urls)))

    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)
    blank = deck.slide_layouts[6]

    title = deck.slides.add_slide(blank)
    box = title.shapes.add_textbox(Inches(0.75), Inches(3), deck.slide_width - Inches(1.5), Inches(1.5))
    box.text_frame.word_wrap = True
    _line(box.text_frame, meta.get("label") or run.get("run_id") or "", 40, bold=True, first=True)
    box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER

    for p in products:
        slide = deck.slides.add_slide(blank)
        images = _gallery(p)
        if images:
            _place(slide, blobs.get(images[0]), MAIN_LEFT, TOP, MAIN)
        for i, url in enumerate(images[1:5]):
            _place(slide, blobs.get(url), GRID_LEFT + (i % 2) * SMALL, TOP + (i // 2) * SMALL, SMALL)

        panel = slide.shapes.add_textbox(Inches(TEXT_LEFT), Inches(TEXT_TOP),
                                         Inches(TEXT_W), Inches(BOTTOM - TEXT_TOP))
        frame = panel.text_frame
        frame.word_wrap = True
        frame.margin_top = frame.margin_bottom = 0
        _line(frame, (p.get("price") or {}).get("raw") or "", 20, bold=True, first=True)
        ident = " · ".join(x for x in (p.get("asin"), p.get("brand")) if x)
        link = _line(frame, ident, 10, color=GREY)
        link.hyperlink.address = p.get("link") or f"https://www.{domain}/dp/{p.get('asin')}"
        if notes.get(p.get("asin")):
            _line(frame, notes[p["asin"]], 12)

    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()
