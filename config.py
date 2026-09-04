"""
Universal configuration for PRA Rulebook PDF -> Excel extraction.

Structure in these PDFs is carried by FONT SIZE + LEFT INDENT, not by text
patterns. Regex alone misfires badly (a bare `^\\d+ \\S` chapter matcher fires
127 times in one document, almost all table rows). So the primary signals here
are typographic; regex is used only to read the marker label off a line that
typography has already told us is a new clause.
"""

# --------------------------------------------------------------------------
# Splitting policy  (requirement 1)
# --------------------------------------------------------------------------
# Word-count threshold evaluated across a group of siblings.
# If ANY sibling in the group exceeds this, EVERY sibling in that group is
# emitted as its own row. If none does, the whole group is folded into the
# parent's cell. Applied independently at each level of the tree.
SPLIT_WORD_THRESHOLD = 300

# --------------------------------------------------------------------------
# Typography
# --------------------------------------------------------------------------
# Fallback only. The real body size is measured per document -- PRA Rulebook
# parts set body text at 12pt, but supervisory statements use 11pt, and every
# threshold below is expressed as a RATIO of the measured size rather than an
# absolute. Hardcoding 12.0 made "size != BODY_SIZE" true for every single line
# of an 11pt document, which sent short right-hand fragments to [formula].
BODY_SIZE = 12.0
SIZE_TOL = 0.6          # pt tolerance when comparing font sizes

# Multiples of the measured body size.
SIZE_RATIOS = {
    "heading_min": 1.20,     # 15.8/12 = 1.32, 16/11 = 1.45
    "subheading_min": 1.05,  # 13.5/12 = 1.13
    "script_max": 0.85,      # sub/superscripts
}
INDENT_TOL = 2.5        # pt tolerance when comparing left indents

# Heading tiers: (min_size, max_indent, level_name)
# 15.8pt @ x0~57.9  -> chapter          "2 Level of Application"
# 15.8pt @ x0~63.9  -> article/section  "Article 325bd Liquidity Horizons"
# 13.5pt @ x0~63.9  -> sub-heading      "Reduced version of BA-CVA"
# 12.0pt bold @ x0~63.9 -> sub-heading  "Formula for Method 1"
CHAPTER_INDENT_MAX = 60.0        # x0 <= this and big+bold => chapter
HEADING_INDENT_MAX = 70.0        # x0 <= this and big+bold => article/section
SUBHEADING_INDENT = 63.9

# Tokens that open an article/section-level container.
CONTAINER_TOKENS = (
    "Article", "Section", "Sub-section", "Chapter", "Annex", "Part", "Appendix",
)

# --------------------------------------------------------------------------
# Line-level regexes
# --------------------------------------------------------------------------
REGEX = {
    # bare effective-date line, e.g. "01/01/2027" -- terminates a clause
    "date": r"^(\d{2}/\d{2}/\d{4})$",

    # cover-page metadata
    "printed_on": r"^Printed on:\s*(\d{2}/\d{2}/\d{4})",
    "rulebook_at": r"^Rulebook at:\s*(\d{2}/\d{2}/\d{4})",
    "part_name": r"^Part$",

    # clause markers. `marker_*` groups capture the label only.
    #
    # Two accepted shapes, and the trailing dot is MANDATORY on the short one.
    # Without that, a wrapped continuation line such as
    #     "280f, the hedging set supervisory factor coefficient..."
    # (carried over from "...referred to in Articles 280a to 280f") reads as a
    # brand new clause numbered 280a.
    #   rule number : "3.3"  "2.1A"  "1.10"
    #   paragraph   : "1."   "2A."   "10."
    # The trailing "(?:\s+(?=\S)|\s*$)" also accepts a marker sitting alone on
    # its line with its body starting on the next line, e.g. Article 274 in
    # Counterparty Credit Risk renders paragraph "2A." with nothing after it.
    "marker_dotted": r"^(?:(\d+[A-Za-z]?\.\d+[A-Za-z]?)|(\d+[A-Za-z]?)\.)(?:\s+(?=\S)|\s*$)",
    # parenthesised: "(1)" "(a)" "(i)" "(aa)"
    "marker_paren": r"^\(([0-9]+|[a-z]{1,3}|[ivxlIVXL]{1,6})\)\s*(?=\S)",

    # container heading, e.g. "Article 104a Reassignment of Positions"
    "container": r"^(Article|Section|Sub-section|Chapter|Annex|Part|Appendix)\s+"
                 r"([0-9]+[a-zA-Z]*|[IVXLC]+|[A-Z])\s*(.*)$",
    # plain numbered chapter, e.g. "2A Organisational Structure"
    "chapter": r"^(\d+[A-Z]?)\s+(.*)$",

    # editorial artefacts
    "note": r"^\[Note[:\s]",
    "deleted": r"^\[Deleted\]\s*$",
    "table_caption": r"^Table\s+\d+",
}

# --------------------------------------------------------------------------
# Watermark / artefact suppression  (requirement 3)
# --------------------------------------------------------------------------
# None of the six sample PDFs carry a watermark layer, but stamped copies do.
# Anything matching these is dropped before parsing and never reaches a cell.
WATERMARK = {
    "skip_cover_page": True,      # page 1 is the Bank of England cover sheet
    "drop_non_upright": True,     # rotated text is a stamp, never body copy
    "min_darkness": 0.55,         # drop text lighter than this (0=black, 1=white)
    "content_band": (60.0, 800.0),  # keep only y within this band
    "max_body_size": 20.0,        # nothing legitimate is larger than a chapter head
    "phrases": [                  # literal stamps to drop on sight
        "DRAFT", "CONFIDENTIAL", "SPECIMEN", "DO NOT COPY", "SAMPLE",
    ],
}

# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
# Table content renders as fragments scattered across x positions that are
# nowhere near the clause indent ladder, so it cannot be parsed as prose and
# only pollutes the surrounding rule. Every line falling inside a detected
# table is dropped and replaced by a single placeholder, so the cell still
# records that a table was there.
TABLE = {
    "detect": True,
    "placeholder": "[Table]",
}

# --------------------------------------------------------------------------
# Bullets
# --------------------------------------------------------------------------
# Bulleted items carry no (a)/(i) marker, so they would otherwise be folded
# into the running text of the clause above them. Each one starts a new line
# inside the same cell instead.
BULLET_CHARS = "\u2022\u25aa\u25cf\u25e6\u2023\u2043\u00b7\u2010\u2011\u2012\u2013\u2014\u2015"

# --------------------------------------------------------------------------
# Footnote markers
# --------------------------------------------------------------------------
# A superscript footnote reference ("...consolidation entities.1") is a raised
# digit in a smaller font. Two problems follow if it is left in place: the
# digit lands in the rule text, and the line now carries two font sizes, which
# makes the formula heuristic swallow the whole line. Stripped at char level,
# before any line classification runs.
FOOTNOTE = {
    "strip_markers": True,
    # The footnote block at the foot of the page is dropped as well. On page 4
    # of SS13/13 it reads "1 On 23 February 2017, this SS was updated - see
    # appendix for full details. 2 www.bankofengland.co.uk/..." and was landing
    # in the middle of rule 2.1. It is identified by two conditions together --
    # set smaller than the body text AND sitting in the bottom band of the page
    # -- so small print elsewhere on the page is untouched.
    "drop_block": True,
    "block_top_ratio": 0.75,  # only in the bottom quarter of the page
    "max_size_ratio": 0.85,   # marker is at most this share of the body size
    "min_rise": 0.8,          # and sits at least this many pt above the baseline
}

# --------------------------------------------------------------------------
# Formula / table suppression
# --------------------------------------------------------------------------
# Formula blocks explode into dozens of fragments at scattered x positions with
# 8.5pt subscripts and stray glyphs. Left alone they poison the rule text.
FORMULA = {
    "glyphs": "∑∏√⎷⎛⎞⎝⎠∣⋅×÷≤≥≠≈±∞ωαβγδσρμΣΠ",
    "placeholder": "[formula]",
    "min_glyph_ratio": 0.12,      # line is a formula if this share is math glyphs
}

# --------------------------------------------------------------------------
# Regulatory reference formatting
# --------------------------------------------------------------------------
# Internally a reference is a container label plus a path of clause markers,
# e.g. ("Article 325", ["1"]) or ("Article 350", ["4", "(b)"]). These settings
# control how that is rendered into the cell.
#
#   Article 325 1        ->  325.1
#   Article 350 4 (b)    ->  350.4(b)
#   Article 104b 2 (j) (i) -> 104b.2(j)(i)
#   3.3 (1) (a)          ->  3.3(1)(a)
#   Annex 1 2            ->  Annex 1.2
#   Section 6            ->  Section 6
REFERENCE_STYLE = {
    # Drop the leading "Article " so the number carries the reference alone.
    # Only Article is stripped: Section, Annex, Part and Chapter keep their
    # word, because a bare "6" would collide with chapter 6.
    "strip_prefixes": ("Article",),
    # Separator between numeric levels.
    "separator": ".",
    # Parenthesised markers attach directly with no separator: 325.4(b).
    # Set False to render them as 325.4.b instead.
    "attach_parens": True,
}

# --------------------------------------------------------------------------
# Excel output
# --------------------------------------------------------------------------
COLUMNS = [
    "Regulation name",
    "Regulatory reference",
    "Rule",
    "Published Rule Date",
    "Effective Date",
    "BAU Line C",
]

# Headings are not columns. They are emitted as their own bold row in the Rule
# column at the point in the document where they start.
#   "chapter"   -> "2 Level of Application"
#   "container" -> "Article 350 Specific Methods for CIUs", "Section 3 Equities"
# Drop "container" from this tuple to keep chapter headings only.
HEADING_ROWS = ("chapter", "container")

STYLE = {
    "header_fill": "00AEEF",
    "bau_fill": "6BA539",
    "header_font": ("Calibri", 11, True, "FFFFFF"),
    "body_font": ("Calibri", 10, False, "000000"),
    "heading_font": ("Calibri", 10, True, "000000"),
    "border": "BFBFBF",
    "widths": {"A": 28, "B": 24, "C": 100, "D": 16, "E": 14, "F": 14},
    "header_height": 30,
}
