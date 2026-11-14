"""Manual-audit workbooks (Excel). The script fills context columns; the HUMAN fills labels.

Round 1 (before test): coherence (all strategies) + dev grounding for the tied strategies.
Labels are never filled in by code; reuse rows only *display* the referenced row's label.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .evaluation import match_key

GREY = PatternFill("solid", fgColor="E7E6E6")
YELLOW = PatternFill("solid", fgColor="FFF2CC")
GREEN = PatternFill("solid", fgColor="E2EFDA")
HEAD = PatternFill("solid", fgColor="1F4E78")
NOTE = PatternFill("solid", fgColor="DDEBF7")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

COHERENCE_RUBRIC = ("0 = cut mid-idea / missing its subject, cannot be understood alone\n"
                    "1 = partly understandable, missing needed context\n"
                    "2 = understandable on its own, the idea is complete")
GROUNDING_RUBRIC = ("Read the 3 chunks TOGETHER.\n0 = no evidence for the answer in the top-3\n"
                    "1 = evidence present but a needed condition/context is missing\n"
                    "2 = enough to answer correctly AND trace it to the source page")


# ------------------------------------------------------------------ anchors
def choose_anchors(pages: list[dict], n: int = 10) -> list[dict]:
    """Fixed rule: content pages sorted by page number; take indices round(i * N / n), i = 0..n-1.
    On each, the anchor is the first sentence (>= 60 chars) of the first paragraph >= 150 chars
    that is not a figure caption or the 'By the end of this section' objectives line."""
    pages = sorted(pages, key=lambda p: p["page"])
    anchors = []
    for i in range(n):
        page = pages[round(i * len(pages) / n)]
        for block in page["text"].split("\n\n"):
            if len(block) < 150 or block.startswith(("Figure ", "By the end of this section", "•")):
                continue
            sentence = next((s for s in SENTENCE_END.split(block) if len(s) >= 60), None)
            if sentence:
                anchors.append({"anchor_id": f"c{i + 1:02d}", "page": page["page"],
                                "printed_page": page["printed_page"], "chapter": page["chapter"],
                                "section": page["section"], "anchor_sentence": sentence})
                break
    return anchors


def locate(anchor: dict, chunks: list[dict]) -> tuple[dict | None, str]:
    """Chunk holding the anchor: first chunk containing the whole sentence; otherwise the chunk
    containing its first 8 words (the sentence was cut)."""
    key = match_key(anchor["anchor_sentence"])
    on_page = [c for c in chunks if c["page"] <= anchor["page"] <= c["page_end"]]
    for c in on_page:
        if key in match_key(c["content"]):
            return c, "whole sentence in chunk"
    start = " ".join(key.split()[:8])
    for c in on_page:
        if start in match_key(c["content"]):
            return c, "sentence CUT: only its start is in this chunk"
    return None, "not found"


# ------------------------------------------------------------------ sheet helpers
def _header(ws, columns: list[tuple[str, int, str, str]], banner: str) -> None:
    """columns: (name, width, kind 'auto'|'input', comment)."""
    ws.append([banner])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    ws["A1"].fill, ws["A1"].alignment = NOTE, WRAP
    ws["A1"].font = Font(bold=True, size=11)
    ws.row_dimensions[1].height = 95
    ws.append([c[0] for c in columns])
    for idx, (name, width, kind, comment) in enumerate(columns, start=1):
        cell = ws.cell(row=2, column=idx)
        cell.fill, cell.font, cell.alignment, cell.border = HEAD, Font(bold=True, color="FFFFFF"), WRAP, BORDER
        ws.column_dimensions[get_column_letter(idx)].width = width
        cell.comment = Comment(f"{'YOU fill this' if kind == 'input' else 'Filled by script - do not edit'}.\n{comment}",
                               "audit", width=320, height=160)
    ws.freeze_panes = "A3"


def _style_rows(ws, columns, reuse_col: str | None) -> None:
    names = [c[0] for c in columns]
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        reuse = reuse_col and row[names.index(reuse_col)].value
        for cell, (_, _, kind, _) in zip(row, columns):
            cell.alignment, cell.border = WRAP, BORDER
            cell.fill = GREEN if reuse else (YELLOW if kind == "input" else GREY)


def _validate(ws, col_letter: str, options: str, prompt: str) -> None:
    dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True, showErrorMessage=True,
                        errorTitle="Invalid value", error=f"Choose one of: {options}",
                        promptTitle="Score", prompt=prompt, showInputMessage=True)
    ws.add_data_validation(dv)
    dv.add(f"{col_letter}3:{col_letter}{max(ws.max_row, 3)}")


def _guide(wb: Workbook, round_no: int, anchor_rule: str, tied: list[str]) -> None:
    ws = wb.active
    ws.title = "0_Guide"
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    rows = [
        ("AUDIT WORKBOOK", f"Round {round_no} - manual labels by the author (human judgement; possible author bias)."),
        ("Colours", "GREY = filled by script, do not edit.  YELLOW = you fill.  GREEN row = reuse: identical text to "
                    "an earlier row; it shows that row's label automatically - you may skip it."),
        ("How to fill", "Pick a score from the dropdown, write a ONE-LINE reason. Do not rename sheets or columns. "
                        "Sorting/filtering rows is fine (row_id is stable). Save as .xlsx."),
        ("PDF navigation", "pdf_page = page number to type in your PDF viewer (physical page). book_page = number "
                           "printed on that page, to confirm you are on the right page."),
        ("", ""),
        ("1_Coherence", "Does THIS chunk make sense on its own?"),
        ("  Score 0", "Cut mid-idea or missing its subject; cannot be understood alone. "
                      "Ex: '...conflict theorists will study the e' / starts mid-sentence."),
        ("  Score 1", "Partly understandable but missing needed context. Ex: starts 'This theory argues...' "
                      "without naming the theory."),
        ("  Score 2", "Understandable alone, the idea is complete. Ex: 'Functionalism / In sociology, the "
                      "functionalist perspective examines...'"),
        ("  Anchor rule", anchor_rule),
        ("  Tip", "Judge the chunk, not whether it answers a question. The anchor only tells you WHICH chunk."),
        ("", ""),
        ("2_Grounding_dev", f"Tiebreak for {', '.join(tied)} (dev questions). Read the 3 retrieved chunks TOGETHER."),
        ("  Score 0", "No evidence for the expected answer anywhere in the top-3."),
        ("  Score 1", "Evidence present, but a needed condition/context is missing (e.g. a number without its "
                      "year/unit, a definition without the exception the question asks about)."),
        ("  Score 2", "Enough to answer correctly AND trace it to the source page."),
        ("  Note", "Grounding judges retrieved EVIDENCE, not an LLM answer. hit@3 (auto) can disagree with "
                   "your score - that is expected; explain it in the reason."),
        ("", ""),
        ("Selection rule", "Fixed before any run: dev Evidence Hit@3, then dev Hit@1, then manual grounding "
                           "(this sheet), then fewer chunks / lower cost."),
        ("When done", "Save the file and tell Claude 'audit round 1 done'. Empty cells are reported, never guessed."),
    ]
    for a, b in rows:
        ws.append([a, b])
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = WRAP
        if row[0].value and not str(row[0].value).startswith("  "):
            row[0].font = Font(bold=True)
    ws["A1"].font = Font(bold=True, size=14)


def _progress(wb: Workbook, sheets: list[tuple[str, str, str, list[str]]]) -> None:
    """sheets: (sheet name, strategy column letter, score column letter, strategies)."""
    ws = wb.create_sheet("9_Progress")
    ws.append(["sheet", "strategy", "rows", "labelled", "remaining"])
    for cell in ws[1]:
        cell.fill, cell.font = HEAD, Font(bold=True, color="FFFFFF")
    for name, s_col, score_col, strategies in sheets:
        for s in strategies:
            r = ws.max_row + 1
            rng_s = f"'{name}'!{s_col}3:{s_col}2000"
            rng_v = f"'{name}'!{score_col}3:{score_col}2000"
            ws.append([name, s, f'=COUNTIF({rng_s},"{s}")',
                       f'=SUMPRODUCT(({rng_s}="{s}")*(LEN({rng_v})>0))', f"=C{r}-D{r}"])
    for col, w in zip("ABCDE", (20, 10, 8, 10, 10)):
        ws.column_dimensions[col].width = w


# ------------------------------------------------------------------ round 1
def build_round1(path: Path, pages: list[dict], chunks_by_strategy: dict[str, list[dict]],
                 strategy_order: list[str], dev_traces: list[dict], tied: list[str]) -> dict:
    wb = Workbook()
    anchor_rule = ("Content pages sorted by page; pages at positions round(i*N/10), i=0..9 (N=85 content "
                   "pages). Anchor = first sentence (>=60 chars) of the first paragraph >=150 chars on that page "
                   "(captions/objectives skipped). Chosen mechanically, not by looking at scores.")
    _guide(wb, 1, anchor_rule, tied)

    # ---- coherence
    anchors = choose_anchors(pages)
    ws = wb.create_sheet("1_Coherence")
    cols = [("row_id", 9, "auto", "Stable id."), ("anchor_id", 9, "auto", "Same anchor for every strategy."),
            ("strategy", 8, "auto", "Strategy letter."), ("pdf_page", 8, "auto", "Type this in your PDF viewer."),
            ("book_page", 8, "auto", "Printed page number."), ("section", 8, "auto", ""),
            ("anchor_sentence", 40, "auto", "Only used to find the chunk."),
            ("how_found", 18, "auto", "Whether the anchor sentence is whole or cut in this chunk."),
            ("chunk_id", 22, "auto", ""), ("chunk_chars", 7, "auto", ""),
            ("chunk_text", 90, "auto", "Read this."),
            ("reuse_of", 10, "auto", "If set: identical text to that row; its label is shown automatically."),
            ("score", 8, "input", COHERENCE_RUBRIC), ("reason", 45, "input", "One line: why this score.")]
    _header(ws, cols, "COHERENCE - does the chunk make sense ON ITS OWN?\n" + COHERENCE_RUBRIC +
            "\nGreen rows reuse an identical chunk: skip them.")
    seen: dict[tuple[str, str], int] = {}
    n = 0
    for a in anchors:
        for s in strategy_order:
            chunk, how = locate(a, chunks_by_strategy[s])
            n += 1
            excel_row = ws.max_row + 1
            text = chunk["content"] if chunk else ""
            key = (a["anchor_id"], text)
            reuse = seen.get(key) if chunk else None
            reuse_id = f"coh{reuse - 2:03d}" if reuse else None
            ws.append([f"coh{n:03d}", a["anchor_id"], s, a["page"], a["printed_page"], a["section"],
                       a["anchor_sentence"], how, chunk["chunk_id"] if chunk else "", len(text), text, reuse_id,
                       f'=IF(M{reuse}="","",M{reuse})' if reuse else None,
                       f'=IF(N{reuse}="","",N{reuse})' if reuse else None])
            if chunk and key not in seen:
                seen[key] = excel_row
    _style_rows(ws, cols, "reuse_of")
    _validate(ws, "M", "0,1,2", "0 cut / 1 partial / 2 complete")

    # ---- dev grounding for tied strategies
    ws2 = wb.create_sheet("2_Grounding_dev")
    gcols = [("row_id", 9, "auto", ""), ("qid", 6, "auto", ""), ("strategy", 8, "auto", ""),
             ("question", 35, "auto", ""), ("expected_answer", 28, "auto", ""),
             ("gold_page", 8, "auto", "PDF page of the gold evidence."), ("evidence_quote", 40, "auto", ""),
             ("filter_applied", 10, "auto", "Filter used for this strategy, if any."),
             ("auto_hit@1", 7, "auto", "Script: rank-1 chunk on gold page AND contains full quote."),
             ("auto_hit@3", 7, "auto", "Script: any of top-3."),
             ("chunk_1", 60, "auto", "rank 1: [page/section/score] + text"), ("chunk_2", 60, "auto", "rank 2"),
             ("chunk_3", 60, "auto", "rank 3"),
             ("reuse_of", 10, "auto", "Identical top-3 text to that row; its label is shown automatically."),
             ("score", 8, "input", GROUNDING_RUBRIC), ("reason", 45, "input", "One line: why this score.")]
    _header(ws2, gcols, "GROUNDING (dev tiebreak) - do the 3 chunks TOGETHER give enough evidence?\n" +
            GROUNDING_RUBRIC + "\nGreen rows have the same top-3 text as an earlier row: skip them.")
    seen_g: dict[tuple, int] = {}
    m = 0
    dev_ids = sorted({t["id"] for t in dev_traces})
    for qid in dev_ids:
        for s in tied:
            t = next(t for t in dev_traces if t["id"] == qid and t["strategy"] == s)
            m += 1
            excel_row = ws2.max_row + 1
            cells = [f"[p{h['page']}{'-' + str(h['page_end']) if h['page_end'] != h['page'] else ''} "
                     f"| {h['section']} | score {h['score']:.3f}]\n{h['text']}" for h in t["hits"]]
            cells += [""] * (3 - len(cells))
            key = (qid, tuple(h["text"] for h in t["hits"]))
            reuse = seen_g.get(key)
            ws2.append([f"grd{m:03d}", qid, s, t["question"], t["expected_answer"],
                        str(t["gold"][0]["page"]), t["gold"][0]["evidence_quote"],
                        json.dumps(t["filter_applied"]) if t["filter_applied"] else "",
                        int(t["hit_at_1"]), int(t["hit_at_3"]), *cells,
                        f"grd{reuse - 2:03d}" if reuse else None,
                        f'=IF(O{reuse}="","",O{reuse})' if reuse else None,
                        f'=IF(P{reuse}="","",P{reuse})' if reuse else None])
            seen_g.setdefault(key, excel_row)
    _style_rows(ws2, gcols, "reuse_of")
    _validate(ws2, "O", "0,1,2", "0 none / 1 partial / 2 enough")

    _progress(wb, [("1_Coherence", "C", "M", strategy_order), ("2_Grounding_dev", "C", "O", tied)])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    reuse_c = sum(1 for r in ws.iter_rows(min_row=3, values_only=True) if r[11])
    reuse_g = sum(1 for r in ws2.iter_rows(min_row=3, values_only=True) if r[13])
    return {"anchors": anchors, "coherence_rows": n, "coherence_reuse": reuse_c,
            "grounding_rows": m, "grounding_reuse": reuse_g, "anchor_rule": anchor_rule}
