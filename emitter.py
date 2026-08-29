"""
Row emission and the splitting policy.

Requirement 1, stated precisely:

    Look at a group of siblings, e.g. (a), (b), (c) under Article 350
    paragraph 1. If ANY sibling in that group runs longer than the word
    threshold, then EVERY sibling in the group gets its own row. If none of
    them does, the whole group collapses into the parent's single cell.

So with a threshold of 300, siblings of 90 / 350 / 90 words all split, because
the middle one tripped the threshold and siblings are kept consistent with each
other rather than being judged one at a time. The test is applied independently
at each level, so a group can split while a group nested inside one of its
members stays merged.

Paragraph level is always split regardless of length -- that is the atomic
regulatory unit and the level the effective date attaches to. The word-count
rule governs everything below it. Change ALWAYS_SPLIT_DEPTH to 0 to put
paragraphs under the word rule too.

Headings are not columns. A chapter or Article heading is emitted as its own
row, bold, in the Rule column, at the point where it starts. Sub-headings are
not rows -- they sit inside the cell of the clause they introduce.
"""

from config import SPLIT_WORD_THRESHOLD, HEADING_ROWS, REFERENCE_STYLE

# Depths shallower than this are always split into their own rows.
# Depth 0 = direct children of an Article/chapter, i.e. paragraphs "1." "2."
# Depth 1 = "(a)" "(b)"      Depth 2 = "(i)" "(ii)"
ALWAYS_SPLIT_DEPTH = 1


def _strip_prefix(label):
    """"Article 325" -> "325". Other container words are kept."""
    for prefix in REFERENCE_STYLE["strip_prefixes"]:
        if label.startswith(prefix + " "):
            return label[len(prefix) + 1:].strip()
    return label


def _reference(container_label, path):
    """Render a reference as 325.1, 350.4(b), 104b.2(j)(i), 3.3(1)(a)."""
    sep = REFERENCE_STYLE["separator"]
    tokens = [t for t in ([_strip_prefix(container_label)] + list(path)) if t]
    if not tokens:
        return ""

    out = tokens[0]
    for token in tokens[1:]:
        if token.startswith("(") and token.endswith(")"):
            if REFERENCE_STYLE["attach_parens"]:
                out += token                      # 350.4(b)
            else:
                out += sep + token.strip("()")    # 350.4.b
        else:
            out += sep + token                    # 325.1
    return out


def _should_split(children, depth):
    """Sibling-group decision: one long child splits the whole group."""
    if depth < ALWAYS_SPLIT_DEPTH:
        return True
    if len(children) <= 1:
        return False
    return max(c.word_count() for c in children) > SPLIT_WORD_THRESHOLD


def _row(node, container_label, path, meta, text, subheading=""):
    return {
        "Regulation name": meta.get("part", ""),
        "Regulatory reference": _reference(container_label, path),
        "Rule": text,
        "Published Rule Date": meta.get("printed_on", ""),
        "Effective Date": node.effective_date,
        "BAU Line C": "",
        "_bold": False,
        # The sub-heading is part of the Rule cell, not a column. It is carried
        # here only so the writer knows which leading run to set in bold.
        "_subheading": subheading,
        "_page": node.page,
        "_words": len(text.split()),
    }


def _heading_row(node, meta):
    """A chapter or Article heading, as a bold row in the Rule column."""
    text = f"{node.label} {node.title}".strip()
    return {
        "Regulation name": meta.get("part", ""),
        "Regulatory reference": _reference(node.label, []),
        "Rule": text,
        "Published Rule Date": meta.get("printed_on", ""),
        "Effective Date": "",
        "BAU Line C": "",
        "_bold": True,
        "_subheading": "",
        "_page": node.page,
        "_words": len(text.split()),
    }


def _emit(node, depth, container_label, path, meta, rows):
    children = node.children
    subheading = node.subheading

    if not children or not _should_split(children, depth):
        text = node.full_text()
        if subheading:
            # Requirement 2: the sub-heading lives inside the cell it heads.
            text = f"{subheading}\n{text}".strip()
        if text:
            rows.append(_row(node, container_label, path, meta, text,
                             subheading))
        return

    # Split. The parent's own lead-in ("...subject to the following
    # conditions:") still needs a home, so it becomes the group's first row.
    lead = node.own_text()
    if subheading:
        lead = f"{subheading}\n{lead}".strip()
    if lead:
        rows.append(_row(node, container_label, path, meta, lead, subheading))

    for child in children:
        _emit(child, depth + 1, container_label, path + [child.label],
              meta, rows)

    for note in node.notes:
        rows.append(_row(node, container_label, path, meta, note))


def build_rows(root, meta):
    from structure import walk_containers

    rows = []
    seen_chapters = set()

    for chapter, container in walk_containers(root):
        # Chapter heading row, once, at the point the chapter starts.
        if ("chapter" in HEADING_ROWS
                and id(chapter) not in seen_chapters
                and chapter.title != "(preamble)"
                and (chapter.label or chapter.title)):
            seen_chapters.add(id(chapter))
            rows.append(_heading_row(chapter, meta))

        label = container.label if container is not chapter else ""

        # Article / Section heading row.
        if (container is not chapter
                and "container" in HEADING_ROWS
                and (container.label or container.title)):
            rows.append(_heading_row(container, meta))

        if container.kind == "container" and not container.children:
            # Article whose body is unnumbered prose (Article 5, Article 16),
            # or a Section heading with no body of its own.
            text = "\n".join([container.own_text()] + container.notes).strip()
            if text:
                rows.append(_row(container, label, [], meta, text))
            continue

        clauses = [c for c in container.children if c.kind == "clause"]
        if not clauses:
            continue

        lead = container.own_text()
        if lead:
            rows.append(_row(container, label, [], meta, lead))

        if _should_split(clauses, 0):
            for clause in clauses:
                _emit(clause, 1, label, [clause.label], meta, rows)
        else:
            text = container.full_text()
            if text:
                rows.append(_row(container, label, [], meta, text))

    return rows
