"""Read and normalize the source PDF into one DocumentPage per physical page.

Extraction uses PyMuPDF text blocks (one block ~ one paragraph/heading), because
pypdf's default mode splits words ("th e", "Kin gsley") on this PDF.

Every page of this LibreTexts PDF ends with a footer like "275 (9.5.1)":
printed page 275, chapter 9, section 9.5, 1st page of that section. The footer
is the trusted source for `chapter`, `section` and `printed_page` metadata.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pymupdf

from .models import DocumentPage

FOOTER_RE = re.compile(r"^(\d+)\s*\((\d+)\.(\d+)(?:\.\d+)?\)$")
HEADING_RE = re.compile(r"^(\d+)\.(\d+):\s*(.+)$")
LICENSE_RE = re.compile(r"^This page titled .* is shared under a CC BY", re.S)
# Sidebar labels the PDF renders as separate blocks; they are not content.
NOISE_BLOCKS = {"Learning Objectives"}
# End-of-chapter sections: study aids / bibliography, not counted as content pages.
END_MATTER = {"Key Terms", "Section Summary", "Section Quiz", "Short Answer",
              "Further Research", "References"}
MIN_CONTENT_CHARS = 200


def normalize_text(text: str) -> str:
    """NFKC-normalize (fixes ligatures/odd spaces) and collapse whitespace inside one block."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")              # soft hyphen
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)           # a block is one paragraph: join its lines
    return text.strip()


def load_pdf(path: Path, document_id: str, source: str,
             first_page: int = 1, last_page: int | None = None
             ) -> tuple[list[DocumentPage], list[dict]]:
    """Extract physical pages first_page..last_page (1-based, inclusive).

    Returns (content_pages, log). `log` has one entry per page with status
    "content" or "excluded" plus the reason, so excluded pages are recorded, not dropped silently.
    """
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path} (see data/manifest.json for the download link)")
    doc = pymupdf.open(path)
    last_page = last_page or doc.page_count
    section_titles: dict[str, str] = {}
    pages: list[DocumentPage] = []
    log: list[dict] = []

    for number in range(first_page, last_page + 1):
        entry: dict = {"page": number}
        try:
            raw_blocks = [b[4] for b in doc[number - 1].get_text("blocks") if b[6] == 0]
        except Exception as exc:  # record and continue; never hide a failed page
            log.append({**entry, "status": "excluded", "reason": f"extraction error: {exc}"})
            continue

        blocks = [normalize_text(b) for b in raw_blocks]
        blocks = [b for b in blocks if b]
        footer = None
        paragraphs: list[str] = []
        for block in blocks:
            match = FOOTER_RE.match(block)
            if match:
                footer = match
            elif block in NOISE_BLOCKS or LICENSE_RE.match(block):
                continue
            else:
                heading = HEADING_RE.match(block)
                if heading and len(block) < 120:
                    section_titles[f"{heading[1]}.{heading[2]}"] = heading[3].strip()
                paragraphs.append(block)

        if footer is None:
            log.append({**entry, "status": "excluded", "reason": "no page footer (cannot trace section)"})
            continue
        printed_page, chapter, sec = int(footer[1]), int(footer[2]), int(footer[3])
        section = f"{chapter}.{sec}"
        title = section_titles.get(section, "")
        text = "\n\n".join(paragraphs)
        entry.update(printed_page=printed_page, chapter=chapter, section=section,
                     section_title=title, chars=len(text))

        is_chapter_cover = footer[0].count(".") == 1  # "(9.1)" = chapter title/TOC page
        if is_chapter_cover:
            reason = "chapter title / table-of-contents page"
        elif title in END_MATTER:
            reason = f"end-of-chapter study aid: {title}"
        elif len(text) < MIN_CONTENT_CHARS:
            reason = f"fewer than {MIN_CONTENT_CHARS} chars after removing footer/license"
        else:
            reason = None

        if reason:
            log.append({**entry, "status": "excluded", "reason": reason})
            continue
        log.append({**entry, "status": "content"})
        pages.append(DocumentPage(
            document_id=document_id, source=source, page=number, text=text,
            metadata={"chapter": chapter, "section": section, "section_title": title,
                      "printed_page": printed_page}))
    doc.close()
    return pages, log
