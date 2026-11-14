"""Day 06 pipeline entry point.

Run from the starter/ folder:
    python -m src.run ingest                      # read PDF, chunk, embed, store all strategies (safe to re-run)
    python -m src.run check-benchmark             # validate questions + evidence quotes (no API calls)
    python -m src.run tune-hybrid                 # choose strategy F's vector/keyword weight on DEV only
    python -m src.run benchmark --split dev       # top-3 retrieval for dev questions, all strategies
    python -m src.run benchmark --split probe     # optional cross-page probe questions (reported apart)
    python -m src.run benchmark --split test --chosen B   # only after choosing on dev
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv

from .chunking import by_paragraph, by_section, fixed_size
from .evaluation import evaluate_query, match_key, summarize
from .ingestion import END_MATTER, MIN_CONTENT_CHARS, load_pdf
from .models import Chunk, DocumentPage

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
RESULTS = STARTER / "results"
MANIFEST = ROOT / "data" / "manifest.json"
QUESTIONS = ROOT / "benchmark" / "questions.jsonl"

DOCUMENT_ID = "openstax-sociology-3e"
PDF_REL = "data/Introductory Sociology 3e.pdf"
FIRST_PAGE, LAST_PAGE = 269, 410            # chapters 9-12 (physical PDF pages)
MIN_PAGES = 30
PRICE_PER_1M_TOKENS = 0.02                   # USD, text-embedding-3-small; verify on openai.com/pricing

HYBRID_TUNING = RESULTS / "hybrid_tuning.json"
HYBRID_WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]   # vector weight grid tried on dev (1.0 = dense only)
DEFAULT_VECTOR_WEIGHT = 0.5

# Each strategy differs from its comparison partner in ONE thing (see "compare").
STRATEGIES = {
    "A": {"chunking": "fixed", "chunk_size": 500, "overlap": 50, "scope": "page", "search": "vector",
          "use_filter": False, "compare": "baseline"},
    "B": {"chunking": "paragraph", "chunk_size": 1000, "overlap": 0, "scope": "page", "search": "vector",
          "use_filter": False, "compare": "A (split rule + size)"},
    "C": {"chunking": "paragraph", "chunk_size": 1000, "overlap": 0, "scope": "page", "search": "vector",
          "use_filter": True, "compare": "B (metadata filter)"},
    "D": {"chunking": "paragraph", "chunk_size": 500, "overlap": 0, "scope": "page", "search": "vector",
          "use_filter": False, "compare": "A (split rule, same size) and B (size, same rule)"},
    "E": {"chunking": "fixed", "chunk_size": 500, "overlap": 150, "scope": "page", "search": "vector",
          "use_filter": False, "compare": "A (overlap 50 -> 150)"},
    "F": {"chunking": "paragraph", "chunk_size": 1000, "overlap": 0, "scope": "page", "search": "hybrid",
          "use_filter": False, "compare": "B (hybrid vector + keyword search)"},
    "Z": {"chunking": "paragraph", "chunk_size": 1000, "overlap": 0, "scope": "section", "search": "vector",
          "use_filter": False, "compare": "B (chunks may cross page breaks; page range metadata)"},
}


# ---------------------------------------------------------------- helpers
def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                sys.exit(f"{path.name} line {n}: invalid JSON ({exc})")
    return rows


def environment() -> dict:
    libs = {}
    for lib in ("openai", "psycopg", "pymupdf", "python-dotenv"):
        try:
            libs[lib] = version(lib)
        except Exception:
            libs[lib] = "unknown"
    return {"python": platform.python_version(), "libraries": libs,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def chunk_pages(pages: list[DocumentPage], cfg: dict) -> tuple[list[Chunk], dict]:
    if cfg["scope"] == "section":
        chunks, joins = by_section(pages, cfg["chunk_size"])
        return chunks, {"cross_page_paragraph_joins": joins}
    if cfg["chunking"] == "fixed":
        return [c for p in pages for c in fixed_size(p, cfg["chunk_size"], cfg["overlap"])], {}
    return [c for p in pages for c in by_paragraph(p, cfg["chunk_size"])], {}


def length_stats(chunks: list[Chunk]) -> dict:
    lengths = sorted(len(c.text) for c in chunks)
    q = statistics.quantiles(lengths, n=10)
    spans = [c.metadata.get("page_end", c.page) - c.page + 1 for c in chunks]
    return {"chunks": len(chunks), "total_chars": sum(lengths), "min": lengths[0], "p10": round(q[0]),
            "median": statistics.median(lengths), "mean": round(statistics.mean(lengths), 1),
            "p90": round(q[-1]), "max": lengths[-1],
            "mean_pages_per_chunk": round(statistics.mean(spans), 3),
            "multi_page_chunks": sum(s > 1 for s in spans)}


def vector_weight() -> tuple[float, str]:
    if HYBRID_TUNING.exists():
        return json.loads(HYBRID_TUNING.read_text(encoding="utf-8"))["chosen_vector_weight"], "tuned on dev"
    return DEFAULT_VECTOR_WEIGHT, "default (not tuned)"


def retrieve(store, name: str, cfg: dict, question: dict, qvec: list[float], weight: float):
    applied = (question.get("filter") or None) if cfg["use_filter"] else None
    if cfg["search"] == "hybrid":
        hits = store.hybrid_search(name, qvec, question["question"], 3, applied, vector_weight=weight)
    else:
        hits = store.search(name, qvec, 3, applied)
    return hits, applied


def open_backends():
    from .embeddings import OpenAIEmbedder
    from .store import PgVectorStore
    load_dotenv(STARTER / ".env")
    embedder = OpenAIEmbedder()
    store = PgVectorStore(os.getenv("DATABASE_URL", ""), embedder.dimensions)
    store.create_schema()
    return embedder, store


def embed_with_cache(embedder, store, texts: list[str]) -> tuple[list[list[float]], int]:
    """Return vectors for texts, calling the API only for texts not cached yet."""
    from .store import text_hash
    hashes = [text_hash(t) for t in texts]
    cached = store.cached_vectors(embedder.model_name, sorted(set(hashes)))
    missing = {h: t for h, t in zip(hashes, texts) if h not in cached}
    if missing:
        new = embedder.embed_documents(list(missing.values()))
        fresh = dict(zip(missing.keys(), new))
        store.cache_vectors(embedder.model_name, fresh)
        cached.update(fresh)
    return [cached[h] for h in hashes], len(missing)


def load_content_pages() -> tuple[list[DocumentPage], list[dict]]:
    pdf = ROOT / PDF_REL
    pages, log = load_pdf(pdf, DOCUMENT_ID, PDF_REL, FIRST_PAGE, LAST_PAGE)
    if len(pages) < MIN_PAGES:
        sys.exit(f"Only {len(pages)} content pages; the lab needs >= {MIN_PAGES}")
    return pages, log


def update_manifest(pages: list[DocumentPage], log: list[dict]) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    src = next(s for s in manifest["sources"] if s["source_id"] == DOCUMENT_ID)
    src["counting_rule"] = (
        f"Physical PDF pages {FIRST_PAGE}-{LAST_PAGE} (chapters 9-12) are read. A page counts as content "
        f"unless it is (a) a chapter title / table-of-contents page, (b) an end-of-chapter study aid "
        f"({', '.join(sorted(END_MATTER))}), or (c) has fewer than {MIN_CONTENT_CHARS} characters after "
        "removing the page footer, the 'Learning Objectives' sidebar label and the LibreTexts license line. "
        "Character counts are of the normalized text (NFKC, whitespace collapsed).")
    src["counted_pages_total"] = len(pages)
    src["counted_pages"] = [{"page": p.page, "printed_page": p.metadata["printed_page"],
                             "chapter": p.metadata["chapter"], "section": p.metadata["section"],
                             "chars": len(p.text)} for p in pages]
    src["excluded_pages"] = [{"page": e["page"], "reason": e["reason"]} for e in log if e["status"] == "excluded"]
    src["char_counts"] = {"total_counted_chars": sum(len(p.text) for p in pages),
                          "min_page_chars": min(len(p.text) for p in pages)}
    write_json(MANIFEST, manifest)


# ---------------------------------------------------------------- commands
def cmd_ingest(_args) -> None:
    t0 = time.perf_counter()
    pages, log = load_content_pages()
    write_jsonl(RESULTS / "pages.jsonl", [{"page": p.page, **p.metadata, "chars": len(p.text), "text": p.text}
                                          for p in pages])
    write_json(RESULTS / "ingestion_log.json", log)
    update_manifest(pages, log)
    print(f"Read pages {FIRST_PAGE}-{LAST_PAGE}: {len(pages)} content, "
          f"{sum(e['status'] == 'excluded' for e in log)} excluded ({time.perf_counter() - t0:.1f}s)")

    embedder, store = open_backends()
    report = {"embedding": {"provider": embedder.provider, "model": embedder.model_name,
                            "dimensions": embedder.dimensions, "query_document_handling":
                            "same encoding for queries and documents (no prefixes)",
                            "price_assumption_usd_per_1M_tokens": PRICE_PER_1M_TOKENS},
              "vector_store": {"backend": "PostgreSQL + pgvector", "table": "chunks", "metric": "cosine (<=>)",
                               "search": "exact (no ANN index)"},
              "environment": environment(), "strategies": {}}
    for name, cfg in STRATEGIES.items():
        t1 = time.perf_counter()
        tokens_before = embedder.tokens_used
        chunks, chunk_info = chunk_pages(pages, cfg)
        vectors, newly_embedded = embed_with_cache(embedder, store, [c.text for c in chunks])
        result = store.upsert(name, chunks, vectors)
        tokens = embedder.tokens_used - tokens_before
        report["strategies"][name] = {**cfg, **length_stats(chunks), **chunk_info, **result,
                                      "texts_newly_embedded": newly_embedded, "api_tokens": tokens,
                                      "estimated_cost_usd": round(tokens / 1e6 * PRICE_PER_1M_TOKENS, 6),
                                      "seconds": round(time.perf_counter() - t1, 1)}
        print(f"  {name}: {len(chunks)} chunks | new embeddings {newly_embedded} | {tokens} tokens | "
              f"rows {result['rows_before']} -> {result['rows_after']} | "
              f"obsolete deleted {result['obsolete_deleted']}")
    report["total_api_tokens"] = embedder.tokens_used
    report["total_estimated_cost_usd"] = round(embedder.tokens_used / 1e6 * PRICE_PER_1M_TOKENS, 6)
    report["total_seconds"] = round(time.perf_counter() - t0, 1)
    write_json(RESULTS / "ingest_report.json", report)
    print(f"Done in {report['total_seconds']}s; {embedder.tokens_used} API tokens "
          f"(~${report['total_estimated_cost_usd']}). Report: results/ingest_report.json")


REQUIRED = ["id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote"]


def check_questions(questions: list[dict]) -> list[str]:
    """Return a list of problems; empty means the benchmark is valid."""
    problems = []
    page_rows = {r["page"]: r for r in read_jsonl(RESULTS / "pages.jsonl")}
    ids = [q.get("id") for q in questions]
    if len(set(ids)) != len(ids):
        problems.append("duplicate question ids")
    for q in questions:
        qid = q.get("id", "?")
        missing = [k for k in REQUIRED if not q.get(k)]
        if missing:
            problems.append(f"{qid}: missing {missing}")
            continue
        if q["split"] not in ("dev", "test", "probe"):
            problems.append(f"{qid}: split must be dev, test or probe")
        golds = [{"source_id": q["gold_source_id"], "page": q["gold_page"], "evidence_quote": q["evidence_quote"]},
                 *q.get("alternative_gold", [])]
        for g in golds:
            pages = g["page"] if isinstance(g["page"], list) else [g["page"]]
            if g["source_id"] != DOCUMENT_ID:
                problems.append(f"{qid}: unknown source_id {g['source_id']!r}")
            quote = match_key(g["evidence_quote"])
            found = any(p in page_rows and quote in match_key(page_rows[p]["text"]) for p in pages)
            # A quote that runs across a page break (probe questions) is found in the pages joined in order.
            if not found and len(pages) > 1 and all(p in page_rows for p in pages):
                found = quote in match_key(" ".join(page_rows[p]["text"] for p in sorted(pages)))
            if not found:
                where = ", ".join(str(p) + ("" if p in page_rows else " (not a content page)") for p in pages)
                problems.append(f"{qid}: evidence quote not found on page {where}")
        main_page = q["gold_page"][0] if isinstance(q["gold_page"], list) else q["gold_page"]
        for key, value in (q.get("filter") or {}).items():
            values = {str(r.get(key)) for r in page_rows.values()}
            if str(value) not in values:
                problems.append(f"{qid}: filter {key}={value!r} matches no page")
            elif str(page_rows.get(main_page, {}).get(key)) != str(value):
                problems.append(f"{qid}: gold page is not inside filter {key}={value!r}")
    questions = [q for q in questions if q.get("split") != "probe"]   # probes don't count toward the minimums
    dev = sum(q.get("split") == "dev" for q in questions)
    test = sum(q.get("split") == "test" for q in questions)
    filt = [q for q in questions if q.get("filter")]
    if len(questions) < 20: problems.append(f"need >= 20 questions, have {len(questions)}")
    if dev < 15: problems.append(f"need >= 15 dev questions, have {dev}")
    if test < 5: problems.append(f"need >= 5 test questions, have {test}")
    if len(filt) < 3: problems.append(f"need >= 3 filter questions, have {len(filt)}")
    if not any(q.get("split") == "test" for q in filt): problems.append("need >= 1 filter question in test")
    return problems


def cmd_check_benchmark(_args) -> None:
    if not (RESULTS / "pages.jsonl").exists():
        sys.exit("Run `python -m src.run ingest` first (it writes results/pages.jsonl).")
    if not QUESTIONS.exists():
        sys.exit(f"Missing {QUESTIONS.relative_to(ROOT)} -- see benchmark/README.md for the format.")
    questions = read_jsonl(QUESTIONS)
    problems = check_questions(questions)
    write_json(RESULTS / "benchmark_check.json", {"questions": len(questions), "problems": problems,
                                                  "checked_at": environment()["run_at"]})
    if problems:
        print("Benchmark problems:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    print(f"Benchmark OK: {len(questions)} questions, every evidence quote found on its gold page.")


def cmd_benchmark(args) -> None:
    questions = read_jsonl(QUESTIONS)
    problems = check_questions(questions)
    if problems:
        sys.exit("Fix the benchmark first (python -m src.run check-benchmark).")
    if args.split == "test" and not args.chosen:
        sys.exit("Choose the strategy on dev first, then pass it: --split test --chosen <A-F|Z>")
    selected = [q for q in questions if q["split"] == args.split]
    if not selected:
        sys.exit(f"No questions with split={args.split!r}")
    embedder, store = open_backends()
    weight, weight_source = vector_weight()
    traces = []
    query_vectors = dict(zip([q["id"] for q in selected],
                             embed_with_cache(embedder, store, [q["question"] for q in selected])[0]))
    for name, cfg in STRATEGIES.items():
        if store.count(name) == 0:
            sys.exit(f"Strategy {name} has no stored chunks; run ingest first.")
        for q in selected:
            hits, applied = retrieve(store, name, cfg, q, query_vectors[q["id"]], weight)
            traces.append(evaluate_query(q, hits, name, applied))
    summary = summarize(traces)
    write_jsonl(RESULTS / f"traces_{args.split}.jsonl", traces)
    info = {"split": args.split, "questions": len(selected), "model": embedder.model_name,
            "chosen_before_test": args.chosen, "api_tokens": embedder.tokens_used,
            "hybrid_vector_weight": weight, "hybrid_weight_source": weight_source,
            "strategies": STRATEGIES, "summary": summary, **environment()}
    write_json(RESULTS / f"summary_{args.split}.json", info)
    with (RESULTS / f"summary_{args.split}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    print(f"{args.split}: {len(selected)} questions x {len(STRATEGIES)} strategies")
    for row in summary:
        print(f"  {row['strategy']}: Hit@1 {row['hit_at_1_str']}  Hit@3 {row['hit_at_3_str']}  "
              f"filter compliance {row['filter_compliance_str']}")
    print(f"Traces: results/traces_{args.split}.jsonl")


def cmd_tune_hybrid(_args) -> None:
    """Pick strategy F's vector weight on DEV questions only (never on test)."""
    questions = read_jsonl(QUESTIONS)
    if check_questions(questions):
        sys.exit("Fix the benchmark first (python -m src.run check-benchmark).")
    dev = [q for q in questions if q["split"] == "dev"]
    embedder, store = open_backends()
    vectors = embed_with_cache(embedder, store, [q["question"] for q in dev])[0]
    cfg = STRATEGIES["F"]
    grid = []
    for w in HYBRID_WEIGHTS:
        traces = [evaluate_query(q, retrieve(store, "F", cfg, q, v, w)[0], "F", None) for q, v in zip(dev, vectors)]
        grid.append({"vector_weight": w, "dev_hit_at_3": sum(t["hit_at_3"] for t in traces),
                     "dev_hit_at_1": sum(t["hit_at_1"] for t in traces), "n": len(dev)})
    # Rule (fixed before looking): best Hit@3, then Hit@1, then the weight closest to 0.5.
    best = max(grid, key=lambda g: (g["dev_hit_at_3"], g["dev_hit_at_1"], -abs(g["vector_weight"] - 0.5)))
    write_json(HYBRID_TUNING, {"split_used": "dev", "rule": "max Hit@3, then Hit@1, then closest to 0.5",
                               "grid": grid, "chosen_vector_weight": best["vector_weight"],
                               "note": "F's dev score is optimistic because the weight was chosen on dev",
                               **environment()})
    for g in grid:
        mark = "  <- chosen" if g is best else ""
        print(f"  w={g['vector_weight']:.2f}: Hit@1 {g['dev_hit_at_1']}/{g['n']}  Hit@3 {g['dev_hit_at_3']}/{g['n']}{mark}")


def cmd_audit_sheets(args) -> None:
    from .audit import build_round1, build_round2
    from .store import PgVectorStore
    load_dotenv(STARTER / ".env")
    if args.round == 2:
        summary = RESULTS / "summary_test.json"
        if not summary.exists():
            sys.exit("Run the test benchmark first: benchmark --split test --chosen <X>")
        chosen = json.loads(summary.read_text(encoding="utf-8"))["chosen_before_test"]
        path = RESULTS / "audit" / "audit_workbook_round2.xlsx"
        if path.exists() and not args.overwrite:
            sys.exit(f"{path.name} already exists (it may hold your labels). Use --overwrite to replace it.")
        info = build_round2(path, read_jsonl(RESULTS / "traces_test.jsonl"), list(STRATEGIES), chosen)
        print(f"Wrote {path.relative_to(STARTER)}")
        print(f"  metadata: {info['metadata_rows']} rows ({info['metadata_reuse']} reuse rows to skip)")
        print(f"  test grounding: {info['grounding_rows']} rows ({info['grounding_reuse']} reuse rows)")
        return
    if args.round != 1:
        sys.exit("--round must be 1 or 2")
    tied = args.tied.split(",")
    traces = read_jsonl(RESULTS / "traces_dev.jsonl")
    pages = read_jsonl(RESULTS / "pages.jsonl")
    store = PgVectorStore(os.getenv("DATABASE_URL", ""), 1536)
    chunks = {s: store.all_chunks(s) for s in STRATEGIES}
    for rows in chunks.values():
        for r in rows:
            r["page_end"] = r["page_end"] or r["page"]
    path = RESULTS / "audit" / "audit_workbook_round1.xlsx"
    if path.exists() and not args.overwrite:
        sys.exit(f"{path.name} already exists (it may hold your labels). Use --overwrite to replace it.")
    info = build_round1(path, pages, chunks, list(STRATEGIES), traces, tied)
    write_json(RESULTS / "audit" / "coherence_anchors.json", {"rule": info["anchor_rule"], "anchors": info["anchors"]})
    print(f"Wrote {path.relative_to(STARTER)}")
    print(f"  coherence: {info['coherence_rows']} rows ({info['coherence_reuse']} reuse rows to skip)")
    print(f"  dev grounding ({', '.join(tied)}): {info['grounding_rows']} rows ({info['grounding_reuse']} reuse rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 06 ingestion + retrieval benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)
    sub.add_parser("check-benchmark").set_defaults(func=cmd_check_benchmark)
    sub.add_parser("tune-hybrid").set_defaults(func=cmd_tune_hybrid)
    a = sub.add_parser("audit-sheets")
    a.add_argument("--round", type=int, default=1)
    a.add_argument("--tied", default="C,F,Z", help="strategies for the dev grounding tiebreak")
    a.add_argument("--overwrite", action="store_true")
    a.set_defaults(func=cmd_audit_sheets)
    b = sub.add_parser("benchmark")
    b.add_argument("--split", choices=["dev", "test", "probe"], required=True)
    b.add_argument("--chosen", choices=list(STRATEGIES), help="strategy chosen on dev (required for test)")
    b.set_defaults(func=cmd_benchmark)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
