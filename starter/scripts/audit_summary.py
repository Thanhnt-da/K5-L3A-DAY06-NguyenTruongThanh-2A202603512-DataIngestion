"""Aggregate both audit workbooks into report tables + a plain CSV of every label with its source.

Reuse rows are resolved through `reuse_of` (not Excel formulas). Empty labels are reported, never guessed.
Outputs: results/audit/audit_summary.json, results/audit/audit_labels.csv
"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

STARTER = Path(__file__).resolve().parents[1]
AUDIT = STARTER / "results" / "audit"
ORDER = list("ABCDEFZ")


def sheet(wb, name):
    ws = wb[name]
    header = [c.value for c in ws[2]]
    rows = [dict(zip(header, r)) for r in ws.iter_rows(min_row=3, values_only=True)]
    by_id = {r["row_id"]: r for r in rows}
    return rows, by_id


def resolved(row, by_id, field):
    src = by_id[row["reuse_of"]] if row.get("reuse_of") else row
    return src.get(field), src.get("labeled_by") or "human"


def score_table(rows, by_id, field="score"):
    table, missing, zeros = {}, [], []
    per = defaultdict(list)
    for r in rows:
        v, _ = resolved(r, by_id, field)
        if v in (None, ""):
            missing.append(r["row_id"])
            continue
        per[r["strategy"]].append(int(v))
        if int(v) == 0:
            zeros.append({"row_id": r["row_id"], "strategy": r["strategy"],
                          "reason": resolved(r, by_id, "reason")[0]})
    for s in [s for s in ORDER if s in per]:
        c = Counter(per[s])
        table[s] = {"n": len(per[s]), "mean": round(sum(per[s]) / len(per[s]), 2),
                    "count_0": c[0], "count_1": c[1], "count_2": c[2]}
    return table, missing, zeros


def main() -> None:
    out, labels = {}, []
    wb1 = load_workbook(AUDIT / "audit_workbook_round1.xlsx")
    coh, coh_id = sheet(wb1, "1_Coherence")
    out["coherence"], m1, z1 = score_table(coh, coh_id)
    out["coherence_zero_examples"] = z1
    gd, gd_id = sheet(wb1, "2_Grounding_dev")
    out["grounding_dev_tiebreak"], m2, _ = score_table(gd, gd_id)
    for r in coh:
        labels.append(["coherence", r["row_id"], r["strategy"], r["anchor_id"], resolved(r, coh_id, "score")[0],
                       resolved(r, coh_id, "reason")[0], "human" if not r["reuse_of"] else f"human (via {r['reuse_of']})"])
    for r in gd:
        labels.append(["grounding_dev", r["row_id"], r["strategy"], r["qid"], resolved(r, gd_id, "score")[0],
                       resolved(r, gd_id, "reason")[0], "human" if not r["reuse_of"] else f"human (via {r['reuse_of']})"])

    wb2 = load_workbook(AUDIT / "audit_workbook_round2.xlsx")
    met, met_id = sheet(wb2, "1_Metadata")
    meta = defaultdict(lambda: {"records": 0, "all_correct": 0, "page_ok": 0, "chapter_ok": 0, "section_ok": 0})
    errors, m3 = [], []
    for r in met:
        vals = {k: resolved(r, met_id, k)[0] for k in ("page_ok", "chapter_ok", "section_ok")}
        if any(v in (None, "") for v in vals.values()):
            m3.append(r["row_id"])
            continue
        m = meta[r["strategy"]]
        m["records"] += 1
        for k, v in vals.items():
            m[k] += v == "✅"
        m["all_correct"] += all(v == "✅" for v in vals.values())
        if not all(v == "✅" for v in vals.values()):
            errors.append({"row_id": r["row_id"], "qid": r["qid"], "strategy": r["strategy"],
                           "pages": f"{r['pdf_page']}-{r['page_end']}", **vals, "notes": resolved(r, met_id, "notes")[0]})
        labels.append(["metadata", r["row_id"], r["strategy"], r["qid"], json.dumps(vals, ensure_ascii=False),
                       resolved(r, met_id, "notes")[0], resolved(r, met_id, "labeled_by")[1]])
    out["metadata_test"] = {s: {**meta[s], "all_correct_str": f"{meta[s]['all_correct']}/{meta[s]['records']}"}
                            for s in ORDER if s in meta}
    out["metadata_errors"] = errors
    filt = defaultdict(lambda: [0, 0])
    for r in met:
        if r.get("auto_in_filter"):
            filt[(r["strategy"], r["qid"])][0] += 1
            filt[(r["strategy"], r["qid"])][1] += r["auto_in_filter"] == "✅"
    out["filter_compliance_test_top3"] = {f"{s}/{q}": f"{ok}/{n} chunks in filter" for (s, q), (n, ok) in sorted(filt.items())}

    gt, gt_id = sheet(wb2, "2_Grounding_test")
    out["grounding_test"], m4, z4 = score_table(gt, gt_id)
    out["grounding_test_zero_examples"] = z4
    for r in gt:
        labels.append(["grounding_test", r["row_id"], r["strategy"], r["qid"], resolved(r, gt_id, "score")[0],
                       resolved(r, gt_id, "reason")[0], resolved(r, gt_id, "labeled_by")[1]])
    out["provenance"] = {
        "coherence": "human (author)", "grounding_dev": "human (author)",
        "metadata_test": "page_ok human (66) + script (1); chapter_ok/section_ok script-verified with pypdf",
        "grounding_test": "Claude LLM judge (results/audit/claude_grounding_labels.json), author to review"}
    out["missing_labels"] = {"coherence": m1, "grounding_dev": m2, "metadata": m3, "grounding_test": m4}

    (AUDIT / "audit_summary.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    with (AUDIT / "audit_labels.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet", "row_id", "strategy", "item", "label", "reason_or_notes", "labeled_by"])
        w.writerows(labels)
    print(json.dumps({k: out[k] for k in ("coherence", "grounding_dev_tiebreak", "metadata_test", "grounding_test",
                                          "missing_labels")}, indent=1, ensure_ascii=False))
    print("metadata errors:", json.dumps(errors, ensure_ascii=False))
    print("filter:", out["filter_compliance_test_top3"])


if __name__ == "__main__":
    main()
