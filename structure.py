"""
Hierarchy parser.

Builds a tree:  Chapter > Container (Article/Section/Annex) > Clause > Clause ...

Placement of a clause is decided by LEFT INDENT, not by the shape of its marker.
That matters because these documents use two numbering dialects and the Trading
Book switches between them mid-file:

    onshored CRR   Article 325bd > 1.   > (a) > (i)
    PRA native     chapter 3     > 3.3  > (1) > (a) > (i)

A marker-shape lookup would need to know which dialect it is in, and would still
trip over (i)/(v)/(x) being valid as both letters and numerals. Indent has no
such ambiguity: the ladder is 57.9 / 73.9 / 94.2 / 114.4 in one dialect and
57.9 / 80.6 / 100.8 / 121.1 in the other, and in both cases deeper simply means
further right.
"""

import re

from config import (
    REGEX, CONTAINER_TOKENS, INDENT_TOL,
    CHAPTER_INDENT_MAX, HEADING_INDENT_MAX,
    SUBHEADING_INDENT, BODY_SIZE, SIZE_TOL, SIZE_RATIOS,
)
from textlayer import is_formula
from config import FORMULA, BULLET_CHARS, TABLE

RE_DATE = re.compile(REGEX["date"])
RE_DOTTED = re.compile(REGEX["marker_dotted"])
RE_PAREN = re.compile(REGEX["marker_paren"])
RE_CONTAINER = re.compile(REGEX["container"])
RE_CHAPTER = re.compile(REGEX["chapter"])
RE_NOTE = re.compile(REGEX["note"])


def _normalise(text):
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".")


class Node:
    __slots__ = ("kind", "label", "title", "lines", "children", "parent",
                 "indent", "effective_date", "subheading", "page", "notes")

    def __init__(self, kind, label="", title="", indent=0.0, page=0, parent=None):
        self.kind = kind
        self.label = label
        self.title = title
        self.lines = []
        self.notes = []
        self.children = []
        self.parent = parent
        self.indent = indent
        self.effective_date = ""
        self.subheading = ""
        self.page = page

    def add_child(self, node):
        node.parent = self
        self.children.append(node)
        return node

    def own_text(self):
        """Join this node's own lines.

        Wrapped prose rejoins with a space, but a bulleted item starts a new
        line. Bullets carry no (a)/(i) marker, so without this they would run
        together with the text above them inside the same cell.
        """
        out = []
        for line in self.lines:
            stripped = line.lstrip()
            starts_item = (stripped[:1] in BULLET_CHARS
                           or stripped.startswith(TABLE["placeholder"]))
            if out and not starts_item:
                out[-1] = f"{out[-1]} {line}"
            else:
                out.append(line)
        return "\n".join(out).strip()

    def full_text(self):
        parts = []
        if self.title:
            parts.append(self.title)
        if self.lines:
            parts.append(self.own_text())
        for child in self.children:
            head = f"{child.label} " if child.label else ""
            parts.append((head + child.full_text()).strip())
        for note in self.notes:
            parts.append(note)
        return "\n".join(p for p in parts if p).strip()

    def word_count(self):
        return len(self.full_text().split())

    def __repr__(self):
        return f"<{self.kind} {self.label!r} {self.title[:30]!r} kids={len(self.children)}>"


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

def _is_big_heading(line):
    body = line.get("body_size", BODY_SIZE)
    if line["size"] < body * SIZE_RATIOS["heading_min"] or line["bold"] <= 0.8:
        return False
    # SS13/13 sets rule number "5.1" in 14pt bold, the same as its chapter
    # headings. A line that is nothing but a clause number is a clause.
    if re.fullmatch(r"\d+[A-Za-z]?(?:\.\d+[A-Za-z]?)?\.?", line["text"].strip()):
        return False
    return True


def _opens_container(text):
    m = RE_CONTAINER.match(text)
    return bool(m) and m.group(1) in CONTAINER_TOKENS


def _opens_chapter(text):
    return bool(RE_CHAPTER.match(text))


def _is_subheading(line, margin=None):
    body = line.get("body_size", BODY_SIZE)
    lo = body * SIZE_RATIOS["subheading_min"]
    hi = body * SIZE_RATIOS["heading_min"]
    if line["bold"] < 0.85:
        return False
    if margin is not None and line["x0"] < margin - INDENT_TOL:
        return False
    text = line["text"].strip()
    if lo < line["size"] < hi:            # 13.5pt -- unambiguous
        return True
    if abs(line["size"] - body) < SIZE_TOL:
        # 12pt bold at the heading indent is a minor label ("Formula for
        # Method 1"). Body text at this indent is bold only in patches and
        # ends in sentence punctuation, so require neither.
        return (line["bold"] > 0.95
                and len(text) < 80
                and not text.endswith((".", ";", ",", ":")))
    return False


def _marker(text):
    """Return (label, remainder) if the line opens a numbered clause."""
    m = RE_PAREN.match(text)
    if m:
        return f"({m.group(1)})", text[m.end():].strip()
    m = RE_DOTTED.match(text)
    if m:
        return (m.group(1) or m.group(2)), text[m.end():].strip()
    return None, text


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse(lines, regulation_name="", chapter_numbers=None):
    chapter_numbers = chapter_numbers or {}
    body_x = [l["x0"] for l in lines
              if l["size"] < l.get("body_size", BODY_SIZE) * SIZE_RATIOS["heading_min"]]
    margin = min(body_x) if body_x else None

    root = Node("document", title=regulation_name)
    chapter = root.add_child(Node("chapter", label="", title="(preamble)"))
    container = chapter
    stack = []                 # open clause nodes, outermost first
    since_date = []            # every clause opened since the last date line
    pending_subheading = ""
    prev_was_subheading = False
    last_heading = None        # for merging wrapped titles

    def current():
        return stack[-1] if stack else container

    def close_clauses(date=""):
        # The date applies to every clause opened since the previous date line,
        # not merely to whatever is still on the stack. Where an Article runs
        # (a), (b), (c) ... straight through to a single trailing date, each
        # sibling is popped as the next one opens, so the stack holds only the
        # last of them by the time the date arrives.
        if date:
            if since_date:
                for node in since_date:
                    if not node.effective_date:
                        node.effective_date = date
            elif container is not None and not container.effective_date:
                # Article whose body is unnumbered prose or a bare note; the
                # date belongs to the Article itself.
                container.effective_date = date
        since_date.clear()
        stack.clear()

    for line in lines:
        text = line["text"].strip()
        if not text:
            continue

        # -- wrapped heading continuation -------------------------------
        if (last_heading is not None
                and _is_big_heading(line)
                and abs(line["size"] - last_heading["size"]) < SIZE_TOL
                and line["page"] == last_heading["page"]
                and 0 < line["top"] - last_heading["top"] < line["size"] * 1.8
                and not _opens_container(text)):
            # A wrapped title, not a new heading. Proximity decides this rather
            # than "does the line start with a digit", because a heading can
            # wrap onto a line that does: "5A Corrections to modified duration
            # for debt instruments under Article" / "340 of the Market Risk:
            # Simplified Standardised Approach (CRR) Part".
            last_heading["node"].title = (last_heading["node"].title + " " + text).strip()
            last_heading["top"] = line["top"]
            continue

        # -- chapter heading --------------------------------------------
        # Chapter vs Article is decided by the LEADING TOKEN, not by indent.
        # Rulebook parts distinguish them by 6pt of indent (57.9 vs 63.9), but
        # SS13/13 sets every heading at x=108, so absolute indents do not
        # transfer. In both document classes an Article/Section/Annex heading
        # opens with that word and a chapter heading does not.
        if _is_big_heading(line) and not _opens_container(text):
            close_clauses()
            m = RE_CHAPTER.match(text)
            label, title = (m.group(1), m.group(2)) if m else ("", text)
            if _opens_container(text):
                mc = RE_CONTAINER.match(text)
                label = f"{mc.group(1)} {mc.group(2)}"
                title = mc.group(3)
            chapter = root.add_child(
                Node("chapter", label=label, title=title,
                     indent=line["x0"], page=line["page"]))
            container = chapter
            last_heading = {"node": chapter, "size": line["size"],
                            "page": line["page"], "top": line["top"]}
            pending_subheading = ""
            continue

        # -- article / section / annex heading ---------------------------
        if _is_big_heading(line):
            close_clauses()
            m = RE_CONTAINER.match(text)
            if m:
                label = f"{m.group(1)} {m.group(2)}"
                title = m.group(3)
            else:
                label, title = "", text
            container = chapter.add_child(
                Node("container", label=label, title=title,
                     indent=line["x0"], page=line["page"]))
            last_heading = {"node": container, "size": line["size"],
                            "page": line["page"], "top": line["top"]}
            pending_subheading = ""
            continue

        last_heading = None

        # -- sub-heading (requirement 2) ---------------------------------
        if _is_subheading(line, margin):
            close_clauses()
            if prev_was_subheading and pending_subheading:
                pending_subheading = f"{pending_subheading} {text}"
            else:
                pending_subheading = text
            prev_was_subheading = True
            continue
        prev_was_subheading = False

        # -- effective date terminates the clause ------------------------
        m = RE_DATE.match(text)
        if m:
            close_clauses(m.group(1))
            continue

        # -- formula fragment --------------------------------------------
        if is_formula(line):
            node = current()
            if not node.lines or node.lines[-1] != FORMULA["placeholder"]:
                node.lines.append(FORMULA["placeholder"])
            continue

        # -- editorial note ----------------------------------------------
        if RE_NOTE.match(text):
            current().notes.append(text)
            continue

        # -- numbered clause ----------------------------------------------
        label, remainder = _marker(text)
        if label:
            indent = line["x0"]
            while stack and stack[-1].indent > indent + INDENT_TOL:
                stack.pop()
            if stack and abs(stack[-1].indent - indent) <= INDENT_TOL:
                stack.pop()                      # sibling of the open node
            parent = stack[-1] if stack else container
            node = parent.add_child(
                Node("clause", label=label, indent=indent, page=line["page"]))
            if remainder:
                node.lines.append(remainder)
            if not stack and pending_subheading:
                # Requirement 2: the sub-heading belongs to the clause it
                # introduces, and travels with that clause's cell.
                node.subheading = pending_subheading
                pending_subheading = ""
            stack.append(node)
            since_date.append(node)
            continue

        # -- continuation line --------------------------------------------
        node = current()
        if node.notes:
            node.notes[-1] = (node.notes[-1] + " " + text).strip()
        else:
            node.lines.append(text)

    _restore_chapter_numbers(root, chapter_numbers)
    return backfill_dates(root)


def _restore_chapter_numbers(root, mapping):
    """Fill in chapter numbers that the PDF never wrote.

    Word's automatic list numbering is not emitted into the content stream, so
    headings such as "1 Introduction" arrive as the bare word "Introduction".
    The Contents page does carry every number, so they are matched back by
    title. This has to run after parsing rather than during it, because a
    heading may wrap over two lines ("Material deficiencies in risk capture by
    an institution's internal" + "approach") and the title is only complete once
    both have been joined.
    """
    if not mapping:
        return

    # First pass: exact title match.
    for chapter in root.children:
        if chapter.kind == "chapter" and not chapter.label:
            number = mapping.get(_normalise(chapter.title))
            if number:
                chapter.label = number

    # Second pass: a heading that wrapped with a gap too large for the
    # proximity test ("Alternative definitions of sensitivities in the advanced
    # standardised" / "approach" are 43pt apart, against ~17pt elsewhere). If
    # joining the two titles produces a Contents entry, they were one heading.
    children = root.children
    index = 0
    while index < len(children) - 1:
        node, following = children[index], children[index + 1]
        # A confirmed Contents match is signal enough to merge; the trailing
        # fragment may already have collected the chapter's clauses.
        if (node.kind == "chapter" and following.kind == "chapter"
                and not node.label and not following.label):
            joined = f"{node.title} {following.title}".strip()
            number = mapping.get(_normalise(joined))
            if number:
                node.label = number
                node.title = joined
                node.lines.extend(following.lines)
                node.notes.extend(following.notes)
                for child in following.children:
                    node.add_child(child)
                children.pop(index + 1)
                continue
        index += 1


def backfill_dates(node):
    """Give containers the date of their first dated descendant.

    The effective date is printed after the clause it governs, so it attaches
    to clauses naturally. An Article's own lead-in prose sits above the first
    clause and would otherwise come out dateless, as would an Article whose
    body is a single unnumbered note.
    """
    for child in node.children:
        backfill_dates(child)
    if not node.effective_date:
        for child in node.children:
            if child.effective_date:
                node.effective_date = child.effective_date
                break
    return node


def _propagate_date(node, date):
    if not node.effective_date:
        node.effective_date = date
    for child in node.children:
        _propagate_date(child, date)


def walk_containers(root):
    """Yield (chapter, container) pairs in document order.

    A chapter may hold clauses directly (PRA-native numbering such as 3.3) and
    also hold Articles (onshored CRR numbering). The Trading Book does both, so
    both cases have to be yielded.
    """
    for chapter in root.children:
        if any(c.kind == "clause" for c in chapter.children):
            yield chapter, chapter
        for child in chapter.children:
            if child.kind == "container":
                yield chapter, child
