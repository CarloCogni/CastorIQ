# docs/fmp-delivery/tools/docx_redline.py
"""Mark a rebuilt Word document as tracked changes against the team's original.

The review copies show teammates what the V3 rebuild changed. ``mark`` walks
the body of the rebuilt document and the original side by side:

- paragraphs, pictures and tables are aligned in order; an unmatched pair of
  paragraphs whose words are similar enough counts as an edit;
- an edited paragraph gets a word-level redline (``w:ins`` / ``w:del``) that
  keeps the new formatting, colours and links;
- a new paragraph, picture or table is marked inserted; an old one that has
  no counterpart is copied back in at its place and marked deleted.

``accepted_text`` and ``rejected_text`` resolve the marks on a copy, so a build
can prove that accepting everything gives the clean document and rejecting
everything gives the original.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

AUTHOR = "CastorIQ V3 rebuild"
PAIR_CUTOFF = 0.5
FIRST_ID = 10000
PLACEHOLDER_BASE = 0xE000  # private-use code points stand in for hyperlinks
TOKEN = re.compile(r"[\ue000-\uf8ff]|[^\s\ue000-\uf8ff]+\s*|\s+")
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
# CT_Settings children that precede w:trackRevisions.
SETTINGS_BEFORE_TRACK = {
    "writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime",
    "doNotDisplayPageBoundaries", "displayBackgroundShape", "printPostScriptOverText",
    "printFractionalCharacterWidth", "printFormsData", "embedTrueTypeFonts",
    "embedSystemFonts", "saveSubsetFonts", "saveFormsData", "mirrorMargins",
    "alignBordersAndEdges", "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter",
    "gutterAtTop", "hideSpellingErrors", "hideGrammaticalErrors", "activeWritingStyle",
    "proofState", "formsDesign", "attachedTemplate", "linkStyles",
    "stylePaneFormatFilter", "stylePaneSortMethod", "documentType", "mailMerge",
    "revisionView",
}  # fmt: skip


@dataclass
class Unit:
    """One aligned body element: a text paragraph, a picture paragraph or a table."""

    kind: str  # "p", "img", "tbl"
    key: str
    words: list[str]
    element: object


class Revisions:
    """Issues revision marks with unique ids, one author and one date."""

    def __init__(self) -> None:
        self.next_id = FIRST_ID
        self.date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def mark(self, tag: str):
        """A new ``w:ins`` or ``w:del`` element."""
        el = OxmlElement(tag)
        el.set(qn("w:id"), str(self.next_id))
        el.set(qn("w:author"), AUTHOR)
        el.set(qn("w:date"), self.date)
        self.next_id += 1
        return el


class Atoms:
    """Placeholder characters for hyperlinks and footnote references, shared by both documents.

    An atom is diffed as one token and never split; a deleted atom is shown by its
    display text (a hyperlink's label, nothing for a footnote reference).
    """

    def __init__(self) -> None:
        self.by_key: dict[str, str] = {}
        self.display: dict[str, str] = {}

    def placeholder(self, key: str, display: str) -> str:
        """The placeholder character for an atom identified by ``key``."""
        if key not in self.by_key:
            char = chr(PLACEHOLDER_BASE + len(self.by_key))
            self.by_key[key] = char
            self.display[char] = display
        return self.by_key[key]

    def expand(self, text: str) -> str:
        """Replace placeholders with the display text of their atoms."""
        return "".join(self.display.get(ch, ch) for ch in text)


def _atom_key(element) -> tuple[str, str] | None:
    """(key, display) when ``element`` is a hyperlink or a footnote-reference run."""
    if element.tag == qn("w:hyperlink"):
        text = visible_text(element)
        return "link:" + text, text
    if element.tag == qn("w:r"):
        ref = element.find(qn("w:footnoteReference"))
        if ref is not None:
            return "fn:" + ref.get(qn("w:id")), ""
    return None


# --- reading -----------------------------------------------------------------


def visible_text(element) -> str:
    """Concatenated ``w:t`` text under an element."""
    return "".join(t.text or "" for t in element.iter(qn("w:t")))


def units(document) -> list[Unit]:
    """Body elements of a document as alignment units (section properties excluded)."""
    out = []
    for el in document.element.body.iterchildren():
        if el.tag == qn("w:tbl"):
            text = " | ".join(visible_text(tc) for tc in el.iter(qn("w:tc")))
            out.append(Unit("tbl", "tbl:" + text, text.split(), el))
        elif el.tag == qn("w:p"):
            blips = [b.get(qn("r:embed")) for b in el.iter(qn("a:blip"))]
            if blips:
                blobs = b"".join(document.part.related_parts[r].blob for r in blips)
                out.append(Unit("img", "img:" + hashlib.sha1(blobs).hexdigest(), [], el))
            else:
                text = visible_text(el)
                out.append(Unit("p", "p:" + text, text.split(), el))
    return out


# --- alignment ---------------------------------------------------------------


def align(old: list[Unit], new: list[Unit]) -> list[tuple[str, Unit | None, Unit | None]]:
    """Operations (equal, edit, delete, insert) that turn ``old`` into ``new``."""
    ops: list[tuple[str, Unit | None, Unit | None]] = []
    matcher = difflib.SequenceMatcher(
        None, [u.key for u in old], [u.key for u in new], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops += [("equal", old[i1 + k], new[j1 + k]) for k in range(i2 - i1)]
        else:
            ops += _pair_up(old[i1:i2], new[j1:j2])
    return ops


def _similarity(a: Unit, b: Unit) -> float:
    matcher = difflib.SequenceMatcher(None, a.words, b.words, autojunk=False)
    if matcher.real_quick_ratio() <= PAIR_CUTOFF or matcher.quick_ratio() <= PAIR_CUTOFF:
        return 0.0
    return matcher.ratio()


def _pair_up(old: list[Unit], new: list[Unit]) -> list[tuple[str, Unit | None, Unit | None]]:
    """Pair the most similar text paragraphs, recursively on each side of the pair."""
    if not old:
        return [("insert", None, n) for n in new]
    if not new:
        return [("delete", o, None) for o in old]
    best, best_i, best_j = PAIR_CUTOFF, -1, -1
    for i, o in enumerate(old):
        if o.kind != "p" or not o.words:
            continue
        for j, n in enumerate(new):
            if n.kind != "p" or not n.words:
                continue
            score = _similarity(o, n)
            if score > best:
                best, best_i, best_j = score, i, j
    if best_i < 0:
        return [("delete", o, None) for o in old] + [("insert", None, n) for n in new]
    return (
        _pair_up(old[:best_i], new[:best_j])
        + [("edit", old[best_i], new[best_j])]
        + _pair_up(old[best_i + 1 :], new[best_j + 1 :])
    )


# --- marking -----------------------------------------------------------------


def _mark_paragraph_mark(paragraph, revision) -> None:
    """Mark a paragraph's own mark inserted or deleted (first child of pPr/rPr)."""
    ppr = paragraph.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        paragraph.insert(0, ppr)
    rpr = ppr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        ppr.append(rpr)
    rpr.insert(0, revision)


def _wrap_runs(container, revisions: Revisions, tag: str) -> None:
    """Wrap every run under ``container`` in a revision mark of kind ``tag``."""
    for run in list(container.iter(qn("w:r"))):
        parent = run.getparent()
        if parent.tag in (qn("w:ins"), qn("w:del")):
            continue
        wrapper = revisions.mark(tag)
        run.addprevious(wrapper)
        wrapper.append(run)
        if tag == "w:del":
            for t in run.findall(qn("w:t")):
                t.tag = qn("w:delText")


def mark_inserted_paragraph(paragraph, revisions: Revisions) -> None:
    """Mark a whole paragraph (text or picture) as inserted."""
    _wrap_runs(paragraph, revisions, "w:ins")
    _mark_paragraph_mark(paragraph, revisions.mark("w:ins"))


def mark_table(table, revisions: Revisions, tag: str) -> None:
    """Mark every row and every cell paragraph of a table inserted or deleted."""
    for tr in table.iter(qn("w:tr")):
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is None:
            tr_pr = OxmlElement("w:trPr")
            tr.insert(0, tr_pr)
        tr_pr.append(revisions.mark(tag))
        for p in tr.iter(qn("w:p")):
            _wrap_runs(p, revisions, tag)
            _mark_paragraph_mark(p, revisions.mark(tag))


def deleted_copy(element, old_document, new_document, revisions: Revisions):
    """A copy of an old paragraph or table, re-linked into the new package, marked deleted."""
    el = copy.deepcopy(element)
    for tag in ("w:highlight", "w:bookmarkStart", "w:bookmarkEnd"):
        for found in list(el.iter(qn(tag))):
            found.getparent().remove(found)
    _relink(el, old_document, new_document)
    if el.tag == qn("w:tbl"):
        mark_table(el, revisions, "w:del")
    else:
        _wrap_runs(el, revisions, "w:del")
        _mark_paragraph_mark(el, revisions.mark("w:del"))
    return el


def _relink(el, old_document, new_document) -> None:
    """Point pictures and hyperlinks of a copied element at parts of the new package."""
    for blip in el.iter(qn("a:blip")):
        blob = old_document.part.related_parts[blip.get(qn("r:embed"))].blob
        r_id, _ = new_document.part.get_or_add_image(io.BytesIO(blob))
        blip.set(qn("r:embed"), r_id)
    used = [int(d.get("id")) for d in new_document.element.body.iter(qn("wp:docPr"))]
    next_id = max(used, default=0) + 1
    for doc_pr in el.iter(qn("wp:docPr")):
        doc_pr.set("id", str(next_id))
        next_id += 1
    for link in el.iter(qn("w:hyperlink")):
        r_id = link.get(qn("r:id"))
        if r_id:
            url = old_document.part.rels[r_id].target_ref
            link.set(qn("r:id"), new_document.part.relate_to(url, RT.HYPERLINK, is_external=True))


def redline_paragraph(old_p, new_p, atoms: Atoms, revisions: Revisions) -> None:
    """Rewrite ``new_p`` in place as a word-level redline against ``old_p``."""
    pieces, others = _new_pieces(new_p, atoms)
    new_text = "".join(text for text, _ in pieces)
    owners = [item for text, item in pieces for _ in text]
    new_tokens = TOKEN.findall(new_text)
    old_tokens = TOKEN.findall(_old_text(old_p, atoms))
    base = _base_rpr(new_p)

    for child in list(new_p):
        if child.tag != qn("w:pPr"):
            new_p.remove(child)

    starts, position = [], 0
    for token in new_tokens:
        starts.append(position)
        position += len(token)

    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        deleted_text = atoms.expand("".join(old_tokens[i1:i2]))
        if tag in ("delete", "replace") and deleted_text:
            wrapper = revisions.mark("w:del")
            wrapper.append(_text_run(deleted_text, base, deleted=True))
            new_p.append(wrapper)
        if tag in ("equal", "insert", "replace") and j2 > j1:
            begin = starts[j1]
            end = starts[j2 - 1] + len(new_tokens[j2 - 1])
            for element in _emit(new_text, owners, begin, end):
                if tag == "equal":
                    new_p.append(element)
                elif element.tag == qn("w:hyperlink"):
                    _wrap_runs(element, revisions, "w:ins")
                    new_p.append(element)
                else:
                    wrapper = revisions.mark("w:ins")
                    wrapper.append(element)
                    new_p.append(wrapper)
    for other in others:
        new_p.append(other)


def _new_pieces(paragraph, atoms: Atoms):
    """(text, owner element) pieces of a paragraph; each atom becomes one placeholder."""
    pieces, others = [], []
    for child in paragraph:
        if child.tag == qn("w:pPr"):
            continue
        atom = _atom_key(child)
        if atom:
            pieces.append((atoms.placeholder(*atom), child))
        elif child.tag == qn("w:r") and child.find(qn("w:t")) is not None:
            pieces.append((visible_text(child), child))
        else:
            others.append(child)
    return pieces, others


def _old_text(paragraph, atoms: Atoms) -> str:
    """Old paragraph text with atoms replaced by their placeholders."""
    parts = []
    for child in paragraph.iter():
        if child.tag == qn("w:hyperlink"):
            parts.append(atoms.placeholder(*_atom_key(child)))
        elif child.tag == qn("w:footnoteReference"):
            parts.append(atoms.placeholder(*_atom_key(child.getparent())))
        elif child.tag == qn("w:t") and not _inside_hyperlink(child):
            parts.append(child.text or "")
    return "".join(parts)


def _inside_hyperlink(element) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == qn("w:hyperlink"):
            return True
        parent = parent.getparent()
    return False


def _emit(text: str, owners: list, begin: int, end: int) -> list:
    """Elements carrying ``text[begin:end]``: run clones split at owner boundaries."""
    out, position = [], begin
    while position < end:
        owner = owners[position]
        stop = position
        while stop < end and owners[stop] is owner:
            stop += 1
        if _atom_key(owner):
            out.append(copy.deepcopy(owner))
        else:
            out.append(_clone_run(owner, text[position:stop]))
        position = stop
    return out


def _clone_run(run, text: str):
    rpr = run.find(qn("w:rPr"))
    return _text_run(text, rpr if rpr is not None else OxmlElement("w:rPr"), deleted=False)


def _text_run(text: str, rpr, *, deleted: bool):
    run = OxmlElement("w:r")
    run.append(copy.deepcopy(rpr))
    t = OxmlElement("w:delText" if deleted else "w:t")
    t.set(XML_SPACE, "preserve")
    t.text = text
    run.append(t)
    return run


def _base_rpr(paragraph):
    """Run properties for deleted words: the paragraph's first text run, without highlight."""
    for run in paragraph.iter(qn("w:r")):
        if run.find(qn("w:t")) is not None and run.find(qn("w:rPr")) is not None:
            rpr = copy.deepcopy(run.find(qn("w:rPr")))
            for tag in ("w:highlight", "w:rFonts", "w:u", "w:b", "w:bCs"):
                for found in rpr.findall(qn(tag)):
                    rpr.remove(found)
            return rpr
    return OxmlElement("w:rPr")


def enable_tracking(document) -> None:
    """Turn on revision tracking so teammates' own edits are tracked too."""
    settings = document.settings.element
    if settings.find(qn("w:trackRevisions")) is not None:
        return
    track = OxmlElement("w:trackRevisions")
    anchor = None
    for child in settings:
        if child.tag.split("}")[1] in SETTINGS_BEFORE_TRACK:
            anchor = child
    if anchor is None:
        settings.insert(0, track)
    else:
        anchor.addnext(track)


def mark(original, rebuilt) -> dict[str, int]:
    """Turn ``rebuilt`` into a redline against ``original``; return counts per operation."""
    revisions, atoms = Revisions(), Atoms()
    body = rebuilt.element.body
    sect_pr = body.find(qn("w:sectPr"))
    counts = {"equal": 0, "edit": 0, "delete": 0, "insert": 0}
    pending: list = []
    for op, old, new in align(units(original), units(rebuilt)):
        counts[op] += 1
        if op == "delete":
            pending.append(deleted_copy(old.element, original, rebuilt, revisions))
            continue
        for element in pending:
            new.element.addprevious(element)
        pending = []
        if op == "edit":
            redline_paragraph(old.element, new.element, atoms, revisions)
        elif op == "insert" and new.kind == "tbl":
            mark_table(new.element, revisions, "w:ins")
        elif op == "insert":
            mark_inserted_paragraph(new.element, revisions)
    for element in pending:
        sect_pr.addprevious(element)
    enable_tracking(rebuilt)
    return counts


# --- verification -------------------------------------------------------------


def _resolve(body, keep: str, drop: str):
    """Copy of ``body`` with revisions of kind ``drop`` removed and ``keep`` unwrapped."""
    body = copy.deepcopy(body)
    for tr in list(body.iter(qn("w:tr"))):
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is not None and tr_pr.find(qn(drop)) is not None:
            tr.getparent().remove(tr)
    for p in list(body.iter(qn("w:p"))):
        rpr = p.find(qn("w:pPr") + "/" + qn("w:rPr"))
        if rpr is not None and rpr.find(qn(drop)) is not None and p.getparent() is not None:
            p.getparent().remove(p)  # only whole-paragraph revisions mark the paragraph mark
    for el in list(body.iter(qn(drop))):
        if el.getparent() is not None:
            el.getparent().remove(el)
    for el in list(body.iter(qn(keep))):
        parent = el.getparent()
        if parent is None or parent.tag in (qn("w:rPr"), qn("w:trPr")):
            continue
        for child in list(el):
            el.addprevious(child)
        parent.remove(el)
    for t in body.iter(qn("w:delText")):
        t.tag = qn("w:t")
    return body


def body_lines(body) -> list[str]:
    """Paragraph and table-row texts of a body, in order, blank paragraphs skipped."""
    lines = []
    for el in body.iterchildren():
        if el.tag == qn("w:p"):
            text = visible_text(el)
            if text.strip():
                lines.append("P " + text)
        elif el.tag == qn("w:tbl"):
            for tr in el.iter(qn("w:tr")):
                lines.append("R " + " | ".join(visible_text(tc) for tc in tr.iter(qn("w:tc"))))
    return lines


def lines_of(elements: list) -> list[str]:
    """``body_lines`` of loose body elements (copies; the elements stay where they are)."""
    holder = OxmlElement("w:body")
    for element in elements:
        holder.append(copy.deepcopy(element))
    return body_lines(holder)


def accepted_text(document) -> list[str]:
    """Text of the document with every revision accepted."""
    return body_lines(_resolve(document.element.body, keep="w:ins", drop="w:del"))


def rejected_text(document) -> list[str]:
    """Text of the document with every revision rejected."""
    return body_lines(_resolve(document.element.body, keep="w:del", drop="w:ins"))


def check(review, clean, original, review_only: list[str]) -> list[str]:
    """Problems with a redline: accept-all must give ``clean`` plus the review-only lines,
    reject-all must give ``original``, and revision ids must be unique."""
    problems = []
    accepted = accepted_text(review)
    for line in review_only:
        if line in accepted:
            accepted.remove(line)
        else:
            problems.append(f"review-only line missing after accept: {line[:60]}")
    expected = body_lines(clean.element.body)
    if accepted != expected:
        problems += _first_difference("accept-all", expected, accepted)
    rejected = rejected_text(review)
    original_lines = body_lines(original.element.body)
    if rejected != original_lines:
        problems += _first_difference("reject-all", original_lines, rejected)
    ids = [
        el.get(qn("w:id")) for tag in ("w:ins", "w:del") for el in review.element.body.iter(qn(tag))
    ]
    if len(ids) != len(set(ids)):
        problems.append("revision ids are not unique")
    return problems


def _first_difference(label: str, expected: list[str], actual: list[str]) -> list[str]:
    diff = list(difflib.unified_diff(expected, actual, lineterm="", n=0))[2:8]
    return [f"{label} differs: " + line[:100] for line in diff]
