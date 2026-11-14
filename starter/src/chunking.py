"""Chunkers for all strategies.

- fixed_size        A, E  : character windows with overlap, page by page
- by_paragraph      B, C, D, F : whole paragraphs up to max_size, page by page
- by_section        Z     : like by_paragraph, but runs over a whole section, so
                            paragraphs split by a page break are re-joined; each
                            chunk keeps a page range (page .. page_end)

Page-by-page chunkers give every chunk exactly one source page (clean
traceability). Their trade-off: a passage continuing onto the next page is
split. Strategy Z tests what lifting that rule gains and costs.
"""
from __future__ import annotations

import re

from .models import Chunk, DocumentPage

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
HEADING_MAX_CHARS = 80  # short blocks (headings, labels) are glued to the next paragraph
MIN_CHUNK_CHARS = 100   # smaller paragraph chunks are merged into a neighbour
TERMINAL = (".", "?", "!", ":", ")", "\"", "”", "’")

Piece = tuple[str, frozenset]  # (text, pages it comes from)


def _make_chunk(page: DocumentPage, kind: str, index: int, text: str, **extra) -> Chunk:
    return Chunk(
        chunk_id=f"{page.document_id}:p{page.page:04d}:{kind}:{index:03d}",
        document_id=page.document_id, source=page.source, page=page.page, text=text,
        metadata={**page.metadata, "page_end": page.page, **extra})


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Character windows of `size`, each starting `size - overlap` after the previous one.

    Cuts ignore sentence/paragraph boundaries on purpose (that is what A and E test).
    """
    if not 0 <= overlap < size:
        raise ValueError("need 0 <= overlap < size")
    text = re.sub(r"\s+", " ", page.text).strip()  # paragraph breaks carry no meaning here
    step = size - overlap
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        piece = text[start:start + size].strip()
        if piece:
            chunks.append(_make_chunk(page, "fixed" if overlap == 50 else f"fixed{overlap}", len(chunks),
                                      piece, char_start=start, char_end=min(start + size, len(text))))
        if start + size >= len(text):
            break
        start += step
    return chunks


def _split_long(paragraph: str, max_size: int) -> list[str]:
    """Split an oversized paragraph at sentence ends; hard-cut only a single sentence > max_size."""
    parts, current = [], ""
    for sentence in SENTENCE_END.split(paragraph):
        while len(sentence) > max_size:          # pathological long sentence
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:max_size])
            sentence = sentence[max_size:]
        if current and len(current) + 1 + len(sentence) > max_size:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def _group(blocks: list[Piece], max_size: int) -> list[Piece]:
    """Group paragraph blocks into chunks of <= max_size; headings stay with their paragraph."""
    pieces: list[Piece] = []
    for text, pages in blocks:
        parts = _split_long(text, max_size) if len(text) > max_size else [text]
        pieces.extend((p, pages) for p in parts)

    grouped: list[Piece] = []
    current, cur_pages, only_headings = "", frozenset(), True
    for piece, pages in pieces:
        is_heading = len(piece) <= HEADING_MAX_CHARS and not piece.endswith((".", "?", "!", ":", ")"))
        too_big = len(current) + 2 + len(piece) > max_size
        # Start a new chunk at a heading, or when full -- but never leave a heading
        # alone at the end of a chunk (a heading-only buffer always takes the next piece).
        if current and not only_headings and (is_heading or too_big):
            grouped.append((current, cur_pages))
            current, cur_pages, only_headings = "", frozenset(), True
        current = f"{current}\n\n{piece}" if current else piece
        cur_pages = cur_pages | pages
        only_headings = only_headings and is_heading
    if current:
        grouped.append((current, cur_pages))

    # Fragments (a box heading at the page bottom, the tail "2002)." of a sentence
    # from the previous page) are merged into a neighbour.
    merged: list[Piece] = []
    for text, pages in grouped:
        if merged and (len(text) < MIN_CHUNK_CHARS or len(merged[-1][0]) < MIN_CHUNK_CHARS):
            prev_text, prev_pages = merged[-1]
            merged[-1] = (f"{prev_text}\n\n{text}", prev_pages | pages)
        else:
            merged.append((text, pages))
    return merged


def _blocks(page: DocumentPage) -> list[str]:
    return [b.strip() for b in page.text.split("\n\n") if b.strip()]


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group whole paragraphs of ONE page up to `max_size` chars."""
    kind = "para" if max_size == 1000 else f"para{max_size}"
    pages = frozenset({page.page})
    grouped = _group([(b, pages) for b in _blocks(page)], max_size)
    return [_make_chunk(page, kind, i, t) for i, (t, _) in enumerate(grouped)]


def by_section(pages: list[DocumentPage], max_size: int) -> tuple[list[Chunk], int]:
    """Strategy Z: paragraph chunks over whole sections, allowed to cross page breaks.

    A paragraph is re-joined across a page break when the last block of page N does
    not end with terminal punctuation (the sentence obviously continues); a figure
    caption at the top of page N+1 is skipped over when looking for the continuation.
    Returns (chunks, number_of_cross_page_joins).
    """
    sections: list[list[DocumentPage]] = []
    for page in sorted(pages, key=lambda p: p.page):
        last = sections[-1][-1] if sections else None
        if last and last.metadata["section"] == page.metadata["section"] and page.page == last.page + 1:
            sections[-1].append(page)
        else:
            sections.append([page])

    chunks: list[Chunk] = []
    joins = 0
    for section_pages in sections:
        blocks: list[list] = []  # [text, pages]
        for page in section_pages:
            page_blocks = _blocks(page)
            if blocks and page_blocks and not blocks[-1][0].endswith(TERMINAL):
                k = next((i for i, b in enumerate(page_blocks) if not b.startswith("Figure ")), None)
                if k is not None:
                    blocks[-1][0] = f"{blocks[-1][0]} {page_blocks[k]}"
                    blocks[-1][1] = blocks[-1][1] | {page.page}
                    page_blocks = page_blocks[:k] + page_blocks[k + 1:]
                    joins += 1
            blocks.extend([b, frozenset({page.page})] for b in page_blocks)
        first = section_pages[0]
        grouped = _group([(t, frozenset(p)) for t, p in blocks], max_size)
        for i, (text, chunk_pages) in enumerate(grouped):
            start, end = min(chunk_pages), max(chunk_pages)
            page_meta = next(p for p in section_pages if p.page == start).metadata
            chunks.append(Chunk(
                chunk_id=f"{first.document_id}:s{first.metadata['section']}:z:{i:03d}",
                document_id=first.document_id, source=first.source, page=start, text=text,
                metadata={**page_meta, "page_end": end}))
    return chunks, joins
