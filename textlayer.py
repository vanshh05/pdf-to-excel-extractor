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

from config import (WATERMARK, FORMULA, BODY_SIZE, TABLE, FOOTNOTE,
                    SIZE_RATIOS, INDENT_TOL)

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


def _strip_footnote_markers(chars):
    """Drop superscript footnote references from a page's character stream.

    A footnote marker is a raised digit set smaller than the surrounding text,
    e.g. the "1" in "...aimed at CRR firms and CRR consolidation entities.1".

    This runs on the raw stream, BEFORE lines are grouped, and that ordering is
    the whole point. A superscript is raised by roughly the same amount as
    LINE_BREAK_DY, so if it survives to the grouping stage it is split off as a
    line of its own, and a lone small digit sitting at the right-hand end of a
    line then trips the formula heuristic and renders as "[formula]". It also
    drags the baseline upward, which corrupts the grouping of the line after it.

    Only digits are removed, and only where they are both smaller than the body
    text and raised above the running baseline, so subscripted variables inside
    real equations are left alone.
    """
    if not FOOTNOTE["strip_markers"] or not chars:
        return chars

    visible = [c for c in chars if c["text"].strip()]
    if len(visible) < 2:
        return chars

    sizes = collections.Counter(round(c["size"], 1) for c in visible)
    body_size = sizes.most_common(1)[0][0]
    if len(sizes) == 1:
        return chars

    max_size = body_size * FOOTNOTE["max_size_ratio"]
    rise = FOOTNOTE["min_rise"]

    # Measure the rise from the BASELINE, not from the glyph's top edge.
    # "top" is the top of the bounding box, and for a smaller glyph that is
    # raised the two effects very nearly cancel: in SS13/13 an 8pt footnote
    # marker beside 11pt body text sits only 0.11pt higher by "top", but 3.11pt
    # higher by "bottom". Using "top" makes the marker undetectable.
    kept = []
    baseline = None
    for c in chars:
        is_body = abs(c["size"] - body_size) < 0.6
        if (not is_body
                and c["text"].isdigit()
                and c["size"] <= max_size
                and baseline is not None
                and baseline - c["bottom"] >= rise):
            continue
        if is_body:
            baseline = c["bottom"]
        kept.append(c)
    return kept or chars


def _line_record(chars, page_no, body_size=BODY_SIZE):
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
    script_max = body_size * SIZE_RATIOS["script_max"]
    has_script = any(c["size"] < script_max for c in visible)

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
        "body_size": body_size,
    }


def detect_body_size(pdf, sample_pages=8):
    """Measure the document's body font size.

    Rulebook parts set body text at 12pt; supervisory statements use 11pt.
    Every size threshold is a ratio of this, so it must be measured rather than
    assumed -- a hardcoded 12.0 makes "size != BODY_SIZE" true for every line of
    an 11pt document.
    """
    counts = collections.Counter()
    pages = pdf.pages[:sample_pages] if len(pdf.pages) > sample_pages else pdf.pages
    for page in pages:
        for c in page.chars:
            if c["text"].strip():
                counts[round(c["size"], 1)] += 1
    return counts.most_common(1)[0][0] if counts else BODY_SIZE


def _table_boxes(page):
    """Bounding boxes of tables on this page."""
    if not TABLE["detect"]:
        return []
    try:
        return [t.bbox for t in page.find_tables()]
    except Exception:
        return []


def _in_table(char, boxes):
    for x0, top, x1, bottom in boxes:
        if x0 <= char["x0"] <= x1 and top <= char["top"] <= bottom:
            return True
    return False


def page_lines(page, body_size=None):
    """Split one page's chars into line records, in document order.

    Table content is dropped and replaced by a single placeholder line. Table
    cells render as fragments at x positions nowhere near the clause indent
    ladder, so they cannot be parsed as prose and would otherwise be spliced
    into the surrounding rule as noise.
    """
    boxes = _table_boxes(page)
    chars = _strip_footnote_markers(
        [c for c in page.chars if not _is_watermark_char(c)])
    if boxes:
        kept = [c for c in chars if not _in_table(c, boxes)]
        dropped = len(chars) - len(kept)
        chars = kept
    else:
        dropped = 0
    if not chars:
        return _table_placeholder(page, boxes) if dropped else []

    if body_size is None:
        sizes = collections.Counter(round(c["size"], 1) for c in chars
                                    if c["text"].strip())
        body_size = sizes.most_common(1)[0][0] if sizes else BODY_SIZE

    groups = []
    current = []
    last_top = None
    last_x1 = None

    for c in chars:
        # Compare against the last body-sized character. A superscript or
        # subscript is raised or dropped by about LINE_BREAK_DY, so measuring
        # from it would split one visual line into several.
        is_body = abs(c["size"] - body_size) < 0.6
        top = c["top"]
        breaks = (
            last_top is None
            or (is_body and abs(top - last_top) > LINE_BREAK_DY)
            or (last_x1 is not None and c["x0"] < last_x1 - LINE_BREAK_DX)
        )
        if breaks and current:
            groups.append(current)
            current = []
        current.append(c)
        if is_body or last_top is None:
            last_top = top
        last_x1 = c["x1"]

    if current:
        groups.append(current)

    records = []
    for g in groups:
        rec = _line_record(g, page.page_number, body_size)
        if rec and not _is_stamp_text(rec["text"]):
            records.append(rec)

    records = _drop_footnote_block(records, page, body_size)

    if dropped:
        for holder in _table_placeholder(page, boxes):
            index = next((i for i, r in enumerate(records)
                          if r["top"] > holder["top"]), len(records))
            records.insert(index, holder)
    return records


def _drop_footnote_block(records, page, body_size):
    """Remove the footnote block printed at the foot of the page.

    Footnote bodies are set smaller than the running text and sit below the
    last line of it. Left in, they are spliced into whichever rule happens to
    be running across the page break -- on page 4 of SS13/13 the note
    "1 On 23 February 2017, this SS was updated..." landed in the middle of
    rule 2.1.

    Both conditions are required: smaller than body AND in the bottom band of
    the page. Size alone would catch small print anywhere, and position alone
    would catch the last ordinary line on the page.
    """
    if not FOOTNOTE.get("drop_block") or not records:
        return records

    cutoff = page.height * FOOTNOTE["block_top_ratio"]
    max_size = body_size * FOOTNOTE["max_size_ratio"]

    start = None
    for index, rec in enumerate(records):
        if rec.get("is_table"):
            continue
        if rec["size"] <= max_size and rec["top"] >= cutoff:
            start = index
            break

    if start is None:
        return records
    # Everything from the first footnote line to the end of the page is part of
    # the block.
    return records[:start]


def _table_placeholder(page, boxes):
    """One marker line per table, so the cell records that a table was here."""
    out = []
    for x0, top, x1, bottom in boxes:
        out.append({
            "text": TABLE["placeholder"],
            "page": page.page_number,
            "size": BODY_SIZE,
            "font": "ArialMT",
            "bold": 0.0,
            "x0": 73.9,
            "x1": x1,
            "top": top,
            "glyph_ratio": 0.0,
            "has_script": False,
            "size_spread": 1,
            "body_size": BODY_SIZE,
            "is_table": True,
        })
    return out


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
    body = line.get("body_size", BODY_SIZE)
    if (line["x0"] > 150 and len(line["text"]) < 25
            and abs(line["size"] - body) > 0.6):
        return True
    return False


TOC_TITLES = ("contents", "table of contents")

# "5A Corrections to modified duration ... Part 11" -> ("5A", "Corrections ...")
TOC_ENTRY = re.compile(r"^(\d+[A-Za-z]?)\s+(.+?)\s+(\d{1,3})\s*$")


def normalise_title(text):
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".")


def parse_contents(pdf, body_size):
    """Read the Contents page into {normalised title: chapter number}.

    Some documents number their chapters with Word's automatic list numbering,
    which is never written to the PDF content stream. In SS13/13 the heading on
    page 4 consists of the thirteen characters "Introduction" and nothing else --
    the "1" simply does not exist as text, so no extractor can read it off the
    page. The Contents page does carry every number, including 3A, 5A and 9A,
    so the numbers are restored from there by matching on title.

    Returns (mapping, last_page_of_contents). The page index is used to skip the
    cover and contents block, which would otherwise be parsed as rules.
    """
    mapping = {}
    toc_page = None
    heading_min = body_size * SIZE_RATIOS["heading_min"]

    for index, page in enumerate(pdf.pages[:12]):
        lines = page_lines(page, body_size)
        if not any(l["size"] >= heading_min and l["bold"] > 0.8
                   and normalise_title(l["text"]) in TOC_TITLES for l in lines):
            continue

        toc_page = index
        base_x = min((l["x0"] for l in lines if l["size"] < heading_min),
                     default=None)
        entries = []
        for line in lines:
            if line["size"] >= heading_min:
                continue
            if base_x is not None and line["x0"] > base_x + INDENT_TOL and entries:
                entries[-1] += " " + line["text"]      # wrapped entry
            else:
                entries.append(line["text"])

        for entry in entries:
            m = TOC_ENTRY.match(entry.strip())
            if m:
                mapping[normalise_title(m.group(2))] = m.group(1)
        break

    return mapping, toc_page


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
        body_size = detect_body_size(pdf)
        _, toc_page = parse_contents(pdf, body_size)

        for index, page in enumerate(pdf.pages):
            # A dedicated Contents page means the cover and contents run to the
            # end of it; content starts on the next page. Without one, fall back
            # to cutting at the first real heading, because the Rulebook parts
            # put the cover and the opening of chapter 1 on the same page.
            if skip_cover and toc_page is not None and index <= toc_page:
                continue
            for line in page_lines(page, body_size):
                if not started:
                    if toc_page is not None or index > 0 or is_content_heading(line):
                        started = True
                    else:
                        continue
                yield line


def contents_map(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        mapping, _ = parse_contents(pdf, detect_body_size(pdf))
    return mapping


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
