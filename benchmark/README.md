# Benchmark

Write your questions in `benchmark/questions.jsonl`: **one JSON object per line**.
Write all of them, and fix the dev/test split, **before** running `benchmark`.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | unique, e.g. `q01` |
| `split` | yes | `dev` or `test` (≥15 dev, ≥5 test), or `probe` for optional cross-page questions (reported separately, not counted) |
| `type` | no | `direct`, `paraphrase`, `condition`, `numeric`, `filter`… (for error analysis) |
| `question` | yes | in your own words, not copied from the book |
| `expected_answer` | yes | short correct answer |
| `gold_source_id` | yes | always `openstax-sociology-3e` |
| `gold_page` | yes | **physical PDF page** (1 = first page of the file), or a list like `[283, 284]` |
| `evidence_quote` | yes | exact sentence(s) from that page that prove the answer |
| `filter` | no | e.g. `{"chapter": 12}`; only for questions whose context gives the chapter (≥3 total, ≥1 in test) |
| `alternative_gold` | no | other valid places: `[{"source_id": "openstax-sociology-3e", "page": 304, "evidence_quote": "..."}]` |

Only content pages count as gold: chapters 9–12, excluding title pages and the end-of-chapter Key Terms / Summary / Quiz / Short Answer / Further Research / References pages (see `data/manifest.json` → `counted_pages`).

Evidence matching ignores case, punctuation and line breaks, but every word must be there in order, so copy the sentence rather than retyping it.

## Example line

```json
{"id": "q01", "split": "dev", "type": "direct", "question": "What is relative poverty?", "expected_answer": "Having an income 50% below the median income", "gold_source_id": "openstax-sociology-3e", "gold_page": 283, "evidence_quote": "Relative poverty is an economic condition in which a family or individuals have 50% income less than the average median income."}
```

With a filter:

```json
{"id": "q06", "split": "test", "type": "filter", "question": "(Context: chapter 12) How does conflict theory explain gender?", "expected_answer": "A struggle for dominance between groups such as men and women over scarce resources", "gold_source_id": "openstax-sociology-3e", "gold_page": 391, "evidence_quote": "society is a struggle for dominance among social groups (like women versus men) that compete for scarce resources.", "filter": {"chapter": 12}}
```

## Optional: cross-page probe questions (for strategy Z)

Write these **after** your 20 main questions. Pick evidence that starts at the bottom of one page and ends at the top of the next, use `"split": "probe"` and list both pages: `"gold_page": [282, 283]`. A–F can never fully contain such a quote (they chunk page by page); Z can. Example: page 282 ends "…A country with a high standard" and page 283 continues "of living will often reflect a high quality of life…".

## Check, then run

From `starter/`:

```powershell
python -m src.run check-benchmark            # verifies every quote is on its gold page; no API calls
python -m src.run tune-hybrid                # picks strategy F's weight using dev only
python -m src.run benchmark --split dev      # all strategies (A-F, Z) on dev only
python -m src.run benchmark --split probe    # optional cross-page probes
# choose the strategy from dev results, write the choice down, THEN:
python -m src.run benchmark --split test --chosen B
```
