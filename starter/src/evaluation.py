"""Automatic retrieval scoring (Hit@1, Evidence Hit@3) with full per-query traces.

A retrieved chunk is a *hit* when it comes from a gold (source, page) AND contains
the whole evidence quote. Matching ignores case, punctuation and whitespace, so
curly vs straight quotes or line breaks do not matter -- but every word of the
quote must be present in order. Manual labels (coherence, grounding) are NOT
inferred here; they are done by a person.
"""
from __future__ import annotations

import re
from typing import Any


def match_key(text: str) -> str:
    """Lowercase letters/digits only, single-spaced: the form used to compare evidence."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def gold_options(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Main gold plus any documented alternatives: [{source_id, page, evidence_quote}]."""
    main = {"source_id": question["gold_source_id"], "page": question["gold_page"],
            "evidence_quote": question["evidence_quote"]}
    return [main, *question.get("alternative_gold", [])]


def _pages(value: Any) -> set[int]:
    return {int(v) for v in value} if isinstance(value, list) else {int(value)}


def judge_hit(question: dict[str, Any], hit: dict[str, Any]) -> dict[str, Any]:
    """Page match = a gold page lies inside the chunk's page range [page, page_end].

    For page-by-page strategies page_end == page, so this is the plain single-page rule;
    for strategy Z a chunk spanning 287-288 matches gold page 287 or 288.
    """
    chunk_key = match_key(hit["content"])
    start = int(hit["page"])
    end = int(hit.get("page_end") or start)
    page_match = evidence = False
    for gold in gold_options(question):
        same_page = (hit["document_id"] == gold["source_id"]
                     and any(start <= p <= end for p in _pages(gold["page"])))
        page_match |= same_page
        if same_page and match_key(gold["evidence_quote"]) in chunk_key:
            evidence = True
    return {"gold_page_match": page_match, "evidence_hit": evidence}


def filter_ok(question: dict[str, Any], hit: dict[str, Any]) -> bool | None:
    filters = question.get("filter") or {}
    if not filters:
        return None
    return all(str(hit.get(k)) == str(v) for k, v in filters.items())


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]],
                   strategy: str, filter_applied: dict[str, Any] | None) -> dict[str, Any]:
    ranked = []
    for rank, hit in enumerate(hits, start=1):
        extra = {k: (float(hit[k]) if k == "fused_score" else hit[k])
                 for k in ("fused_score", "dense_rank", "keyword_rank") if k in hit}
        ranked.append({"rank": rank, "chunk_id": hit["chunk_id"], "score": round(float(hit["score"]), 6),
                       **extra, "document_id": hit["document_id"], "source": hit["source"],
                       "page": hit["page"], "page_end": hit.get("page_end") or hit["page"],
                       "printed_page": hit["printed_page"], "chapter": hit["chapter"],
                       "section": hit["section"], "section_title": hit["section_title"],
                       "text": hit["content"], **judge_hit(question, hit),
                       "matches_question_filter": filter_ok(question, hit)})
    has_filter = bool(question.get("filter"))
    return {
        "strategy": strategy, "id": question["id"], "split": question["split"],
        "type": question.get("type"), "question": question["question"],
        "expected_answer": question["expected_answer"], "gold": gold_options(question),
        "question_filter": question.get("filter"), "filter_applied": filter_applied,
        "hit_at_1": bool(ranked and ranked[0]["evidence_hit"]),
        "hit_at_3": any(r["evidence_hit"] for r in ranked[:3]),
        "page_hit_at_3": any(r["gold_page_match"] for r in ranked[:3]),
        "top3_filter_compliant": (all(r["matches_question_filter"] for r in ranked[:3]) if has_filter else None),
        "hits": ranked,
    }


def summarize(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (strategy, split) with counts, so results are reported as n/N, not only %."""
    rows = []
    keys = sorted({(t["strategy"], t["split"]) for t in traces}, key=lambda k: (k[1], k[0]))
    for strategy, split in keys:
        group = [t for t in traces if t["strategy"] == strategy and t["split"] == split]
        filtered = [t for t in group if t["question_filter"]]
        n = len(group)
        row = {"strategy": strategy, "split": split, "n": n,
               "hit_at_1": sum(t["hit_at_1"] for t in group),
               "hit_at_3": sum(t["hit_at_3"] for t in group),
               "page_hit_at_3": sum(t["page_hit_at_3"] for t in group),
               "filter_questions": len(filtered),
               "filter_compliant": sum(bool(t["top3_filter_compliant"]) for t in filtered)}
        row["hit_at_1_str"] = f"{row['hit_at_1']}/{n}"
        row["hit_at_3_str"] = f"{row['hit_at_3']}/{n}"
        row["filter_compliance_str"] = (f"{row['filter_compliant']}/{len(filtered)}" if filtered else "N/A")
        rows.append(row)
    return rows
