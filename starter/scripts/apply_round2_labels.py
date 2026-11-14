"""Fill the EMPTY round-2 cells with labelled provenance; never overwrites a human label.

- metadata page_ok      : human labels kept; empty cells <- script (verify_metadata.py)
- metadata chapter_ok / section_ok : script (verify_metadata.py, pypdf)
- disagreements between a human label and the script are written to notes, not overwritten
- test grounding score/reason : Claude LLM-judge labels (results/audit/claude_grounding_labels.json)
- 3_Notes : drafts marked as Claude drafts for the author to rewrite
A `labeled_by` column records the source of every label.
"""
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

STARTER = Path(__file__).resolve().parents[1]
AUDIT = STARTER / "results" / "audit"
WB = AUDIT / "audit_workbook_round2.xlsx"
ORANGE = PatternFill("solid", fgColor="FCE4D6")
WRAP = Alignment(wrap_text=True, vertical="top")
OK, BAD = "✅", "❌"

NOTE_DRAFTS = [
    "[DRAFT by Claude - author must review/rewrite] `chapter` helped filtering: on q20 (chapter 12 context) B, F and "
    "Z ranked two chapter-9 Marx chunks (p288) above the Engels passage; C's chapter filter put p391 at rank 1. "
    "`page` + `printed_page` made every retrieved chunk traceable to the PDF in one step.",
    "[DRAFT by Claude - author must review/rewrite] `page` alone does not locate a sentence on a long page (up to "
    "~5,000 chars). Z's page ranges are sometimes wider than the text: 13 of 46 two-page Z chunks (e.g. q18, "
    "p341-342, text only on p342) because a box heading at a page bottom was treated as an unfinished sentence. "
    "The filter also depends on the user supplying the chapter.",
    "[DRAFT by Claude - author must review/rewrite] q19, strategy A: auto hit@3 = 0 because the quote is cut between "
    "two fixed windows, but read together chunks 2 and 3 contain the full answer, so grounding = 2.",
    "",
]


def main() -> None:
    if any(p.name.startswith("~$") for p in AUDIT.iterdir()):
        raise SystemExit("The workbook seems open in Excel (lock file found). Close it first.")
    script = json.loads((AUDIT / "script_metadata_checks.json").read_text(encoding="utf-8"))
    judge = json.loads((AUDIT / "claude_grounding_labels.json").read_text(encoding="utf-8"))["labels"]
    wb = load_workbook(WB)

    ws = wb["1_Metadata"]
    ws.cell(row=2, column=20, value="labeled_by").font = Font(bold=True)
    ws.column_dimensions["T"].width = 34
    stats = {"human_page_ok": 0, "script_page_ok": 0, "disagreements": []}
    for r in range(3, ws.max_row + 1):
        row_id, reuse = ws.cell(r, 1).value, ws.cell(r, 15).value
        if reuse:
            ws.cell(r, 20, value=f"same as {reuse}")
            continue
        s = script[row_id]
        sources = []
        page_cell = ws.cell(r, 16)
        if page_cell.value in (None, ""):
            page_cell.value = OK if s["page_ok"] else BAD
            sources.append("page_ok: script")
            stats["script_page_ok"] += 1
        else:
            sources.append("page_ok: human")
            stats["human_page_ok"] += 1
            if (page_cell.value == OK) != s["page_ok"]:
                stats["disagreements"].append(row_id)
                note = ws.cell(r, 19)
                msg = (f"SCRIPT DISAGREES with human page_ok={page_cell.value}: {s['note']} - author to review")
                note.value = f"{note.value}; {msg}" if note.value else msg
                note.fill = ORANGE
        for col, key in ((17, "chapter_ok"), (18, "section_ok")):
            cell = ws.cell(r, col)
            if cell.value in (None, ""):
                cell.value = OK if s[key] else BAD
        sources.append("chapter_ok/section_ok: script (pypdf)")
        if s["note"] and not ws.cell(r, 19).value:
            ws.cell(r, 19, value=f"script: {s['note']}")
        ws.cell(r, 20, value="; ".join(sources))

    ws2 = wb["2_Grounding_test"]
    ws2.cell(row=2, column=17, value="labeled_by").font = Font(bold=True)
    ws2.column_dimensions["Q"].width = 26
    filled = 0
    for r in range(3, ws2.max_row + 1):
        row_id, reuse = ws2.cell(r, 1).value, ws2.cell(r, 14).value
        if reuse:
            ws2.cell(r, 17, value=f"same as {reuse}")
            continue
        if ws2.cell(r, 15).value in (None, ""):
            score, reason = judge[row_id]
            ws2.cell(r, 15, value=score)
            ws2.cell(r, 16, value=reason)
            ws2.cell(r, 17, value="Claude (LLM judge)")
            filled += 1
        else:
            ws2.cell(r, 17, value="human")

    ws3 = wb["3_Notes"]
    for i, draft in enumerate(NOTE_DRAFTS, start=2):
        if draft and not ws3.cell(i, 2).value:
            ws3.cell(i, 2, value=draft).fill = ORANGE

    g = wb["0_Guide"]
    for a, b in [("", ""), ("LABEL PROVENANCE", "Added because the audit could not be finished in time. Every label "
                  "has a source in the `labeled_by` column. page_ok: 66 rows labelled by the author (human). "
                  "chapter_ok / section_ok and 1 page_ok: script-verified with pypdf (scripts/verify_metadata.py), "
                  "independent of the PyMuPDF ingestion. Test grounding: Claude as LLM judge "
                  "(results/audit/claude_grounding_labels.json, method inside). Orange cells = disagreement or "
                  "Claude draft to review. These are NOT presented as human labels.")]:
        g.append([a, b])
        g.cell(g.max_row, 2).alignment = WRAP
        g.cell(g.max_row, 1).font = Font(bold=True)
    wb.save(WB)
    print(json.dumps({**stats, "grounding_filled_by_claude": filled}, indent=2))


if __name__ == "__main__":
    main()
