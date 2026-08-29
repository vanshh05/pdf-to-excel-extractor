"""
Text layer reconstruction.

pdfplumber's extract_text() is not usable on these PDFs for two reasons:

1. Every defined term is a bold hyperlink and the space around it is rendered as
   a positioning offset rather than a space glyph. extract_text() returns
   "A firmmust", "the PRAin writing", "of 3.2and confirm". Real gaps measure
   ~3.37pt against an Arial-12 space width of 3.34pt, while intra-word gaps are
   ~0.10pt, so a gap threshold cleanly recovers the spaces.

2. extract_text() sorts characters by x position. Where a PDF stamps one text
   run on top of another at the same y, the two interleave character by
   character. Reading page.chars in DOCUMENT order keeps the runs separate.

This module therefore builds lines from raw chars, and carries font size, weight
and indent forward so the parser can use typography instead of guessing.
"""

import collections
import re

import pdfplumber

from config import WATERMARK, FORMULA, BODY_SIZE

# Gap wider than this fraction of the font size means a missing space.
# Measured separation: real spaces ~0.28*size, intra-word ~0.01*size.
SPACE_GAP_RATIO = 0.15

# Vertical movement that starts a new line.
LINE_BREAK_DY = 3.0

# Backwards x jump that starts a new line (a carriage return within a run).
LINE_BREAK_DX = 15.0

_GLYPHS = set(FORMULA["glyphs"])


def _darkness(char):
    """0.0 = black, 1.0 = white. Used to drop faint watermark text."""
    col = char.get("non_stroking_color")
    if col is None:
        return 0.0
    if isinstance(col, (int, float)):
        return float(col)
    try:
        vals = [float(v) for v in col]
    except (TypeError, ValueError):
        return 0.0
    if not vals:
        return 0.0
    if len(vals) == 4:  # CMYK - K channel dominates darkness
        return 1.0 - min(1.0, vals[3] + max(vals[:3]))
    return sum(vals) / len(vals)


def _is_watermark_char(char):
    """Requirement 3: watermark and stamp characters never reach a cell."""
    if WATERMARK["drop_non_upright"] and not char.get("upright", True):
        return True
    if _darkness(char) > WATERMARK["min_darkness"]:
        return True
    top = char["top"]
    lo, hi = WATERMARK["content_band"]
    if top < lo or top > hi:
        return True
    if char["size"] > WATERMARK["max_body_size"]:
        return True
    return False


def _join(chars):
    """Concatenate chars, reinserting spaces lost at font-run boundaries."""
    out = []
    prev = None
    for c in chars:
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            if (gap > SPACE_GAP_RATIO * max(c["size"], 1.0)
                    and out and not out[-1].isspace()
                    and not c["text"].isspace()):
                out.append(" ")
        out.append(c["text"])
        prev = c
    return "".join(out)


def _line_record(chars, page_no):
    text = _join(chars).strip()
    if not text:
        return None

    visible = [c for c in chars if c["text"].strip()]
    if not visible:
        return None

    sizes = collections.Counter(round(c["size"], 1) for c in visible)
    dominant_size = sizes.most_common(1)[0][0]
    body = [c for c in visible if abs(c["size"] - dominant_size) < 0.6]

    bold = sum(1 for c in body if "Bold" in c["fontname"]) / max(len(body), 1)
    glyph_ratio = sum(1 for c in visible if c["text"] in _GLYPHS) / len(visible)
    has_script = any(c["size"] < FORMULA["subscript_size_max"] for c in visible)

    families = collections.Counter(c["fontname"] for c in body)

    return {
        "text": text,
        "page": page_no,
        "size": dominant_size,
        "font": families.most_common(1)[0][0],
        "bold": bold,
        "x0": round(min(c["x0"] for c in chars), 1),
        "x1": round(max(c["x1"] for c in chars), 1),
        "top": round(min(c["top"] for c in chars), 1),
        "glyph_ratio": glyph_ratio,
        "has_script": has_script,
        "size_spread": len(sizes),
    }


def page_lines(page):
    """Split one page's chars into line records, in document order."""
    chars = [c for c in page.chars if not _is_watermark_char(c)]
    if not chars:
        return []

    groups = []
    current = []
    last_top = None
    last_x1 = None

    for c in chars:
        top = c["top"]
        breaks = (
            last_top is None
            or abs(top - last_top) > LINE_BREAK_DY
            or (last_x1 is not None and c["x0"] < last_x1 - LINE_BREAK_DX)
        )
        if breaks and current:
            groups.append(current)
            current = []
        current.append(c)
        last_top = top
        last_x1 = c["x1"]

    if current:
        groups.append(current)

    records = []
    for g in groups:
        rec = _line_record(g, page.page_number)
        if rec and not _is_stamp_text(rec["text"]):
            records.append(rec)
    return records


def _is_stamp_text(text):
    upper = text.strip().upper()
    return any(p in upper and len(upper) <= len(p) + 6
               for p in WATERMARK["phrases"])


def is_formula(line):
    """Formula fragments must not be concatenated into rule text."""
    if line["glyph_ratio"] >= FORMULA["min_glyph_ratio"]:
        return True
    # A short fragment carrying subscripts and sitting far from any indent
    # ladder position is part of a rendered equation.
    if line["has_script"] and line["size_spread"] > 1 and len(line["text"]) < 60:
        return True
    if line["x0"] > 150 and len(line["text"]) < 25 and line["size"] != BODY_SIZE:
        return True
    return False


def is_content_heading(line):
    """A real structural heading, as opposed to cover-sheet branding.

    The cover sets its own titles in Calibri-Bold; every heading in the body of
    the document is Arial-Bold. Page 1 carries the cover block AND the opening
    of chapter 1, so the cover has to be cut at the first Arial-Bold heading
    rather than by dropping the page. Cutting here also discards the "Chapters"
    table of contents, whose entries would otherwise parse as clauses.
    """
    return (line["size"] >= 15.0
            and "Bold" in line["font"]
            and "Calibri" not in line["font"])


def document_lines(pdf_path, skip_cover=True):
    """Yield line records for the whole document, cover block excluded."""
    started = not skip_cover
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages):
            for line in page_lines(page):
                if not started:
                    if index > 0 or is_content_heading(line):
                        started = True
                    else:
                        continue
                yield line


def cover_metadata(pdf_path):
    """Read the part name and the two dates off the cover sheet."""
    meta = {"part": "", "printed_on": "", "rulebook_at": ""}
    with pdfplumber.open(pdf_path) as pdf:
        lines = [l["text"] for l in page_lines(pdf.pages[0])]

    for i, text in enumerate(lines):
        m = re.match(r"^Printed on:\s*(\d{2}/\d{2}/\d{4})", text)
        if m:
            meta["printed_on"] = m.group(1)
        m = re.match(r"^Rulebook at:\s*(\d{2}/\d{2}/\d{4})", text)
        if m:
            meta["rulebook_at"] = m.group(1)
        if text.strip() == "Part" and i + 1 < len(lines):
            meta["part"] = lines[i + 1].strip()

    return meta
