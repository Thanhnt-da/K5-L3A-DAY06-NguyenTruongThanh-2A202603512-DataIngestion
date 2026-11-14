"""Script verification of round-2 metadata checks (NOT human labels).

Independent of the ingestion pipeline (PyMuPDF): re-reads the PDF with pypdf and compares
letters/digits only (pypdf splits words like "th e", so spaces are ignored).

page_ok    : first and last 12 words of the chunk are on the stored page range; for a range,
             the text must START on `page` and END on `page_end` (an over-wide range is wrong).
chapter_ok : the page footer "NNN (ch.sec.x)" printed on the stored page has the stored chapter.
section_ok : the footer has the stored section number AND the stored section title appears as a
             heading "ch.sec: Title" in pypdf's text of that section.
Output: results/audit/script_metadata_checks.json
"""
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader
from openpyxl import load_workbook

STARTER = Path(__file__).resolve().parents[1]
PDF = STARTER.parent / "data" / "Introductory Sociology 3e.pdf"
WB = STARTER / "results" / "audit" / "audit_workbook_round2.xlsx"
OUT = STARTER / "results" / "audit" / "script_metadata_checks.json"


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def main() -> None:
    reader = PdfReader(str(PDF))
    cache: dict[int, str] = {}

    def page_text(n: int) -> str:
        if n not in cache:
            cache[n] = reader.pages[n - 1].extract_text() or ""
        return cache[n]

    def footer(n: int):
        m = re.findall(r"\((\d+)\.(\d+)(?:\.\d+)?\)", page_text(n).replace(" ", ""))
        return (int(m[-1][0]), f"{m[-1][0]}.{m[-1][1]}") if m else (None, None)

    rows = list(load_workbook(WB)["1_Metadata"].iter_rows(min_row=3, values_only=True))
    results = {}
    for r in rows:
        if r[14]:  # reuse row
            continue
        row_id, page, page_end, chapter, section, title, text = r[0], r[6], r[7], r[9], r[10], r[11], r[12]
        w = words(text)
        head, tail = squash(" ".join(w[:12])), squash(" ".join(w[-12:]))
        start_ok = head in squash(page_text(page))
        end_ok = tail in squash(page_text(page_end))
        if page == page_end:
            page_ok = start_ok and end_ok
            why = "" if page_ok else "chunk text not found on stored page"
        else:
            page_ok = start_ok and end_ok
            why = "" if page_ok else ("range wider than text: does not start on page %d" % page if not start_ok
                                      else "range wider than text: does not end on page %d" % page_end)
        f_ch, f_sec = footer(page)
        chapter_ok = f_ch == int(chapter)
        sec_pages = [n for n in range(page - 12, page + 1) if n >= 1]
        heading = squash(f"{section}: {title}")
        title_found = any(heading in squash(page_text(n)) for n in sec_pages)
        section_ok = f_sec == section and title_found
        notes = []
        if why:
            notes.append(why)
        if not chapter_ok:
            notes.append(f"footer says chapter {f_ch}, stored {chapter}")
        if not section_ok:
            notes.append(f"footer section {f_sec}, stored {section}; title heading found: {title_found}")
        results[row_id] = {"page_ok": page_ok, "chapter_ok": chapter_ok, "section_ok": section_ok,
                           "note": "; ".join(notes)}
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    bad = {k: v for k, v in results.items() if not (v["page_ok"] and v["chapter_ok"] and v["section_ok"])}
    print(f"checked {len(results)} rows; problems: {len(bad)}")
    for k, v in bad.items():
        print(" ", k, v)


if __name__ == "__main__":
    sys.exit(main())
