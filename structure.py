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
    CHAPTER_SIZE_MIN, CHAPTER_INDENT_MAX, HEADING_INDENT_MAX,
    SUBHEADING_SIZE_RANGE, SUBHEADING_INDENT, BODY_SIZE, SIZE_TOL,
)
from textlayer import is_formula
from config import FORMULA

RE_DATE = re.compile(REGEX["date"])
RE_DOTTED = re.compile(REGEX["marker_dotted"])
RE_PAREN = re.compile(REGEX["marker_paren"])
RE_CONTAINER = re.compile(REGEX["container"])
RE_CHAPTER = re.compile(REGEX["chapter"])
RE_NOTE = re.compile(REGEX["note"])


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
        return " ".join(self.lines).strip()

    def full_text(self):
        parts = []
        if self.title:
            parts.append(self.title)
        if self.lines:
            parts.append(" ".join(self.lines))
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
    return line["size"] >= CHAPTER_SIZE_MIN and line["bold"] > 0.8


def _opens_container(text):
    m = RE_CONTAINER.match(text)
    return bool(m) and m.group(1) in CONTAINER_TOKENS


def _opens_chapter(text):
    return bool(RE_CHAPTER.match(text))


def _is_subheading(line):
    lo, hi = SUBHEADING_SIZE_RANGE
    if line["bold"] < 0.85:
        return False
    if abs(line["x0"] - SUBHEADING_INDENT) > INDENT_TOL:
        return False
    text = line["text"].strip()
    if lo < line["size"] < hi:            # 13.5pt -- unambiguous
        return True
    if abs(line["size"] - BODY_SIZE) < SIZE_TOL:
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

def parse(lines, regulation_name=""):
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
                and not _opens_container(text)
                and not _opens_chapter(text)):
            last_heading["node"].title = (last_heading["node"].title + " " + text).strip()
            continue

        # -- chapter heading --------------------------------------------
        if _is_big_heading(line) and line["x0"] <= CHAPTER_INDENT_MAX:
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
            last_heading = {"node": chapter, "size": line["size"]}
            pending_subheading = ""
            continue

        # -- article / section / annex heading ---------------------------
        if _is_big_heading(line) and line["x0"] <= HEADING_INDENT_MAX:
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
            last_heading = {"node": container, "size": line["size"]}
            pending_subheading = ""
            continue

        last_heading = None

        # -- sub-heading (requirement 2) ---------------------------------
        if _is_subheading(line):
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

    return backfill_dates(root)


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
